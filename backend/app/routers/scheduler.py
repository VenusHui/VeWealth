"""
调度器管理路由
"""

from fastapi import APIRouter, Depends
from app.core.deps import get_current_active_user
from app.models.user import User
from app.services.scheduler import app_scheduler, collect_daily_data, check_price_alerts

router = APIRouter(prefix="/scheduler", tags=["调度器管理"])


@router.post("/run/collect-data")
async def run_collect_data(current_user: User = Depends(get_current_active_user)):
    """
    手动触发数据采集任务
    
    需要登录才能访问
    """
    result = collect_daily_data()
    return {
        "success": result is not None,
        "message": "数据采集任务已执行",
        "result": result
    }


@router.post("/run/check-alerts")
async def run_check_alerts(current_user: User = Depends(get_current_active_user)):
    """
    手动触发价格预警检查
    
    需要登录才能访问
    """
    result = check_price_alerts()
    return {
        "success": result is not None,
        "message": "预警检查任务已执行",
        "result": result
    }

