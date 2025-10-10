"""
股票相关API路由
负责处理HTTP请求和响应，不包含业务逻辑
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Literal, List
from datetime import datetime

from app.schemas.stock import StockSearchResponse, StockDataResponse
from app.services.stock_service import stock_service
from app.utils.trading_calendar import trading_calendar

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get("/search", response_model=StockSearchResponse)
async def search_stock(
    keyword: str = Query(..., description="股票代码或名称关键词", min_length=1)
):
    """
    搜索股票
    
    支持按代码或名称模糊搜索A股股票
    """
    try:
        results = stock_service.search_stocks(keyword)
        return StockSearchResponse(success=True, results=results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data", response_model=StockDataResponse)
async def get_stock_data(
    symbol: str = Query(..., description="股票代码，例如：000001"),
    start_date: str = Query(..., description="开始日期，格式：YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期，格式：YYYY-MM-DD"),
    period: Literal["1min", "daily"] = Query(
        "daily",
        description="数据周期：1min（1分钟） 或 daily（日线）"
    )
):
    """
    获取股票数据
    
    支持两种时间周期：
    - 1min: 1分钟级数据，无日期限制，使用多线程加速获取
    - daily: 日线数据
    
    注意：分钟数据会根据交易日历自动过滤非交易日
    """
    try:
        data = stock_service.get_stock_data(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            period=period
        )
        return StockDataResponse(**data)
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trading-days")
async def get_trading_days(
    days: int = Query(30, description="获取最近N个交易日", ge=1, le=365)
) -> dict:
    """
    获取最近的交易日列表
    
    Args:
        days: 天数（1-365）
        
    Returns:
        交易日列表
    """
    try:
        trading_days = trading_calendar.get_recent_trading_days(days)
        return {
            "success": True,
            "trading_days": trading_days,
            "count": len(trading_days)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/is-trading-day")
async def check_trading_day(
    date: str = Query(..., description="日期，格式：YYYY-MM-DD")
) -> dict:
    """
    检查指定日期是否为交易日
    
    Args:
        date: 日期字符串
        
    Returns:
        是否为交易日
    """
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        is_trading = trading_calendar.is_trading_day(dt)
        return {
            "success": True,
            "date": date,
            "is_trading_day": is_trading
        }
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

