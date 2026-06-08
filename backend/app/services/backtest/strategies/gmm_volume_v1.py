"""GMM 成交量密度策略 — 基于高斯混合模型的量价分布交易信号"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseStrategy
from .contracts import BaseStrategyV2
from app.utils.data_processor import DataProcessor


class GMMVolumeV1Strategy(BaseStrategy, BaseStrategyV2):
    strategy_id = "gmm_volume_v1"
    name = "GMM 成交量密度 v1"
    description = (
        "基于 GMM 多峰拟合的成交量密度策略。"
        "滚动窗口内对价格-成交量分布拟合高斯混合模型，"
        "密度低于下阈值（1-threshold）时买入，高于上阈值（threshold）时卖出。"
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
    # V2: strategy_select mode — cross-sectional candidate generation
    # ------------------------------------------------------------------

    def generate_candidates(
        self, market_df: pd.DataFrame, params: dict
    ) -> pd.DataFrame:
        cols = ["trade_date", "symbol", "signal_strength", "reason"]
        if market_df.empty:
            return pd.DataFrame(columns=cols)

        work = market_df.copy()
        work["trade_date"] = pd.to_datetime(work["datetime"])
        symbols = work["symbol"].dropna().unique()

        lookback = int(params.get("lookback_days", 60))
        threshold = float(params.get("threshold", 0.7))
        max_comp = int(params.get("max_components", 5))
        refit_int = int(params.get("refit_interval", 5))
        upper = threshold
        lower = 1.0 - threshold

        candidates: list[dict] = []
        for sym in symbols:
            sym_df = (
                work[work["symbol"] == sym]
                .sort_values("trade_date")
                .reset_index(drop=True)
            )
            if len(sym_df) < lookback + 2:
                continue

            cached_fit = None
            for i in range(lookback, len(sym_df)):
                window = sym_df.iloc[i - lookback : i]
                if len(window) < max(10, lookback // 2):
                    continue

                if cached_fit is None or i % refit_int == 0:
                    chart_data = _build_chart_data(window)
                    cached_fit = DataProcessor.fit_gaussian_mixture(
                        chart_data, max_components=max_comp
                    )

                if cached_fit is None or "fit_curve" not in cached_fit:
                    continue

                close_price = float(sym_df.iloc[i]["close"])
                density = _compute_density(close_price, cached_fit)
                if density is None:
                    continue

                if density <= lower:
                    candidates.append(
                        {
                            "trade_date": sym_df.iloc[i]["trade_date"],
                            "symbol": str(sym).strip(),
                            "signal_strength": round(1.0 - density, 4),
                            "reason": (
                                f"GMM密度 {(density * 100):.0f}% ≤ "
                                f"{(lower * 100):.0f}% (买入信号)"
                            ),
                        }
                    )

        return pd.DataFrame(candidates)


# ------------------------------------------------------------------
# Shared helpers
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
