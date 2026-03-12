"""均线金叉死叉策略"""

import pandas as pd

from .base import BaseStrategy


class MACrossV1Strategy(BaseStrategy):
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
