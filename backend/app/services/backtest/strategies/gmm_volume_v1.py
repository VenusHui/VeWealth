"""GMM 成交量密度策略 — 基于高斯混合模型的量价分布交易信号"""

from __future__ import annotations

# Force single-thread BLAS before numpy/sklearn load, otherwise internal
# OpenBLAS threads survive fork() and deadlock child processes.
import os as _os

_os.environ["OPENBLAS_NUM_THREADS"] = "1"
_os.environ["OMP_NUM_THREADS"] = "1"

import logging
import multiprocessing
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .base import BaseStrategy
from .contracts import BaseStrategyV2
from app.utils.data_processor import DataProcessor

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Per-job share state for fork-based multiprocessing.
# The parent builds _SymbolData for THIS job and hands it to a per-job
# worker via thread-local storage (single-process path) or the pool
# initializer (multi-process path), so concurrent jobs never share a
# mutable global that could clobber each other. Workers receive only
# (start, end) index ranges and read their own copy.
# ------------------------------------------------------------------


@dataclass
class _SymbolData:
    sym_id: str
    chart_data: list[dict]


# Thread-local holder instead of a module-level global: each background job
# runs in its own thread, so concurrent scans/backtests no longer overwrite
# each other's data. The pool initializer sets this inside each forked child.
_DATA_LOCAL = threading.local()


def _set_shared_data(symbol_data: list[_SymbolData]) -> None:
    _DATA_LOCAL.symbol_data = symbol_data


def _get_shared_data() -> list[_SymbolData]:
    return getattr(_DATA_LOCAL, "symbol_data", [])


class GMMVolumeV1Strategy(BaseStrategy, BaseStrategyV2):
    strategy_id = "gmm_volume_v1"
    name = "GMM 成交量密度 v1"
    min_history_bars = 250
    description = (
        "基于 GMM 多峰拟合的成交量密度策略。"
        "滚动窗口内对价格-成交量分布拟合高斯混合模型，"
        "密度低于下阈值（1-threshold）时买入，高于上阈值（threshold）时卖出。"
        "策略选股模式使用 fork 多进程并行，告别 Python GIL。"
    )

    #: 策略能力契约：手动信号与自动选股均可运行
    supported_modes = {"manual_symbols", "strategy_select"}
    #: lookback_days 上限 250，保证长回看配置吃到足量 warmup bar
    min_history_bars = 250
    signal_timestamp = "next_open"
    score_definition = "1 - 当前价在 GMM 分布中的密度百分位（越低越值得买入，范围 0~1）"
    #: 密度百分位 ∈ [0,1]，故 1-density ∈ [0,1]，天然可直接作为策略评分。
    score_range = (0.0, 1.0)
    exit_rule = "密度 ≥ threshold 时次日开盘卖出；自动选股持有天数由 policy 决定"

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
        # max_workers 已从 param_schema 移除（基础设施项），不再接受用户配置；
        # 内部固定默认并 clamp 到 [1, 8]，避免外部传超大值 fork 出过量进程。
        max_workers = min(8, max(1, int(params.get("max_workers", 4))))

        # Pre-serialize all symbol data for THIS job only (job-isolated)
        symbol_data: list[_SymbolData] = []
        for sym in symbols:
            sym_df = (
                work[work["symbol"] == sym]
                .sort_values("trade_date")
                .reset_index(drop=True)
            )
            if len(sym_df) < lookback + 2:
                continue
            symbol_data.append(
                _SymbolData(
                    sym_id=str(sym).strip(),
                    chart_data=_build_chart_data(sym_df),
                )
            )

        total = len(symbol_data)
        if total == 0:
            return pd.DataFrame(columns=cols)

        # Single-process path for small work or max_workers=1.
        # Set thread-local just for the duration of this call so concurrent
        # jobs in other threads can't overwrite our data mid-scan.
        n_workers = min(max_workers, total)
        if n_workers <= 1:
            _set_shared_data(symbol_data)
            try:
                result = _scan_range(0, total, lookback, threshold, max_comp, refit_int)
            finally:
                _set_shared_data([])
            return pd.DataFrame(result)

        # Multi-process via multiprocessing.Pool with fork.
        # Each worker gets its OWN copy of this job's symbol_data via the
        # initializer, so different jobs' pools are fully isolated.
        # BLAS threading is already disabled (module-level env) and
        # re-confirmed in the pool initializer for belt-and-suspenders.
        chunk_size = max(1, total // n_workers)
        ranges = [(i, min(i + chunk_size, total)) for i in range(0, total, chunk_size)]

        all_candidates: list[dict] = []
        ctx = multiprocessing.get_context("fork")

        with ctx.Pool(
            processes=n_workers,
            initializer=_pool_init,
            initargs=(symbol_data,),
            maxtasksperchild=1,
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
                    result = ar.get(timeout=120)
                    if result:
                        all_candidates.extend(result)
                except multiprocessing.TimeoutError:
                    _logger.error(
                        "GMM worker chunk timed out after 120s, results dropped"
                    )
                except Exception:
                    _logger.exception("GMM worker chunk failed")

        return pd.DataFrame(all_candidates)


# ------------------------------------------------------------------
# Worker: reads data from fork-inherited _SHARED_DATA via index range
# ------------------------------------------------------------------


def _pool_init(symbol_data: list[_SymbolData]):
    """Pool initializer: load this job's symbol data + force single-thread BLAS."""
    _set_shared_data(symbol_data)
    _blas_single_thread()


def _scan_range(
    start: int,
    end: int,
    lookback: int,
    threshold: float,
    max_comp: int,
    refit_int: int,
) -> list[dict]:
    """Scan symbols [start, end) using this job's thread-local shared data."""
    shared = _get_shared_data()
    candidates: list[dict] = []
    lower = 1.0 - threshold

    for idx in range(start, end):
        sd = shared[idx]
        chart_data = sd.chart_data
        n = len(chart_data)

        cached_fit = None
        for i in range(lookback, n):
            if cached_fit is None or i % refit_int == 0:
                cached_fit = DataProcessor.fit_gaussian_mixture(
                    chart_data[i - lookback : i], max_components=max_comp
                )

            if cached_fit is None or "fit_curve" not in cached_fit:
                continue

            density = _compute_density(float(chart_data[i]["price"]), cached_fit)
            if density is None:
                continue

            if density <= lower:
                candidates.append(
                    {
                        "trade_date": chart_data[i]["datetime"],
                        "symbol": sd.sym_id,
                        "signal_strength": round(1.0 - density, 4),
                        "reason": (
                            f"GMM密度 {(density * 100):.0f}% ≤ "
                            f"{(lower * 100):.0f}% (买入信号)"
                        ),
                    }
                )

    return candidates


def _blas_single_thread():
    """Pool initializer: force single-thread BLAS in child processes."""
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
