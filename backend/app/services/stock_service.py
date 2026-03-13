"""
股票数据服务
负责调用AKShare API获取数据并进行处理
"""

import pandas as pd
from typing import List, Dict, Any, Tuple
from datetime import datetime
import logging
from sqlalchemy.orm import Session

from app.schemas.stock import StockSearchResult
from app.utils.data_processor import DataProcessor
from app.utils.stock_data_fetcher import stock_data_fetcher
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.stock_data import StockMinuteData

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

    def get_daily_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> Tuple[pd.DataFrame, str, str]:
        """
        获取日线数据（用于回测）

        Args:
            symbol: 股票代码
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        Returns:
            (日线数据DataFrame, 实际开始日期, 实际结束日期)
        """
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            api_df = stock_data_fetcher.fetch_daily_data(
                stock_code=symbol,
                start_date=start_dt.strftime("%Y%m%d"),
                end_date=end_dt.strftime("%Y%m%d"),
                adjust="qfq",
            )

            if api_df is None or api_df.empty:
                return (
                    pd.DataFrame(
                        columns=["datetime", "open", "high", "low", "close", "volume"]
                    ),
                    start_date,
                    end_date,
                )

            result_df = stock_data_fetcher.clean_daily_data_for_analysis(api_df)
            if result_df.empty or "datetime" not in result_df.columns:
                return (
                    pd.DataFrame(
                        columns=["datetime", "open", "high", "low", "close", "volume"]
                    ),
                    start_date,
                    end_date,
                )

            actual_start = result_df["datetime"].min()
            actual_end = result_df["datetime"].max()
            return result_df, actual_start, actual_end

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"获取日线数据失败: {str(e)}")
            return (
                pd.DataFrame(
                    columns=["datetime", "open", "high", "low", "close", "volume"]
                ),
                start_date,
                end_date,
            )

    def get_minute_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> Tuple[pd.DataFrame, str, str]:
        """
        获取分钟数据，优先从数据库获取历史数据，然后从API获取最近数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            (分钟数据DataFrame, 实际开始日期, 实际结束日期)

        Raises:
            Exception: 获取数据失败时抛出异常
        """
        try:
            # 获取日期范围
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            # 设置交易时间范围（09:00:00 到 16:00:00）
            start_datetime_str = start_dt.strftime("%Y-%m-%d 09:00:00")
            end_datetime_str = end_dt.strftime("%Y-%m-%d 16:00:00")

            # 1. 从数据库查询历史数据
            db = SessionLocal()
            try:
                db_records = (
                    db.query(StockMinuteData)
                    .filter(
                        StockMinuteData.stock_code == symbol,
                        StockMinuteData.trade_date >= start_dt.date(),
                        StockMinuteData.trade_date <= end_dt.date(),
                    )
                    .order_by(StockMinuteData.trade_time)
                    .all()
                )

                # 转换数据库记录为DataFrame
                db_data = []
                if db_records:
                    for record in db_records:
                        db_data.append(
                            {
                                "datetime": record.trade_time.strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                                "open": record.open_price,
                                "high": record.high_price,
                                "low": record.low_price,
                                "close": record.close_price,
                                "volume": record.volume,
                            }
                        )
                    logger.info(
                        f"从数据库获取到 {len(db_data)} 条历史数据，股票: {symbol}"
                    )

                db_df = pd.DataFrame(db_data) if db_data else pd.DataFrame()

            finally:
                db.close()

            # 2. 使用统一的数据获取工具获取分时数据（前复权）- 最近5个交易日
            api_df = stock_data_fetcher.fetch_minute_data(
                stock_code=symbol,
                start_datetime=start_datetime_str,
                end_datetime=end_datetime_str,
                period="1",
                adjust="qfq",
            )

            if api_df is not None and not api_df.empty:
                # 使用统一的数据清洗方法（用于分析）
                api_df = stock_data_fetcher.clean_minute_data_for_analysis(api_df)
                logger.info(f"从API获取到 {len(api_df)} 条数据，股票: {symbol}")
            else:
                api_df = pd.DataFrame()

            # 3. 合并数据库数据和API数据
            if not db_df.empty and not api_df.empty:
                # 合并两个DataFrame
                combined_df = pd.concat([db_df, api_df], ignore_index=True)
                # 去重（基于datetime列）
                combined_df = combined_df.drop_duplicates(
                    subset=["datetime"], keep="last"
                )
                # 按时间排序
                combined_df = combined_df.sort_values("datetime").reset_index(drop=True)
                logger.info(f"合并后共 {len(combined_df)} 条数据，股票: {symbol}")
                result_df = combined_df
            elif not db_df.empty:
                result_df = db_df
            elif not api_df.empty:
                result_df = api_df
            else:
                result_df = pd.DataFrame(
                    columns=["datetime", "open", "high", "low", "close", "volume"]
                )

            # 4. 计算实际的时间范围
            if result_df.empty or "datetime" not in result_df.columns:
                return result_df, start_datetime_str, end_datetime_str

            actual_start = result_df["datetime"].min()
            actual_end = result_df["datetime"].max()

            return result_df, actual_start, actual_end

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"获取分钟数据失败: {str(e)}")
            return (
                pd.DataFrame(
                    columns=["datetime", "open", "high", "low", "close", "volume"]
                ),
                start_date,
                end_date,
            )

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

        df, actual_start_date, actual_end_date = self.get_minute_data(
            symbol, start_date, end_date
        )

        # 确保datetime列是字符串格式
        if not df.empty:
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
            "actual_start_date": actual_start_date,
            "actual_end_date": actual_end_date,
            "period": "1min",
            "chart_data": chart_data,
            "count": len(chart_data),
            "fit_result": fit_result,  # 添加拟合结果
        }

    def get_cyq_data(self, symbol: str, adjust: str = "") -> Dict[str, Any]:
        """
        获取股票筹码分布数据

        Args:
            symbol: 股票代码
            adjust: 复权类型，""表示不复权，"qfq"表示前复权，"hfq"表示后复权

        Returns:
            包含筹码分布数据的字典

        Raises:
            Exception: 数据获取失败
        """
        try:
            # 使用统一的数据获取工具获取筹码分布数据
            df = stock_data_fetcher.fetch_cyq_data(stock_code=symbol, adjust=adjust)

            if df is None or df.empty:
                raise Exception(f"未找到股票 {symbol} 的筹码分布数据")

            # akshare返回的是按日期的时间序列数据
            # 列名：日期, 获利比例, 平均成本, 90成本-低, 90成本-高, 90集中度, 70成本-低, 70成本-高, 70集中度

            # 获取最新的一条数据（最后一行）
            latest_data = df.iloc[-1]

            # 提取关键信息
            cyq_info = {
                "date": str(latest_data.get("日期", "")),
                "profit_ratio": float(latest_data.get("获利比例", 0)),
                "avg_cost": float(latest_data.get("平均成本", 0)),
                "cost_90_low": float(latest_data.get("90成本-低", 0)),
                "cost_90_high": float(latest_data.get("90成本-高", 0)),
                "concentration_90": float(latest_data.get("90集中度", 0)),
                "cost_70_low": float(latest_data.get("70成本-低", 0)),
                "cost_70_high": float(latest_data.get("70成本-高", 0)),
                "concentration_70": float(latest_data.get("70集中度", 0)),
            }

            return {
                "success": True,
                "symbol": symbol,
                "adjust": adjust,
                "cyq_info": cyq_info,
            }

        except Exception as e:
            logger.error(f"获取筹码分布数据失败: {str(e)}")
            raise Exception(f"获取筹码分布数据失败: {str(e)}")


# 创建全局服务实例
stock_service = StockService()
