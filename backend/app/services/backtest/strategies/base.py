"""策略基类"""

from abc import ABC, abstractmethod

import pandas as pd


class BaseStrategy(ABC):
    strategy_id: str = "base"
    name: str = "Base"
    description: str = "策略基类"

    @classmethod
    @abstractmethod
    def param_schema(cls) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """返回带 buy_signal/sell_signal 列的DataFrame"""
        raise NotImplementedError
