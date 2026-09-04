"""Screener schemas — request/response models for the stock screening feature."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    strategy_id: str = Field(..., description="策略ID, e.g. ma_cross_v1")
    strategy_params: dict[str, Any] = Field(
        default_factory=dict, description="策略参数, key-value pairs"
    )
    boards: list[str] = Field(
        default_factory=lambda: ["main"],
        description="板块过滤: main, gem, star, bse",
    )
    exclude_st: bool = Field(True, description="是否排除 ST 股票")


# ---------------------------------------------------------------------------
# Progress & Result items
# ---------------------------------------------------------------------------


class ScanProgress(BaseModel):
    #: 漏斗计数：fetched + data_failed == total；data_ok == fetched - stale_data_count；
    #: signal_hits + rejected == evaluated。每个字段只表达一件事，不复用。
    total: int = 0
    fetched: int = 0  #: 成功取到非空数据的股票数
    data_ok: int = 0  #: fetched 中最新数据日 >= as_of_date 的股票数
    data_failed: int = 0  #: 取数失败/空数据股票数
    evaluated: int = 0  #: data_ok 中已跑完策略计算的股票数
    signal_hits: int = 0  #: evaluated 中在 as_of_date 命中信号的股票数
    rejected: int = 0  #: evaluated 中当日无信号的股票数
    stale_data_count: int = 0  #: fetched 中数据末日落后者
    as_of_date: str | None = None


class ScreenerResultItem(BaseModel):
    symbol: str
    stock_name: str | None = None
    #: 原始单调信号强度（用于内部排序/比较）
    signal_strength: float = 0.0
    #: 归一化 [0,1] 策略评分（前端条形与数值同尺度展示）
    strategy_score: float = 0.0
    reason: str = ""
    current_price: float | None = None
    change_pct: float | None = None


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class ScanResponse(BaseModel):
    scan_id: str
    status: str  # scanning | completed | failed
    strategy_id: str
    boards: list[str] = []
    exclude_st: bool = True
    progress: ScanProgress = Field(default_factory=ScanProgress)
    #: 本次扫描的 as-of 日期；只有该日信号算候选，数据末日落后者计入 stale_data_count。
    as_of_date: str | None = None
    results: list[ScreenerResultItem] = Field(default_factory=list)
    error: str | None = None
    created_at: str = ""
    completed_at: str | None = None


class ScanListItem(BaseModel):
    scan_id: str
    strategy_id: str
    strategy_name: str = ""
    status: str
    boards: list[str] = []
    exclude_st: bool = True
    total_scanned: int = 0
    total_hits: int = 0
    created_at: str = ""


class ScanListResponse(BaseModel):
    data: list[ScanListItem] = Field(default_factory=list)
    total: int = 0
