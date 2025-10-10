"""
股票数据服务
负责调用AKShare API获取数据并进行处理
"""
import akshare as ak
import pandas as pd
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from app.schemas.stock import StockSearchResult, ChartDataPoint
from app.utils.data_processor import DataProcessor
from app.utils.trading_calendar import trading_calendar
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
            # 获取A股实时行情数据
            df = ak.stock_zh_a_spot_em()
            
            if df is None or df.empty:
                return []
            
            # 筛选包含关键词的股票
            mask = (
                df['代码'].str.contains(keyword, case=False, na=False) |
                df['名称'].str.contains(keyword, case=False, na=False)
            )
            
            filtered_df = df[mask].head(settings.MAX_SEARCH_RESULTS)
            
            results = []
            for _, row in filtered_df.iterrows():
                results.append(
                    StockSearchResult(
                        code=str(row['代码']),
                        name=str(row['名称']),
                        current_price=float(row['最新价']) if pd.notna(row['最新价']) else 0.0,
                    )
                )
            
            return results
            
        except Exception as e:
            raise Exception(f"搜索股票失败: {str(e)}")
    
    def get_daily_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        获取日线数据
        
        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            日线数据DataFrame
            
        Raises:
            Exception: 获取数据失败时抛出异常
        """
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="qfq"
            )
            
            if df is None or df.empty:
                raise ValueError("未找到该股票数据")
            
            # 清洗数据
            df = self.data_processor.clean_daily_data(df)
            
            return df
            
        except ValueError:
            raise
        except Exception as e:
            raise Exception(f"获取日线数据失败: {str(e)}")
    
    def _fetch_minute_data_single_day(
        self,
        symbol: str,
        date: str,
        period: str = "1"
    ) -> pd.DataFrame:
        """
        获取单日分钟数据
        
        Args:
            symbol: 股票代码
            date: 日期（YYYY-MM-DD）
            period: 数据周期
            
        Returns:
            单日分钟数据DataFrame
        """
        try:
            df = ak.stock_zh_a_hist_min_em(
                symbol=symbol,
                period=period,
                start_date=date.replace("-", "") + " 09:30:00",
                end_date=date.replace("-", "") + " 15:00:00",
                adjust="qfq"
            )
            
            if df is not None and not df.empty:
                df = self.data_processor.clean_minute_data(df)
                return df
            
            return pd.DataFrame()
            
        except Exception as e:
            logger.warning(f"获取 {date} 的分钟数据失败: {str(e)}")
            return pd.DataFrame()
    
    def get_minute_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str
    ) -> pd.DataFrame:
        """
        获取分钟数据（多线程加速）
        
        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            period: 数据周期（只支持1min）
            
        Returns:
            分钟数据DataFrame
            
        Raises:
            Exception: 获取数据失败时抛出异常
        """
        try:
            # 获取日期范围内的所有交易日
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            
            trading_days = trading_calendar.get_trading_days(start_dt, end_dt)
            
            if not trading_days:
                raise ValueError("所选日期范围内没有交易日")
            
            logger.info(f"开始获取 {len(trading_days)} 个交易日的分钟数据")
            
            # 使用线程池并行获取数据
            all_dataframes = []
            
            with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
                # 提交所有任务
                future_to_date = {
                    executor.submit(
                        self._fetch_minute_data_single_day,
                        symbol,
                        day.strftime("%Y-%m-%d"),
                        "1"
                    ): day
                    for day in trading_days
                }
                
                # 收集结果
                for future in as_completed(future_to_date):
                    date = future_to_date[future]
                    try:
                        df = future.result()
                        if not df.empty:
                            all_dataframes.append(df)
                    except Exception as e:
                        logger.error(f"获取 {date} 数据时出错: {str(e)}")
            
            # 合并所有数据
            if not all_dataframes:
                raise ValueError("未获取到任何分钟数据")
            
            df = pd.concat(all_dataframes, ignore_index=True)
            df = df.sort_values('datetime')
            
            logger.info(f"成功获取 {len(df)} 条分钟数据")
            
            return df
            
        except ValueError:
            raise
        except Exception as e:
            raise Exception(f"获取分钟数据失败: {str(e)}")
    
    def get_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily"
    ) -> Dict[str, Any]:
        """
        获取股票数据（统一入口）
        
        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            period: 数据周期
            
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
        
        # 验证日期范围
        max_days = settings.MAX_MINUTE_QUERY_DAYS if period != "daily" else settings.MAX_DAILY_QUERY_DAYS
        try:
            self.data_processor.validate_date_range(start_date, end_date, period, max_days)
        except ValueError as e:
            raise ValueError(str(e))
        
        # 获取数据
        if period == "daily":
            df = self.get_daily_data(symbol, start_date, end_date)
        else:
            df = self.get_minute_data(symbol, start_date, end_date, period)
        
        # 确保datetime列是字符串格式
        df['datetime'] = df['datetime'].astype(str)
        
        # 转换为图表数据
        chart_data = self.data_processor.to_chart_data(df)
        
        return {
            "success": True,
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "period": period,
            "chart_data": chart_data,
            "count": len(chart_data)
        }


# 创建全局服务实例
stock_service = StockService()

