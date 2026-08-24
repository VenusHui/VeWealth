"""
预警历史路由
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.alert_history import AlertHistory
from app.schemas.alert import AlertHistoryResponse

router = APIRouter(prefix="/alerts", tags=["预警历史"])


@router.get("", response_model=AlertHistoryResponse)
async def get_alert_history(
    limit: int = Query(20, ge=1, le=200, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
    direction: Optional[str] = Query(None, description="按方向筛选: buy / sell"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的预警历史记录（按时间倒序）。"""
    query = db.query(AlertHistory).filter(AlertHistory.user_id == current_user.id)
    if direction and direction in ("buy", "sell"):
        query = query.filter(AlertHistory.alert_direction == direction)
    query = query.order_by(AlertHistory.created_at.desc())
    total = query.count()
    items = query.offset(offset).limit(limit).all()

    return AlertHistoryResponse(success=True, data=items, total=total)
