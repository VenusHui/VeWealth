"""连续缩量下跌策略（选股+固定持有）"""

from .base import BaseStrategy


class VolumeShrinkDropV1Strategy(BaseStrategy):
    strategy_id = "volume_shrink_drop_v1"
    name = "连续缩量下跌反弹 v1"
    description = "连续N天缩量下跌，下一交易日开盘买入，持有M天后开盘卖出。"

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

    def generate_signals(self, df, params):
        return df
