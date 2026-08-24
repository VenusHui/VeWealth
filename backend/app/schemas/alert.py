"""
预警历史相关Schema
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class AlertHistoryItem(BaseModel):
    """预警历史记录项"""

    id: int
    user_id: int
    stock_code: str
    stock_name: Optional[str]
    alert_threshold: Optional[float]
    current_price: float
    change_pct: Optional[float]
    alert_direction: Optional[str] = Field(None, description="预警方向: buy / sell")
    density_value: Optional[float] = Field(None, description="触发时的密度百分位")
    peak_price: Optional[float] = Field(None, description="最近的GMM峰值价格")
    created_at: datetime

    class Config:
        from_attributes = True


class AlertHistoryResponse(BaseModel):
    """预警历史响应"""

    success: bool
    data: list[AlertHistoryItem]
    total: int
