"""
股票数据获取工具类
统一管理所有股票数据的获取逻辑，供数据采集和查询服务使用
"""

import akshare as ak
import pandas as pd
from datetime import datetime, date
from typing import Optional
import logging
import time

from app.core.config import settings

try:
    import tushare as ts
except Exception:  # pragma: no cover - 运行时可选依赖
    ts = None

logger = logging.getLogger(__name__)


class StockDataFetcher:
    """股票数据获取器 - 统一的数据获取接口"""

    @staticmethod
    def _to_tushare_code(stock_code: str) -> str:
        code = str(stock_code).zfill(6)
        # 常见上交所代码前缀
        if code.startswith(("5", "6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    @staticmethod
    def _fetch_daily_data_tushare(
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str,
        max_retries: int,
    ) -> Optional[pd.DataFrame]:
        if not settings.TUSHARE_ENABLED:
            return None
        if not settings.TUSHARE_TOKEN:
            logger.warning("Tushare 未配置 token，跳过备源")
            return None
        if ts is None:
            logger.warning("tushare 依赖未安装，跳过备源")
            return None

        ts_code = StockDataFetcher._to_tushare_code(stock_code)
        adj = adjust if adjust in {"qfq", "hfq"} else None

        for attempt in range(1, max_retries + 2):
            try:
                ts.set_token(settings.TUSHARE_TOKEN)
                df = ts.pro_bar(
                    ts_code=ts_code,
                    adj=adj,
                    start_date=start_date,
                    end_date=end_date,
                )
                if df is None or df.empty:
                    return None

                # 统一列结构为后续清洗逻辑可识别格式
                # tushare: trade_date/open/high/low/close/vol/amount
                normalized = pd.DataFrame(
                    {
                        "日期": pd.to_datetime(df["trade_date"], format="%Y%m%d"),
                        "开盘": df["open"],
                        "收盘": df["close"],
                        "最高": df["high"],
                        "最低": df["low"],
                        "成交量": df["vol"],
                        "成交额": df.get("amount"),
                    }
                )
                normalized = normalized.sort_values("日期").reset_index(drop=True)
                logger.info(f"股票 {stock_code} 日线数据由 Tushare 备源返回")
                return normalized
            except Exception as e:
                if attempt <= max_retries:
                    logger.warning(
                        f"Tushare 获取股票 {stock_code} 日线失败(第{attempt}次重试): {str(e)}"
                    )
                    time.sleep(0.8 * attempt)
                    continue
                logger.error(f"Tushare 获取股票 {stock_code} 日线失败: {str(e)}")
                return None

        return None

    @staticmethod
    def fetch_daily_data(
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        max_retries: int = 2,
    ) -> Optional[pd.DataFrame]:
        """
        获取股票日线数据（原始数据，未经处理）

        Args:
            stock_code: 股票代码
            start_date: 开始日期，格式为 "YYYYMMDD"
            end_date: 结束日期，格式为 "YYYYMMDD"
            adjust: 复权类型，""表示不复权，"qfq"表示前复权，"hfq"表示后复权
            max_retries: 网络失败时的最大重试次数

        Returns:
            原始日线数据DataFrame，如果获取失败返回None
        """
        for attempt in range(1, max_retries + 2):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=stock_code,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                )

                if df is None or df.empty:
                    logger.warning(
                        f"股票 {stock_code} 在 {start_date} 到 {end_date} 期间无日线数据"
                    )
                    return None

                return df
            except Exception as e:
                if attempt <= max_retries:
                    logger.warning(
                        f"获取股票 {stock_code} 日线数据失败(第{attempt}次重试): {str(e)}"
                    )
                    time.sleep(0.6 * attempt)
                    continue

                logger.error(f"获取股票 {stock_code} 日线数据失败: {str(e)}")
                break

        # AKShare 主源失败后，尝试 Tushare 备源
        return StockDataFetcher._fetch_daily_data_tushare(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            max_retries=settings.TUSHARE_RETRY_TIMES,
        )

    @staticmethod
    def fetch_minute_data(
        stock_code: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "1",
        adjust: str = "",
        max_retries: int = 2,
    ) -> Optional[pd.DataFrame]:
        """
        获取股票分时数据（原始数据，未经处理）

        Args:
            stock_code: 股票代码
            start_datetime: 开始时间，格式为 "YYYY-MM-DD HH:MM:SS"
            end_datetime: 结束时间，格式为 "YYYY-MM-DD HH:MM:SS"
            period: 时间周期，默认"1"表示1分钟
            adjust: 复权类型，""表示不复权，"qfq"表示前复权，"hfq"表示后复权
            max_retries: 网络失败时的最大重试次数

        Returns:
            原始分时数据DataFrame，如果获取失败返回None
        """
        for attempt in range(1, max_retries + 2):
            try:
                # 调用 AKShare API 获取数据
                df = ak.stock_zh_a_hist_min_em(
                    symbol=stock_code,
                    period=period,
                    start_date=start_datetime,
                    end_date=end_datetime,
                    adjust=adjust,
                )

                if df is None or df.empty:
                    logger.warning(
                        f"股票 {stock_code} 在 {start_datetime} 到 {end_datetime} 期间无数据"
                    )
                    return None

                return df

            except Exception as e:
                if attempt <= max_retries:
                    logger.warning(
                        f"获取股票 {stock_code} 分时数据失败(第{attempt}次重试): {str(e)}"
                    )
                    time.sleep(0.6 * attempt)
                    continue

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
        df: pd.DataFrame, stock_code: str, trade_date: date
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
    def clean_daily_data_for_analysis(df: pd.DataFrame) -> pd.DataFrame:
        """
        清洗日线数据，准备用于分析和回测

        Args:
            df: 原始日线数据DataFrame

        Returns:
            清洗后的DataFrame，包含分析所需的列
        """
        if df is None or df.empty:
            return pd.DataFrame()

        column_mapping = {
            "日期": "datetime",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
        }

        clean_df = df.rename(columns=column_mapping)
        if "datetime" in clean_df.columns:
            clean_df["datetime"] = pd.to_datetime(clean_df["datetime"]).dt.strftime(
                "%Y-%m-%d 00:00:00"
            )

        return clean_df

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

    @staticmethod
    def fetch_cyq_data(stock_code: str, adjust: str = "") -> Optional[pd.DataFrame]:
        """
        获取股票筹码分布数据

        Args:
            stock_code: 股票代码（6位数字）
            adjust: 复权类型，""表示不复权，"qfq"表示前复权，"hfq"表示后复权

        Returns:
            筹码分布DataFrame，如果获取失败返回None
        """
        try:
            # 调用 AKShare API 获取筹码分布数据
            df = ak.stock_cyq_em(symbol=stock_code, adjust=adjust)

            if df is None or df.empty:
                logger.warning(f"股票 {stock_code} 无筹码分布数据")
                return None

            return df

        except Exception as e:
            logger.error(f"获取股票 {stock_code} 筹码分布数据失败: {str(e)}")
            return None


# 创建全局实例
stock_data_fetcher = StockDataFetcher()
