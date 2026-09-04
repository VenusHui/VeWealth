"""连续缩量下跌策略（选股+固定持有）"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy
from .contracts import BaseStrategyV2


class VolumeShrinkDropV1Strategy(BaseStrategy, BaseStrategyV2):
    strategy_id = "volume_shrink_drop_v1"
    name = "连续缩量下跌反弹 v1"
    description = "连续N天缩量下跌，下一交易日开盘买入，持有M天后开盘卖出。"
    min_history_bars = 71

    #: 策略能力契约：补齐手动买卖信号，两种模式均可运行
    supported_modes = {"manual_symbols", "strategy_select"}
    #: consecutive_days(10) + hold_days(60) + 1，保证完整持有周期有足量 bar
    min_history_bars = 71
    signal_timestamp = "next_open"
    score_definition = "固定 1.0（连续 N 天缩量下跌命中即入选）"
    exit_rule = "手动模式持有 hold_days 天后次日开盘卖出；自动选股由 policy 固定持有"

    @classmethod
    def param_schema(cls) -> list[dict]:
        return [
            {
                "key": "min_price_drop_pct",
                "label": "单日最小跌幅(%)",
                "type": "float",
                "required": True,
                "default": -1.0,
            },
            {
                "key": "min_volume_shrink_pct",
                "label": "单日最小缩量幅度(%)",
                "type": "float",
                "required": True,
                "default": 10.0,
            },
            {
                "key": "consecutive_days",
                "label": "连续触发天数",
                "type": "int",
                "required": True,
                "default": 3,
                "min": 2,
                "max": 10,
            },
            {
                "key": "hold_days",
                "label": "持有天数",
                "type": "int",
                "required": True,
                "default": 5,
                "min": 1,
                "max": 60,
            },
            {
                "key": "position_size_pct",
                "label": "单笔仓位占比(0~1)",
                "type": "float",
                "required": True,
                "default": 0.1,
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

    def generate_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """手动模式买卖信号：与 strategy_select 候选触发条件一致。

        连续 N 天缩量下跌窗口的最后一天置 buy_signal=True，引擎于次日开盘买入；
        买入后持有 hold_days 天，于第 hold_days 天置 sell_signal=True，引擎次日开盘卖出。
        """
        min_price_drop_pct = float(params.get("min_price_drop_pct", -1.0))
        min_volume_shrink_pct = float(params.get("min_volume_shrink_pct", 10.0))
        consecutive_days = int(params.get("consecutive_days", 3))
        hold_days = int(params.get("hold_days", 5))

        work = df.copy().reset_index(drop=True)
        work["datetime"] = pd.to_datetime(work["datetime"])
        work = work.sort_values("datetime").reset_index(drop=True)
        work["buy_signal"] = False
        work["sell_signal"] = False

        if len(work) < consecutive_days + 2:
            return work

        work["close_prev"] = work["close"].shift(1)
        work["vol_prev"] = work["volume"].shift(1)
        work["price_drop_pct"] = (work["close"] / work["close_prev"] - 1.0) * 100
        work["volume_shrink_pct"] = (
            (work["vol_prev"] - work["volume"]) / work["vol_prev"]
        ) * 100
        work["daily_pass"] = (work["price_drop_pct"] <= min_price_drop_pct) & (
            work["volume_shrink_pct"] >= min_volume_shrink_pct
        )

        for i in range(consecutive_days, len(work)):
            window = work.iloc[i - consecutive_days + 1 : i + 1]
            if not bool(window["daily_pass"].all()):
                continue
            # 引擎在 buy_signal 的下一根 bar 开盘买入，因此把信号打在窗口末日 i，
            # 实际买入日为 i+1。
            buy_idx = i + 1
            if buy_idx >= len(work):
                continue
            work.at[i, "buy_signal"] = True
            # 引擎在 sell_signal 的下一根 bar 开盘卖出。买入日为 i+1，
            # 卖出日为 i+1+hold_days，故 sell_signal 打在 i+hold_days。
            sell_idx = i + hold_days
            if sell_idx < len(work):
                work.at[sell_idx, "sell_signal"] = True

        return work

    def generate_candidates(
        self, market_df: pd.DataFrame, params: dict
    ) -> pd.DataFrame:
        """V2 候选生成：保持与 strategy_select 触发条件一致，仅输出买入候选日。"""
        if market_df.empty:
            return pd.DataFrame(
                columns=["trade_date", "symbol", "signal_strength", "reason"]
            )

        min_price_drop_pct = float(params.get("min_price_drop_pct", -1.0))
        min_volume_shrink_pct = float(params.get("min_volume_shrink_pct", 10.0))
        consecutive_days = int(params.get("consecutive_days", 3))

        work = market_df.copy().reset_index(drop=True)
        work["trade_date"] = pd.to_datetime(work["datetime"])
        work["close_prev"] = work["close"].shift(1)
        work["vol_prev"] = work["volume"].shift(1)
        work["price_drop_pct"] = (work["close"] / work["close_prev"] - 1.0) * 100
        work["volume_shrink_pct"] = (
            (work["vol_prev"] - work["volume"]) / work["vol_prev"]
        ) * 100
        work["daily_pass"] = (work["price_drop_pct"] <= min_price_drop_pct) & (
            work["volume_shrink_pct"] >= min_volume_shrink_pct
        )

        candidates: list[dict] = []
        i = consecutive_days
        while i < len(work):
            window = work.iloc[i - consecutive_days + 1 : i + 1]
            if bool(window["daily_pass"].all()):
                # 候选日期统一表示信号形成日；成交日由组合引擎严格推进到
                # 下一交易日开盘，策略层不得预先偷换时间语义。
                signal_row = work.iloc[i]
                symbol = str(signal_row.get("symbol") or "").strip()
                if symbol:
                    candidates.append(
                        {
                            "trade_date": signal_row["trade_date"],
                            "symbol": symbol,
                            "signal_strength": 1.0,
                            "reason": f"连续{consecutive_days}天缩量下跌",
                        }
                    )
            i += 1

        return pd.DataFrame(candidates)
