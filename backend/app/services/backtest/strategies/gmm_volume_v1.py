"""GMM 成交量密度策略 — 基于高斯混合模型的量价分布交易信号"""

from __future__ import annotations

# Must be set BEFORE numpy/sklearn imports, otherwise BLAS internal
# threading spawns threads that cause fork() to deadlock in child processes.
import os as _os

_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
_os.environ.setdefault("OMP_NUM_THREADS", "1")

import multiprocessing
import numpy as np
import pandas as pd

from .base import BaseStrategy
from .contracts import BaseStrategyV2
from app.utils.data_processor import DataProcessor

# ------------------------------------------------------------------
# Shared-memory state for fork-based multiprocessing.
# The parent process loads all chart data here BEFORE spawning workers.
# With fork(), child processes inherit this memory via copy-on-write
# without any pickling. Workers receive only index ranges.
# ------------------------------------------------------------------
_SHARED_SYM_IDS: list[str] = []
_SHARED_CHART_DATA: list[list[dict]] = []
_SHARED_CLOSES: list[list[float]] = []
_SHARED_DATES: list[list[str]] = []


class GMMVolumeV1Strategy(BaseStrategy, BaseStrategyV2):
    strategy_id = "gmm_volume_v1"
    name = "GMM 成交量密度 v1"
    description = (
        "基于 GMM 多峰拟合的成交量密度策略。"
        "滚动窗口内对价格-成交量分布拟合高斯混合模型，"
        "密度低于下阈值（1-threshold）时买入，高于上阈值（threshold）时卖出。"
        "策略选股模式使用 fork 多进程并行，告别 Python GIL。"
    )

    @classmethod
    def param_schema(cls) -> list[dict]:
        return [
            {
                "key": "lookback_days",
                "label": "回看天数",
                "type": "int",
                "required": True,
                "default": 60,
                "min": 20,
                "max": 250,
            },
            {
                "key": "threshold",
                "label": "密度阈值",
                "type": "float",
                "required": True,
                "default": 0.7,
                "min": 0.5,
                "max": 0.95,
            },
            {
                "key": "max_components",
                "label": "最大高斯分量数",
                "type": "int",
                "required": True,
                "default": 5,
                "min": 2,
                "max": 8,
            },
            {
                "key": "refit_interval",
                "label": "重拟合间隔(天)",
                "type": "int",
                "required": True,
                "default": 5,
                "min": 1,
                "max": 20,
            },
            {
                "key": "position_size_pct",
                "label": "单笔仓位占比",
                "type": "float",
                "required": True,
                "default": 0.2,
                "min": 0.01,
                "max": 1.0,
            },
            {
                "key": "max_workers",
                "label": "并行进程数",
                "type": "int",
                "required": True,
                "default": 4,
                "min": 1,
                "max": 8,
            },
        ]

    @classmethod
    def required_columns(cls) -> set[str]:
        return {"datetime", "open", "close", "volume"}

    @classmethod
    def default_policy_profile(cls) -> str:
        return "vsd_v1_default"

    # ------------------------------------------------------------------
    # V1: manual_symbols mode — per-stock signal generation
    # ------------------------------------------------------------------

    def generate_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        lookback = int(params.get("lookback_days", 60))
        threshold = float(params.get("threshold", 0.7))
        max_comp = int(params.get("max_components", 5))
        refit_int = int(params.get("refit_interval", 5))

        work = df.copy()
        work["datetime"] = pd.to_datetime(work["datetime"])
        work = work.sort_values("datetime").reset_index(drop=True)
        work["buy_signal"] = False
        work["sell_signal"] = False

        if len(work) < lookback + 2:
            return work

        upper = threshold
        lower = 1.0 - threshold
        cached_fit = None

        for i in range(lookback, len(work)):
            window = work.iloc[i - lookback : i]
            if len(window) < max(10, lookback // 2):
                continue

            if cached_fit is None or i % refit_int == 0:
                chart_data = _build_chart_data(window)
                cached_fit = DataProcessor.fit_gaussian_mixture(
                    chart_data, max_components=max_comp
                )

            if cached_fit is None or "fit_curve" not in cached_fit:
                continue

            density = _compute_density(float(work.iloc[i]["close"]), cached_fit)
            if density is None:
                continue

            if density >= upper:
                work.at[i, "sell_signal"] = True
            elif density <= lower:
                work.at[i, "buy_signal"] = True

        return work

    # ------------------------------------------------------------------
    # V2: strategy_select mode — fork-based parallel scanning
    # ------------------------------------------------------------------

    def generate_candidates(
        self, market_df: pd.DataFrame, params: dict
    ) -> pd.DataFrame:
        cols = ["trade_date", "symbol", "signal_strength", "reason"]
        if market_df.empty:
            return pd.DataFrame(columns=cols)

        work = market_df.copy()
        work["trade_date"] = pd.to_datetime(work["datetime"])
        symbols = list(work["symbol"].dropna().unique())

        if not symbols:
            return pd.DataFrame(columns=cols)

        lookback = int(params.get("lookback_days", 60))
        threshold = float(params.get("threshold", 0.7))
        max_comp = int(params.get("max_components", 5))
        refit_int = int(params.get("refit_interval", 5))
        max_workers = int(params.get("max_workers", 4))

        # Pre-serialize all symbol data. This runs once in the parent process.
        global _SHARED_SYM_IDS, _SHARED_CHART_DATA, _SHARED_CLOSES, _SHARED_DATES
        _SHARED_SYM_IDS = []
        _SHARED_CHART_DATA = []
        _SHARED_CLOSES = []
        _SHARED_DATES = []

        for sym in symbols:
            sym_df = (
                work[work["symbol"] == sym]
                .sort_values("trade_date")
                .reset_index(drop=True)
            )
            if len(sym_df) < lookback + 2:
                continue
            _SHARED_SYM_IDS.append(str(sym).strip())
            _SHARED_CHART_DATA.append(_build_chart_data(sym_df))
            _SHARED_CLOSES.append([float(c["price"]) for c in _SHARED_CHART_DATA[-1]])
            _SHARED_DATES.append([c["datetime"] for c in _SHARED_CHART_DATA[-1]])

        total = len(_SHARED_SYM_IDS)
        if total == 0:
            return pd.DataFrame(columns=cols)

        # Single-process path for small work or max_workers=1
        n_workers = min(max_workers, total)
        if n_workers <= 1:
            result = _scan_range(0, total, lookback, threshold, max_comp, refit_int)
            return pd.DataFrame(result)

        # Multi-process via multiprocessing.Pool with fork.
        # Workers inherit shared data via copy-on-write — only index ranges
        # are passed as arguments, zero large-data pickling.
        # Must disable BLAS internal threading before fork, otherwise
        # sklearn's OpenBLAS threads cause child processes to deadlock.
        chunk_size = max(1, total // n_workers)
        ranges = []
        for start in range(0, total, chunk_size):
            end = min(start + chunk_size, total)
            ranges.append((start, end))

        all_candidates: list[dict] = []
        ctx = multiprocessing.get_context("fork")

        with ctx.Pool(
            processes=n_workers,
            initializer=_blas_single_thread,
        ) as pool:
            async_results = [
                pool.apply_async(
                    _scan_range,
                    (s, e, lookback, threshold, max_comp, refit_int),
                )
                for s, e in ranges
            ]
            for ar in async_results:
                try:
                    result = ar.get(timeout=600)
                    if result:
                        all_candidates.extend(result)
                except Exception:
                    continue

        return pd.DataFrame(all_candidates)


# ------------------------------------------------------------------
# Worker: receives index range only, reads data from fork-inherited globals
# ------------------------------------------------------------------


def _scan_range(
    start: int,
    end: int,
    lookback: int,
    threshold: float,
    max_comp: int,
    refit_int: int,
) -> list[dict]:
    """Scan symbols [start, end) using fork-inherited shared data. No pickling."""
    candidates: list[dict] = []
    lower = 1.0 - threshold

    for idx in range(start, end):
        sym = _SHARED_SYM_IDS[idx]
        chart_data = _SHARED_CHART_DATA[idx]
        closes = _SHARED_CLOSES[idx]
        dates = _SHARED_DATES[idx]
        n = len(chart_data)

        cached_fit = None
        for i in range(lookback, n):
            if cached_fit is None or i % refit_int == 0:
                window = chart_data[i - lookback : i]
                cached_fit = DataProcessor.fit_gaussian_mixture(
                    window, max_components=max_comp
                )

            if cached_fit is None or "fit_curve" not in cached_fit:
                continue

            density = _compute_density(closes[i], cached_fit)
            if density is None:
                continue

            if density <= lower:
                candidates.append(
                    {
                        "trade_date": dates[i],
                        "symbol": sym,
                        "signal_strength": round(1.0 - density, 4),
                        "reason": (
                            f"GMM密度 {(density * 100):.0f}% ≤ "
                            f"{(lower * 100):.0f}% (买入信号)"
                        ),
                    }
                )

    return candidates


def _blas_single_thread():
    """Pool initializer: force BLAS to single-thread to avoid fork deadlocks."""
    import os

    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _build_chart_data(df: pd.DataFrame) -> list[dict]:
    """将 OHLCV DataFrame 转为 DataProcessor.fit_gaussian_mixture 所需格式。"""
    rows = []
    for _, bar in df.iterrows():
        if "datetime" in df.columns:
            dt = bar["datetime"]
            dt_str = (
                dt.strftime("%Y-%m-%d %H:%M:%S") if hasattr(dt, "strftime") else str(dt)
            )
        else:
            dt_str = ""
        rows.append(
            {
                "datetime": dt_str,
                "price": float(bar["close"]),
                "volume": float(bar["volume"]),
                "open": float(bar.get("open", bar["close"])),
                "high": float(bar.get("high", bar["close"])),
                "low": float(bar.get("low", bar["close"])),
            }
        )
    return rows


def _compute_density(price: float, fit_result: dict) -> float | None:
    """计算当前价格在 GMM 拟合分布中的密度百分位。"""
    curve = fit_result.get("fit_curve")
    if not curve:
        return None
    prices = [p["price"] for p in curve]
    densities = [p["fitVolume"] for p in curve]
    max_den = max(densities)
    if max_den <= 0:
        return None
    cur_den = float(np.interp(price, prices, densities))
    return cur_den / max_den
