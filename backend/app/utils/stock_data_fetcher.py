"""
股票数据获取工具类
统一管理所有股票数据的获取逻辑，供数据采集和查询服务使用
"""

import akshare as ak
import pandas as pd
from datetime import datetime, date
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class StockDataFetcher:
    """股票数据获取器 - 统一的数据获取接口"""

    @staticmethod
    def fetch_minute_data(
        stock_code: str,
        start_date: date,
        end_date: Optional[date] = None,
        period: str = "1",
        adjust: str = "",
    ) -> Optional[pd.DataFrame]:
        """
        获取股票分时数据（原始数据，未经处理）

        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期，如果为None则使用start_date
            period: 时间周期，默认"1"表示1分钟
            adjust: 复权类型，""表示不复权，"qfq"表示前复权，"hfq"表示后复权

        Returns:
            原始分时数据DataFrame，如果获取失败返回None
        """
        try:
            if end_date is None:
                end_date = start_date

            # 转换日期格式
            start_date_str = start_date.strftime("%Y-%m-%d") if isinstance(start_date, date) else start_date
            end_date_str = end_date.strftime("%Y-%m-%d") if isinstance(end_date, date) else end_date

            # 调用 AKShare API 获取数据
            df = ak.stock_zh_a_hist_min_em(
                symbol=stock_code,
                period=period,
                start_date=start_date_str,
                end_date=end_date_str,
                adjust=adjust,
            )

            if df is None or df.empty:
                logger.warning(f"股票 {stock_code} 在 {start_date_str} 到 {end_date_str} 期间无数据")
                return None

            return df

        except Exception as e:
            logger.error(f"获取股票 {stock_code} 分时数据失败: {str(e)}")
            return None

    @staticmethod
    def fetch_realtime_data() -> Optional[pd.DataFrame]:
        """
        获取A股实时行情数据

        Returns:
            实时行情DataFrame，如果获取失败返回None
        """
        try:
            df = ak.stock_zh_a_spot_em()
            
            if df is None or df.empty:
                logger.warning("获取实时行情数据为空")
                return None

            return df

        except Exception as e:
            logger.error(f"获取实时行情数据失败: {str(e)}")
            return None

    @staticmethod
    def clean_minute_data_for_storage(
        df: pd.DataFrame,
        stock_code: str,
        trade_date: date
    ) -> pd.DataFrame:
        """
        清洗分时数据，准备存储到数据库
        
        Args:
            df: 原始分时数据DataFrame
            stock_code: 股票代码
            trade_date: 交易日期
            
        Returns:
            清洗后的DataFrame，包含数据库所需的列
        """
        if df is None or df.empty:
            return pd.DataFrame()

        # 重命名列为数据库字段名
        df = df.rename(
            columns={
                "时间": "trade_time",
                "开盘": "open_price",
                "收盘": "close_price",
                "最高": "high_price",
                "最低": "low_price",
                "成交量": "volume",
            }
        )

        # 转换时间格式
        df["trade_time"] = pd.to_datetime(df["trade_time"])
        df["trade_date"] = trade_date
        df["stock_code"] = stock_code

        return df

    @staticmethod
    def clean_minute_data_for_analysis(df: pd.DataFrame) -> pd.DataFrame:
        """
        清洗分时数据，准备用于分析和展示
        
        Args:
            df: 原始分时数据DataFrame
            
        Returns:
            清洗后的DataFrame，包含分析所需的列
        """
        if df is None or df.empty:
            return pd.DataFrame()

        # 重命名列为分析字段名
        column_mapping = {
            "时间": "datetime",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
        }

        df = df.rename(columns=column_mapping)

        return df


# 创建全局实例
stock_data_fetcher = StockDataFetcher()

