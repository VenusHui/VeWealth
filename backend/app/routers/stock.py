"""
股票相关API路由
负责处理HTTP请求和响应，不包含业务逻辑
"""

from fastapi import APIRouter, HTTPException, Query

from app.schemas.stock import (
    StockSearchResponse,
    KlineResponse,
    VolumeProfileResponse,
    DepthResponse,
    StockInfoResponse,
    BatchQuoteResponse,
)
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


@router.get("/kline", response_model=KlineResponse)
async def get_kline_data(
    symbol: str = Query(..., description="股票代码，例如：000001"),
    period: str = Query("5", description="K线周期: 1/5/15/30/60/101"),
    start_date: str = Query("", description="开始日期，格式：YYYY-MM-DD"),
    end_date: str = Query("", description="结束日期，格式：YYYY-MM-DD"),
    adjust: str = Query("qfq", description="复权类型: qfq/hfq/''"),
    offset: int = Query(0, description="数据偏移量（分页用，跳过前N根K线）"),
    count: int = Query(500, description="返回的最大K线数量"),
):
    """获取K线数据（支持多周期+分页）。offset/count 用于前端滚动动态加载。"""
    try:
        data = stock_service.get_kline_data(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            offset=offset,
            count=count,
        )
        return KlineResponse(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/volume-profile", response_model=VolumeProfileResponse)
async def get_volume_profile(
    symbol: str = Query(..., description="股票代码，例如：000001"),
    period: str = Query("5", description="K线周期: 1/5/15/30/60/101"),
    start_date: str = Query("", description="开始日期，格式：YYYY-MM-DD"),
    end_date: str = Query("", description="结束日期，格式：YYYY-MM-DD"),
    adjust: str = Query("qfq", description="复权类型"),
    bins: int = Query(100, description="价格bin数量"),
):
    """获取Volume Profile（成交量分布）。"""
    try:
        data = stock_service.get_volume_profile(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            bins=bins,
        )
        return VolumeProfileResponse(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/depth", response_model=DepthResponse)
async def get_depth_data(
    symbol: str = Query(..., description="股票代码，例如：000001"),
    period: str = Query("5", description="K线周期: 1/5/15/30/60/101"),
    start_date: str = Query("", description="开始日期，格式：YYYY-MM-DD"),
    end_date: str = Query("", description="结束日期，格式：YYYY-MM-DD"),
    adjust: str = Query("qfq", description="复权类型"),
):
    """获取深度数据（K线 + Volume Profile + 筹码分布 + 个股信息）。"""
    try:
        data = stock_service.get_depth_data(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        return DepthResponse(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info", response_model=StockInfoResponse)
async def get_stock_info(
    symbol: str = Query(..., description="股票代码，例如：000001"),
):
    """获取个股基本信息 + 腾讯行情数据。"""
    try:
        data = stock_service.get_stock_info(symbol)
        return StockInfoResponse(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quotes", response_model=BatchQuoteResponse)
async def get_batch_quotes(
    codes: str = Query(..., description="股票代码列表，逗号分隔，例如：000001,600519"),
):
    """批量获取腾讯实时行情。"""
    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        data = stock_service.get_batch_quotes(code_list)
        return BatchQuoteResponse(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
