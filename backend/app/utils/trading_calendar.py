"""
A股交易日历工具
"""
from datetime import datetime, timedelta
from typing import List
import chinese_calendar as calendar


class TradingCalendar:
    """A股交易日历"""
    
    @staticmethod
    def is_trading_day(date: datetime) -> bool:
        """
        判断是否为交易日
        
        Args:
            date: 日期
            
        Returns:
            是否为交易日
        """
        # 检查是否为工作日且不是节假日
        return calendar.is_workday(date)
    
    @staticmethod
    def get_trading_days(start_date: datetime, end_date: datetime) -> List[datetime]:
        """
        获取日期范围内的所有交易日
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            交易日列表
        """
        trading_days = []
        current_date = start_date
        
        while current_date <= end_date:
            if TradingCalendar.is_trading_day(current_date):
                trading_days.append(current_date)
            current_date += timedelta(days=1)
        
        return trading_days
    
    @staticmethod
    def get_previous_trading_day(date: datetime) -> datetime:
        """
        获取前一个交易日
        
        Args:
            date: 基准日期
            
        Returns:
            前一个交易日
        """
        previous_date = date - timedelta(days=1)
        
        while not TradingCalendar.is_trading_day(previous_date):
            previous_date -= timedelta(days=1)
        
        return previous_date
    
    @staticmethod
    def get_next_trading_day(date: datetime) -> datetime:
        """
        获取下一个交易日
        
        Args:
            date: 基准日期
            
        Returns:
            下一个交易日
        """
        next_date = date + timedelta(days=1)
        
        while not TradingCalendar.is_trading_day(next_date):
            next_date += timedelta(days=1)
        
        return next_date
    
    @staticmethod
    def get_recent_trading_days(days: int = 10) -> List[str]:
        """
        获取最近N个交易日
        
        Args:
            days: 天数
            
        Returns:
            交易日列表（字符串格式 YYYY-MM-DD）
        """
        today = datetime.now()
        trading_days = []
        current_date = today
        
        while len(trading_days) < days:
            if TradingCalendar.is_trading_day(current_date):
                trading_days.append(current_date.strftime("%Y-%m-%d"))
            current_date -= timedelta(days=1)
        
        return list(reversed(trading_days))


# 创建全局实例
trading_calendar = TradingCalendar()

