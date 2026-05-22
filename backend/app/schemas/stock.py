"""
股票相关的数据模型
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class StockSearchResult(BaseModel):
    """股票搜索结果"""

    code: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    current_price: float = Field(..., description="当前价格")


class StockSearchResponse(BaseModel):
    """股票搜索响应"""

    success: bool = True
    results: List[StockSearchResult]


class ChartDataPoint(BaseModel):
    """图表数据点"""

    datetime: str = Field(..., description="日期时间")
    price: float = Field(..., description="收盘价")
    volume: float = Field(..., description="成交量")
    open: Optional[float] = Field(None, description="开盘价")
    high: Optional[float] = Field(None, description="最高价")
    low: Optional[float] = Field(None, description="最低价")


class GaussianComponent(BaseModel):
    """高斯分量参数"""

    mean: float = Field(..., description="均值（价格）")
    std: float = Field(..., description="标准差")
    weight: float = Field(..., description="权重")
    volume: float = Field(..., description="该分量对应的成交量")


class FitCurvePoint(BaseModel):
    """拟合曲线数据点"""

    price: float = Field(..., description="价格")
    fitVolume: float = Field(..., description="拟合的成交量")


class FitResult(BaseModel):
    """高斯混合模型拟合结果"""

    n_components: int = Field(..., description="高斯分量数量")
    components: List[GaussianComponent] = Field(..., description="各高斯分量参数")
    fit_curve: List[FitCurvePoint] = Field(..., description="拟合曲线数据")
    bic: float = Field(..., description="BIC评分")


class StockDataResponse(BaseModel):
    """股票数据查询响应"""

    success: bool = True
    symbol: str
    start_date: str
    end_date: str
    actual_start_date: str
    actual_end_date: str
    period: str
    chart_data: List[ChartDataPoint]
    count: int
    fit_result: Optional[FitResult] = Field(None, description="正态分布拟合结果")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "symbol": "000001",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "period": "daily",
                "chart_data": [
                    {
                        "datetime": "2024-01-01",
                        "price": 12.34,
                        "volume": 1234567,
                        "open": 12.30,
                        "high": 12.40,
                        "low": 12.25,
                    }
                ],
                "count": 20,
            }
        }


class CyqInfo(BaseModel):
    """筹码分布信息"""

    date: str = Field(..., description="数据日期")
    profit_ratio: float = Field(..., description="获利比例")
    avg_cost: float = Field(..., description="平均成本")
    cost_90_low: float = Field(..., description="90%成本区间下限")
    cost_90_high: float = Field(..., description="90%成本区间上限")
    concentration_90: float = Field(..., description="90%集中度")
    cost_70_low: float = Field(..., description="70%成本区间下限")
    cost_70_high: float = Field(..., description="70%成本区间上限")
    concentration_70: float = Field(..., description="70%集中度")


class CyqDataResponse(BaseModel):
    """筹码分布数据响应"""

    success: bool = True
    symbol: str = Field(..., description="股票代码")
    adjust: str = Field(..., description="复权类型")
    cyq_info: CyqInfo = Field(..., description="筹码分布信息")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "symbol": "000001",
                "adjust": "",
                "cyq_info": {
                    "date": "2024-01-11",
                    "profit_ratio": 0.074399,
                    "avg_cost": 11.25,
                    "cost_90_low": 9.16,
                    "cost_90_high": 12.56,
                    "concentration_90": 0.173302,
                    "cost_70_low": 9.33,
                    "cost_70_high": 12.56,
                    "concentration_70": 0.147273,
                },
            }
        }


# ---------------------------------------------------------------------------
# K-line endpoint schemas
# ---------------------------------------------------------------------------


class KlineDataPoint(BaseModel):
    """K-line bar data point"""

    datetime: str = Field(..., description="日期时间")
    open: float = Field(..., description="开盘价")
    close: float = Field(..., description="收盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    volume: float = Field(..., description="成交量")
    amount: Optional[float] = Field(None, description="成交额")


class KlineResponse(BaseModel):
    """K-line data query response"""

    success: bool = True
    symbol: str
    period: str
    adjust: str
    start_date: str
    end_date: str
    actual_start_date: str
    actual_end_date: str
    count: int
    klines: List[KlineDataPoint]


# ---------------------------------------------------------------------------
# Volume Profile endpoint schemas
# ---------------------------------------------------------------------------


class VolumeProfilePoint(BaseModel):
    """Volume profile data point"""

    price: float = Field(..., description="价格")
    volume: float = Field(..., description="该价格的累积成交量")


class ValueArea(BaseModel):
    """Value area bounds"""

    vah: float = Field(..., description="Value Area High")
    val: float = Field(..., description="Value Area Low")
    volume_pct: float = Field(..., description="Value Area 覆盖的成交量百分比")


class PocData(BaseModel):
    """Point of Control"""

    price: float = Field(..., description="POC 价格")
    volume: float = Field(..., description="POC 成交量")


class VolumeProfileResponse(BaseModel):
    """Volume Profile query response"""

    success: bool = True
    symbol: str
    period: str
    total_volume: float
    price_min: float
    price_max: float
    bin_size: float
    profile: List[VolumeProfilePoint]
    poc: PocData
    value_area: ValueArea
    hvn_levels: List[float]
    lvn_levels: List[float]
    vwap: float
    fit_result: Optional[FitResult] = Field(None, description="Volume Profile分布的GMM拟合结果")


# ---------------------------------------------------------------------------
# Stock info schemas
# ---------------------------------------------------------------------------


class StockInfo(BaseModel):
    """个股基本信息"""

    code: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    industry: str = Field("", description="所属行业")
    total_shares: float = Field(0, description="总股本")
    float_shares: float = Field(0, description="流通股本")
    mcap: float = Field(0, description="总市值")
    float_mcap: float = Field(0, description="流通市值")
    list_date: str = Field("", description="上市日期")
    price: float = Field(0, description="最新价")


class TencentQuote(BaseModel):
    """腾讯行情实时数据"""

    name: str = Field("", description="股票名称")
    price: float = Field(0, description="最新价")
    last_close: float = Field(0, description="昨收")
    open: float = Field(0, description="开盘价")
    change_amt: float = Field(0, description="涨跌额")
    change_pct: float = Field(0, description="涨跌幅")
    high: float = Field(0, description="最高价")
    low: float = Field(0, description="最低价")
    amount_wan: float = Field(0, description="成交额(万)")
    turnover_pct: float = Field(0, description="换手率")
    pe_ttm: float = Field(0, description="市盈率(TTM)")
    amplitude_pct: float = Field(0, description="振幅")
    mcap_yi: float = Field(0, description="总市值(亿)")
    float_mcap_yi: float = Field(0, description="流通市值(亿)")
    pb: float = Field(0, description="市净率")
    limit_up: float = Field(0, description="涨停价")
    limit_down: float = Field(0, description="跌停价")
    vol_ratio: float = Field(0, description="量比")
    pe_static: float = Field(0, description="市盈率(静态)")


class StockInfoResponse(BaseModel):
    """个股信息响应"""

    success: bool = True
    symbol: str
    stock_info: Optional[StockInfo] = Field(None, description="个股基本信息")
    tencent_quote: Optional[TencentQuote] = Field(None, description="腾讯行情数据")


# ---------------------------------------------------------------------------
# Depth (combined) endpoint schemas
# ---------------------------------------------------------------------------


class DepthResponse(BaseModel):
    """深度数据综合响应 - 一次返回 K线 + Volume Profile + 筹码分布 + 个股信息"""

    success: bool = True
    symbol: str
    period: str
    adjust: str
    start_date: str
    end_date: str
    klines: List[KlineDataPoint]
    volume_profile: Optional[VolumeProfileResponse] = None
    cyq_info: Optional[CyqInfo] = None
    stock_info: Optional[StockInfo] = None
    tencent_quote: Optional[TencentQuote] = None
