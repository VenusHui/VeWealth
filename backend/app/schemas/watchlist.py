"""
监控列表相关Schema
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class AddWatchListRequest(BaseModel):
    """添加监控请求"""

    stock_code: str = Field(..., min_length=6, max_length=6, description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    alert_enabled: bool = Field(True, description="是否启用预警")
    alert_threshold: Optional[float] = Field(
        None, ge=0, le=1, description="个性化预警阈值（null则使用用户默认值）"
    )


class UpdateWatchListRequest(BaseModel):
    """更新监控请求"""

    stock_name: Optional[str] = Field(None, description="股票名称")
    alert_enabled: Optional[bool] = Field(None, description="是否启用预警")
    alert_threshold: Optional[float] = Field(
        None, ge=0, le=1, description="个性化预警阈值"
    )


class WatchListItem(BaseModel):
    """监控列表项"""

    id: int
    stock_code: str
    stock_name: Optional[str]
    alert_enabled: bool
    alert_threshold: Optional[float]
    last_alerted_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WatchListResponse(BaseModel):
    """监控列表响应"""

    success: bool
    data: list[WatchListItem]
    total: int


class WatchListItemResponse(BaseModel):
    """单个监控项响应"""

    success: bool
    data: WatchListItem


class DeleteResponse(BaseModel):
    """删除响应"""

    success: bool
    message: str
