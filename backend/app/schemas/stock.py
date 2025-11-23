"""
股票相关的数据模型
"""

from typing import List, Optional, Literal
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


class StockDataRequest(BaseModel):
    """股票数据查询请求"""

    symbol: str = Field(..., description="股票代码")
    start_date: str = Field(..., description="开始日期，格式：YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期，格式：YYYY-MM-DD")


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
