"""回测相关Schema"""

from datetime import datetime, date
from typing import Any, Optional

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
    symbols: list[str] = Field(..., min_length=1)
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
