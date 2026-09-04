"""数据源抽象基类"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

import pandas as pd

from app.providers.provenance import DailyDataResult, DataProvenance


def _norm_date(value: str | None) -> str | None:
    """把请求日期归一化为 YYYY-MM-DD（入参可能是 YYYYMMDD 或 YYYY-MM-DD）。"""
    if not value:
        return None
    try:
        return str(pd.Timestamp(str(value)).normalize())[:10]
    except Exception:
        return str(value)


def _has_coverage_gap(
    req_start: str | None,
    req_end: str | None,
    actual_start: str | None,
    actual_end: str | None,
) -> bool:
    """判断实际范围是否覆盖请求范围：起点滞后或终点提前即为覆盖缺口。"""
    if not actual_start or not actual_end:
        return True
    gap = False
    if req_start:
        if pd.Timestamp(actual_start).normalize() > pd.Timestamp(req_start).normalize():
            gap = True
    if req_end:
        if pd.Timestamp(actual_end).normalize() < pd.Timestamp(req_end).normalize():
            gap = True
    return gap


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

        默认实现基于 ``fetch_daily_data`` 做 best-effort provenance（source="provider"）；
        具体子类可覆盖以记录真实来源与覆盖缺口。返回 ``(df, provenance)``，
        df 为 None 表示无数据或失败。
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
            requested_start=_norm_date(start_date),
            requested_end=_norm_date(end_date),
            failure_reason="",
        )
        if df is not None and not df.empty and "datetime" in df.columns:
            dates = pd.to_datetime(df["datetime"])
            actual_start = str(dates.min())[:10]
            actual_end = str(dates.max())[:10]
            provenance.actual_start = actual_start
            provenance.actual_end = actual_end
            provenance.bar_count = int(len(df))
            provenance.last_bar = actual_end
            provenance.gap = _has_coverage_gap(
                provenance.requested_start,
                provenance.requested_end,
                actual_start,
                actual_end,
            )
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
