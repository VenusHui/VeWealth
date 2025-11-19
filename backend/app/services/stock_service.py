"""
股票数据服务
负责调用AKShare API获取数据并进行处理
"""

import pandas as pd
from typing import List, Dict, Any
from datetime import datetime
import logging

from app.schemas.stock import StockSearchResult
from app.utils.data_processor import DataProcessor
from app.utils.stock_data_fetcher import stock_data_fetcher
from app.core.config import settings

logger = logging.getLogger(__name__)


class StockService:
    """股票数据服务类"""

    def __init__(self):
        self.data_processor = DataProcessor()

    def search_stocks(self, keyword: str) -> List[StockSearchResult]:
        """
        搜索股票

        Args:
            keyword: 股票代码或名称关键词

        Returns:
            股票搜索结果列表

        Raises:
            Exception: 搜索失败时抛出异常
        """
        try:
            # 使用统一的数据获取工具获取实时行情
            df = stock_data_fetcher.fetch_realtime_data()

            if df is None or df.empty:
                return []

            # 筛选包含关键词的股票
            mask = df["代码"].str.contains(keyword, case=False, na=False) | df[
                "名称"
            ].str.contains(keyword, case=False, na=False)

            filtered_df = df[mask].head(settings.MAX_SEARCH_RESULTS)

            results = []
            for _, row in filtered_df.iterrows():
                results.append(
                    StockSearchResult(
                        code=str(row["代码"]),
                        name=str(row["名称"]),
                        current_price=(
                            float(row["最新价"]) if pd.notna(row["最新价"]) else 0.0
                        ),
                    )
                )

            return results

        except Exception as e:
            raise Exception(f"搜索股票失败: {str(e)}")

    def get_minute_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        获取分钟数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            分钟数据DataFrame

        Raises:
            Exception: 获取数据失败时抛出异常
        """
        try:
            # 获取日期范围内的所有交易日
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            # 使用统一的数据获取工具获取分时数据（前复权）
            df = stock_data_fetcher.fetch_minute_data(
                stock_code=symbol,
                start_date=start_dt,
                end_date=end_dt,
                period="1",
                adjust="qfq",
            )

            if df is not None and not df.empty:
                # 使用统一的数据清洗方法（用于分析）
                df = stock_data_fetcher.clean_minute_data_for_analysis(df)
                return df

            return pd.DataFrame()

        except ValueError:
            raise
        except Exception as e:
            raise Exception(f"获取分钟数据失败: {str(e)}")

    def get_stock_data(
        self, symbol: str, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        """
        获取股票1分钟数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            包含图表数据的字典

        Raises:
            ValueError: 参数验证失败
            Exception: 数据获取失败
        """
        # 验证日期格式
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"日期格式错误: {str(e)}")

        df = self.get_minute_data(symbol, start_date, end_date)

        # 确保datetime列是字符串格式
        df["datetime"] = df["datetime"].astype(str)

        # 转换为图表数据
        chart_data = self.data_processor.to_chart_data(df)

        # 进行高斯混合模型拟合（多峰正态分布拟合）
        fit_result = self.data_processor.fit_gaussian_mixture(chart_data)

        return {
            "success": True,
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "period": "1min",
            "chart_data": chart_data,
            "count": len(chart_data),
            "fit_result": fit_result,  # 添加拟合结果
        }


# 创建全局服务实例
stock_service = StockService()
