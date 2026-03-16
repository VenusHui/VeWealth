"""回测相关Schema"""

from datetime import datetime, date
from typing import Any, Optional, Literal

from pydantic import BaseModel, Field


class StrategyParamSchema(BaseModel):
    key: str
    label: str
    type: str
    required: bool = True
    default: Optional[Any] = None
    min: Optional[float] = None
    max: Optional[float] = None
    options: Optional[list[str]] = None


class StrategyInfo(BaseModel):
    strategy_id: str
    name: str
    description: str
    param_schema: list[StrategyParamSchema]


class BacktestStrategiesResponse(BaseModel):
    success: bool = True
    data: list[StrategyInfo]


class CostConfig(BaseModel):
    commission_rate: float = Field(0.0003, ge=0, description="佣金费率")
    min_commission: float = Field(5.0, ge=0, description="最低佣金")
    stamp_tax_rate: float = Field(0.001, ge=0, description="印花税（仅卖出）")
    slippage_rate: float = Field(0.0005, ge=0, description="滑点")


class BacktestRunRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    strategy_id: str = Field(..., min_length=1, max_length=64)
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["manual_symbols", "strategy_select"] = "manual_symbols"
    universe_type: Literal["all", "custom"] = "all"
    symbols: list[str] = Field(default_factory=list)
    pool_symbols: list[str] = Field(default_factory=list)
    start_date: date
    end_date: date
    initial_cash: float = Field(..., gt=0)
    benchmark: Optional[str] = None
    cost_config: CostConfig = Field(default_factory=CostConfig)


class BacktestSummary(BaseModel):
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float
    win_rate: float
    profit_loss_ratio: float
    turnover: float
    total_trades: int


class BacktestRunResult(BaseModel):
    run_id: int
    summary: BacktestSummary
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    positions_snapshot: list[dict[str, Any]]
    warnings: list[str]
    diagnostics: Optional[dict[str, Any]] = None


class BacktestRunResponse(BaseModel):
    success: bool = True
    data: BacktestRunResult


class BacktestRunListItem(BaseModel):
    id: int
    name: str
    status: str
    strategy_id: str
    symbols: list[str]
    start_date: date
    end_date: date
    initial_cash: float
    summary: Optional[dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True


class BacktestRunListResponse(BaseModel):
    success: bool = True
    data: list[BacktestRunListItem]
    total: int


class BacktestRunDetail(BaseModel):
    id: int
    name: str
    status: str
    strategy_id: str
    strategy_params: dict[str, Any]
    symbols: list[str]
    start_date: date
    end_date: date
    initial_cash: float
    benchmark: Optional[str]
    cost_config: dict[str, Any]
    summary: Optional[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    warnings: list[str]
    error_message: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class BacktestRunDetailResponse(BaseModel):
    success: bool = True
    data: BacktestRunDetail


class BacktestJobItem(BaseModel):
    job_id: str
    status: Literal["pending", "running", "success", "failed", "cancelled"]
    progress_pct: float = 0
    total_symbols: int = 0
    processed_symbols: int = 0
    eta_seconds: Optional[int] = None
    stage: str = "pending"
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class BacktestJobDetail(BacktestJobItem):
    request_payload: dict[str, Any]
    result: Optional[dict[str, Any]] = None


class BacktestJobCreateResponse(BaseModel):
    success: bool = True
    data: BacktestJobItem


class BacktestJobListResponse(BaseModel):
    success: bool = True
    data: list[BacktestJobItem]


class BacktestJobDetailResponse(BaseModel):
    success: bool = True
    data: BacktestJobDetail


class BacktestRunOverviewResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class BacktestRunTradesResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]
    total: int


class BacktestRunRoundsResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]
    total: int


class BacktestRunSnapshotsResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]


class BacktestRunStrategyConfigResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]
