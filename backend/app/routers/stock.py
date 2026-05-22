"""
股票相关API路由
负责处理HTTP请求和响应，不包含业务逻辑
"""

from fastapi import APIRouter, HTTPException, Query

from app.schemas.stock import (
    StockSearchResponse,
    StockDataResponse,
    CyqDataResponse,
    KlineResponse,
    VolumeProfileResponse,
    DepthResponse,
    StockInfoResponse,
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


@router.get("/data", response_model=StockDataResponse)
async def get_stock_data(
    symbol: str = Query(..., description="股票代码，例如：000001"),
    start_date: str = Query(..., description="开始日期，格式：YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期，格式：YYYY-MM-DD"),
    period: str = Query("1", description="K线周期: 1/5/15/30/60/101"),
):
    """
    获取股票数据（多周期支持）

    - period=1: 1分钟数据（默认，兼容旧版）
    - period=5/15/30/60: 对应分钟K线
    - period=101: 日线数据
    - 自动进行GMM高斯混合模型拟合
    """
    try:
        data = stock_service.get_stock_data(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            period=period,
        )
        return StockDataResponse(**data)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kline", response_model=KlineResponse)
async def get_kline_data(
    symbol: str = Query(..., description="股票代码，例如：000001"),
    period: str = Query("5", description="K线周期: 1/5/15/30/60/101"),
    start_date: str = Query("", description="开始日期，格式：YYYY-MM-DD"),
    end_date: str = Query("", description="结束日期，格式：YYYY-MM-DD"),
    adjust: str = Query("qfq", description="复权类型: qfq/hfq/''"),
):
    """获取K线数据（支持多周期）。"""
    try:
        data = stock_service.get_kline_data(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
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
