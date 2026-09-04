"""数据源抽象基类"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

import pandas as pd

from app.providers.provenance import DailyDataResult, DataProvenance


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

    def fetch_daily_data_with_meta(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        max_retries: int = 2,
        **kwargs,
    ) -> DailyDataResult:
        """获取日线数据并返回结构化 provenance。

        默认实现基于 ``fetch_daily_data`` 做 best-effort provenance；具体子类可覆盖以
        记录真实来源与覆盖缺口。返回 ``(df, provenance)``，df 为 None 表示无数据或失败。
        """
        df = self.fetch_daily_data(
            stock_code,
            start_date,
            end_date,
            adjust=adjust,
            max_retries=max_retries,
            **kwargs,
        )
        provenance = DataProvenance(
            source=None,
            adjustment=adjust,
            requested_start=start_date,
            requested_end=end_date,
            failure_reason="",
        )
        if df is not None and not df.empty and "datetime" in df.columns:
            dates = pd.to_datetime(df["datetime"])
            provenance.actual_start = str(dates.min())[:10]
            provenance.actual_end = str(dates.max())[:10]
            provenance.bar_count = int(len(df))
            provenance.last_bar = provenance.actual_end
            provenance.gap = False
            provenance.source = "provider"
            provenance.failure_reason = None
        else:
            provenance.source = None
            provenance.failure_reason = "无数据或失败"
        return DailyDataResult(df=df, provenance=provenance)

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
