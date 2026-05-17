"""数据源抽象基类"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

import pandas as pd


class MarketDataProvider(ABC):
    """数据源提供者抽象接口。

    每个具体实现负责将源数据列名标准化为英文列名，
    消费方不应依赖任何源特定的列名（如中文列名）。
    """

    @abstractmethod
    def fetch_daily_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        max_retries: int = 2,
    ) -> Optional[pd.DataFrame]:
        """获取日线数据。

        Returns:
            DataFrame columns: datetime, open, close, high, low, volume, amount
            失败或无数据返回 None
        """
        ...

    @abstractmethod
    def fetch_minute_data(
        self,
        stock_code: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "1",
        adjust: str = "",
        max_retries: int = 2,
    ) -> Optional[pd.DataFrame]:
        """获取分钟数据。

        Returns:
            DataFrame columns: datetime, open, close, high, low, volume
            失败或无数据返回 None
        """
        ...

    @abstractmethod
    def fetch_realtime_data(self) -> Optional[pd.DataFrame]:
        """获取全市场实时行情。

        Returns:
            DataFrame columns: code, name, price
            其他列（涨跌幅、成交量等）为 best-effort，不同数据源可能不同
            失败或无数据返回 None
        """
        ...

    @abstractmethod
    def fetch_cyq_data(
        self, stock_code: str, adjust: str = ""
    ) -> Optional[pd.DataFrame]:
        """获取筹码分布原始数据（格式由各数据源定义）。"""
        ...

    @abstractmethod
    def normalize_cyq_data(self, df: pd.DataFrame) -> dict[str, Any]:
        """将 fetch_cyq_data 的原始 DataFrame 转换为标准化 dict。

        Returns keys: date, profit_ratio, avg_cost, cost_90_low, cost_90_high,
                      concentration_90, cost_70_low, cost_70_high, concentration_70
        """
        ...
