"""
数据处理工具
负责数据的清洗、转换、聚合等操作
"""
import pandas as pd
from typing import Dict, List, Any


class DataProcessor:
    """数据处理器"""
    
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
