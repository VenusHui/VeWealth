"""
数据处理工具
负责数据的清洗、转换、聚合等操作
"""
import pandas as pd
from typing import Dict, List, Any


class DataProcessor:
    """数据处理器"""
    
    @staticmethod
    def resample_to_3min(df: pd.DataFrame) -> pd.DataFrame:
        """
        将1分钟数据重采样为3分钟数据
        
        Args:
            df: 包含OHLC数据的DataFrame，需要有datetime索引
            
        Returns:
            重采样后的DataFrame
        """
        if df.empty:
            return df
        
        # 确保datetime列是索引
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
            df.set_index('datetime', inplace=True)
        
        # 重采样到3分钟
        df_resampled = pd.DataFrame()
        df_resampled['open'] = df['open'].resample('3T').first()
        df_resampled['high'] = df['high'].resample('3T').max()
        df_resampled['low'] = df['low'].resample('3T').min()
        df_resampled['close'] = df['close'].resample('3T').last()
        df_resampled['volume'] = df['volume'].resample('3T').sum()
        
        if 'amount' in df.columns:
            df_resampled['amount'] = df['amount'].resample('3T').sum()
        
        # 重置索引并删除空值
        df_result = df_resampled.reset_index()
        df_result = df_result.dropna()
        
        return df_result
    
    @staticmethod
    def clean_daily_data(df: pd.DataFrame) -> pd.DataFrame:
        """
        清洗日线数据
        
        Args:
            df: 原始日线数据DataFrame
            
        Returns:
            清洗后的DataFrame
        """
        if df is None or df.empty:
            return pd.DataFrame()
        
        # 重命名列
        column_mapping = {
            '日期': 'datetime',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
        }
        
        df = df.rename(columns=column_mapping)
        
        # 确保datetime列是字符串格式
        df['datetime'] = df['datetime'].astype(str)
        
        return df
    
    @staticmethod
    def clean_minute_data(df: pd.DataFrame) -> pd.DataFrame:
        """
        清洗分钟数据
        
        Args:
            df: 原始分钟数据DataFrame
            
        Returns:
            清洗后的DataFrame
        """
        if df is None or df.empty:
            return pd.DataFrame()
        
        # 重命名列
        column_mapping = {
            '时间': 'datetime',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
        }
        
        df = df.rename(columns=column_mapping)
        
        return df
    
    @staticmethod
    def to_chart_data(df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        将DataFrame转换为图表数据格式
        
        Args:
            df: 包含OHLC数据的DataFrame
            
        Returns:
            图表数据列表
        """
        if df.empty:
            return []
        
        chart_data = []
        for _, row in df.iterrows():
            chart_data.append({
                'datetime': str(row.get('datetime', '')),
                'price': float(row.get('close', 0)),
                'volume': float(row.get('volume', 0)),
                'open': float(row.get('open', 0)),
                'high': float(row.get('high', 0)),
                'low': float(row.get('low', 0)),
            })
        
        return chart_data
    
    @staticmethod
    def validate_date_range(start_date: str, end_date: str, period: str, max_days: int) -> None:
        """
        验证日期范围是否合理
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            period: 数据周期
            max_days: 最大天数限制
            
        Raises:
            ValueError: 日期范围不合理时抛出异常
        """
        from datetime import datetime
        
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        
        if start_dt > end_dt:
            raise ValueError("开始日期不能晚于结束日期")
        
        days_diff = (end_dt - start_dt).days
        
        if period != 'daily' and days_diff > max_days:
            raise ValueError(f"分钟级数据查询范围不能超过{max_days}天")

