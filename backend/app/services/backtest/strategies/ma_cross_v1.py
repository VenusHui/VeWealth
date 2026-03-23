"""均线金叉死叉策略"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy
from .contracts import BaseStrategyV2


class MACrossV1Strategy(BaseStrategy, BaseStrategyV2):
    strategy_id = "ma_cross_v1"
    name = "双均线策略 v1"
    description = "短均线上穿长均线买入，下穿卖出。仅做多。"

    @classmethod
    def param_schema(cls) -> list[dict]:
        return [
            {
                "key": "short_window",
                "label": "短均线周期",
                "type": "int",
                "required": True,
                "default": 5,
                "min": 2,
                "max": 120,
            },
            {
                "key": "long_window",
                "label": "长均线周期",
                "type": "int",
                "required": True,
                "default": 20,
                "min": 3,
                "max": 240,
            },
        ]

    @classmethod
    def required_columns(cls) -> set[str]:
        return {"datetime", "open", "close"}

    @classmethod
    def default_policy_profile(cls) -> str:
        return "vsd_v1_default"

    def generate_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        short_window = int(params.get("short_window", 5))
        long_window = int(params.get("long_window", 20))

        if short_window >= long_window:
            raise ValueError("short_window 必须小于 long_window")

        signal_df = df.copy()
        signal_df["ma_short"] = signal_df["close"].rolling(window=short_window).mean()
        signal_df["ma_long"] = signal_df["close"].rolling(window=long_window).mean()

        cross_up = (signal_df["ma_short"] > signal_df["ma_long"]) & (
            signal_df["ma_short"].shift(1) <= signal_df["ma_long"].shift(1)
        )
        cross_down = (signal_df["ma_short"] < signal_df["ma_long"]) & (
            signal_df["ma_short"].shift(1) >= signal_df["ma_long"].shift(1)
        )

        signal_df["buy_signal"] = cross_up.fillna(False)
        signal_df["sell_signal"] = cross_down.fillna(False)
        return signal_df

    def generate_candidates(self, market_df: pd.DataFrame, params: dict) -> pd.DataFrame:
        signal_df = self.generate_signals(market_df, params)
        buy_rows = signal_df[signal_df["buy_signal"]].copy()
        if buy_rows.empty:
            return pd.DataFrame(columns=["trade_date", "symbol", "signal_strength", "reason"])

        buy_rows["trade_date"] = pd.to_datetime(buy_rows["datetime"])
        buy_rows["symbol"] = buy_rows.get("symbol", "")
        buy_rows["signal_strength"] = (
            (buy_rows["ma_short"] - buy_rows["ma_long"]) / buy_rows["ma_long"].abs()
        ).fillna(0.0)
        buy_rows["reason"] = "均线金叉"

        return buy_rows[["trade_date", "symbol", "signal_strength", "reason"]]
