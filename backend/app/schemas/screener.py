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
    total: int = 0
    scanned: int = 0
    hits: int = 0


class ScreenerResultItem(BaseModel):
    symbol: str
    stock_name: str | None = None
    signal_strength: float = 0.0
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
