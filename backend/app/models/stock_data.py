"""
股票分时数据模型
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Index
from app.core.database import Base


class StockMinuteData(Base):
    """股票分时数据表"""

    __tablename__ = "stock_minute_data"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False, comment="股票代码")
    trade_date = Column(Date, nullable=False, comment="交易日期")
    trade_time = Column(DateTime, nullable=False, comment="交易时间")

    # OHLCV数据
    open_price = Column(Float, nullable=False, comment="开盘价")
    high_price = Column(Float, nullable=False, comment="最高价")
    low_price = Column(Float, nullable=False, comment="最低价")
    close_price = Column(Float, nullable=False, comment="收盘价")
    volume = Column(Float, nullable=False, comment="成交量")

    created_at = Column(
        DateTime, default=datetime.utcnow, nullable=False, comment="创建时间"
    )

    # 索引
    __table_args__ = (
        # 复合索引：股票代码+交易日期，用于快速查询特定股票的历史数据
        Index("idx_stock_date", "stock_code", "trade_date"),
        # 复合索引：股票代码+交易时间，用于快速查询特定股票的时间序列
        Index("idx_stock_time", "stock_code", "trade_time"),
        # 唯一索引：防止重复数据
        Index("idx_unique_data", "stock_code", "trade_time", unique=True),
    )

    def __repr__(self):
        return f"<StockMinuteData(stock_code={self.stock_code}, trade_time={self.trade_time}, close={self.close_price})>"
