"""
股票相关API路由
负责处理HTTP请求和响应，不包含业务逻辑
"""

from fastapi import APIRouter, HTTPException, Query

from app.schemas.stock import StockSearchResponse, StockDataResponse, CyqDataResponse
from app.services.stock_service import stock_service

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
):
    """
    获取股票1分钟级数据

    特性：
    - 1分钟级数据，最细粒度
    - 无日期限制，使用多线程加速获取
    - 自动根据交易日历过滤非交易日
    - 适合价格分布分析
    """
    try:
        data = stock_service.get_stock_data(
            symbol=symbol, start_date=start_date, end_date=end_date
        )
        return StockDataResponse(**data)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cyq", response_model=CyqDataResponse)
async def get_cyq_data(
    symbol: str = Query(..., description="股票代码，例如：000001"),
    adjust: str = Query("", description="复权类型，''表示不复权，'qfq'表示前复权"),
):
    """
    获取股票筹码分布数据

    特性：
    - 筹码分布数据（获利比例、平均成本等）
    - 支持复权选项
    - 用于分析主力成本和散户持仓情况
    """
    try:
        data = stock_service.get_cyq_data(symbol=symbol, adjust=adjust)
        return CyqDataResponse(**data)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
