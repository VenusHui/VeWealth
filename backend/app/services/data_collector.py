"""
数据采集服务
"""
import akshare as ak
import pandas as pd
from datetime import datetime, date
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.models.stock_data import StockMinuteData
from app.models.watchlist import WatchList
from app.core.config import settings


class DataCollector:
    """数据采集器"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def collect_minute_data(self, stock_code: str, trade_date: date) -> int:
        """
        采集单个股票的分时数据
        
        Args:
            stock_code: 股票代码
            trade_date: 交易日期
        
        Returns:
            采集的数据条数
        """
        try:
            # 获取分时数据
            df = ak.stock_zh_a_hist_min_em(
                symbol=stock_code,
                period="1",
                start_date=trade_date.strftime("%Y-%m-%d"),
                end_date=trade_date.strftime("%Y-%m-%d"),
                adjust=""
            )
            
            if df is None or df.empty:
                return 0
            
            # 数据清洗
            df = df.rename(columns={
                '时间': 'trade_time',
                '开盘': 'open_price',
                '收盘': 'close_price',
                '最高': 'high_price',
                '最低': 'low_price',
                '成交量': 'volume'
            })
            
            # 转换时间格式
            df['trade_time'] = pd.to_datetime(df['trade_time'])
            df['trade_date'] = trade_date
            df['stock_code'] = stock_code
            
            # 批量插入数据库
            count = 0
            for _, row in df.iterrows():
                # 检查是否已存在
                existing = self.db.query(StockMinuteData).filter(
                    and_(
                        StockMinuteData.stock_code == stock_code,
                        StockMinuteData.trade_time == row['trade_time']
                    )
                ).first()
                
                if not existing:
                    data_point = StockMinuteData(
                        stock_code=stock_code,
                        trade_date=trade_date,
                        trade_time=row['trade_time'],
                        open_price=float(row['open_price']),
                        high_price=float(row['high_price']),
                        low_price=float(row['low_price']),
                        close_price=float(row['close_price']),
                        volume=float(row['volume'])
                    )
                    self.db.add(data_point)
                    count += 1
            
            self.db.commit()
            return count
            
        except Exception as e:
            print(f"采集股票 {stock_code} 数据失败: {str(e)}")
            self.db.rollback()
            return 0
    
    def collect_all_watchlist_stocks(self, trade_date: Optional[date] = None) -> dict:
        """
        采集所有监控列表中的股票数据
        
        Args:
            trade_date: 交易日期，默认为今天
        
        Returns:
            采集结果统计
        """
        if trade_date is None:
            trade_date = date.today()
        
        # 获取所有不重复的股票代码
        stock_codes = self.db.query(WatchList.stock_code).distinct().all()
        stock_codes = [code[0] for code in stock_codes]
        
        results = {
            'total_stocks': len(stock_codes),
            'success_count': 0,
            'fail_count': 0,
            'total_records': 0,
            'details': []
        }
        
        for stock_code in stock_codes:
            count = self.collect_minute_data(stock_code, trade_date)
            if count > 0:
                results['success_count'] += 1
                results['total_records'] += count
                results['details'].append({
                    'stock_code': stock_code,
                    'records': count,
                    'status': 'success'
                })
            else:
                results['fail_count'] += 1
                results['details'].append({
                    'stock_code': stock_code,
                    'records': 0,
                    'status': 'failed'
                })
        
        return results
    
    def get_historical_data(
        self,
        stock_code: str,
        start_date: date,
        end_date: date
    ) -> List[StockMinuteData]:
        """
        获取历史分时数据
        
        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
        
        Returns:
            分时数据列表
        """
        data = self.db.query(StockMinuteData).filter(
            and_(
                StockMinuteData.stock_code == stock_code,
                StockMinuteData.trade_date >= start_date,
                StockMinuteData.trade_date <= end_date
            )
        ).order_by(StockMinuteData.trade_time).all()
        
        return data

