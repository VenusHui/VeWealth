"""
股票分时数据模型
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Index
from app.core.database import Base

# 在线分时表的默认周期（分钟）。现有读路径（分时图 / 告警 / 选股信号）消费的都是
# 1 分钟粒度；写入侧按 period 参数化后，读路径必须显式限定周期，否则多周期数据
# 会混在同一段序列里（VEW-64）。
DEFAULT_MINUTE_PERIOD = "1"


class StockMinuteData(Base):
    """股票分时数据表。

    本表是**在线查询**用的近期窗口（分时图 / 告警 / 选股信号），数据量小、按
    (stock_code, period, trade_time) 唯一定位。全市场跨年归档走本地分钟库
    （`app/services/minute_store.py` 的 Parquet 分区），不写进本表 —— 详见
    VEW-64 的存储选型说明。
    """

    __tablename__ = "stock_minute_data"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False, comment="股票代码")
    period = Column(
        String(8),
        nullable=False,
        default=DEFAULT_MINUTE_PERIOD,
        server_default=DEFAULT_MINUTE_PERIOD,
        comment="K线周期（分钟）：1/5/15/30/60",
    )
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
        # 唯一索引：同一标的、同一周期、同一时间点只允许一行 —— 批量 upsert 的冲突键。
        # period 必须进键：1min 与 5min 在 09:35 这类时点上 trade_time 相同（VEW-64）。
        Index("idx_unique_data", "stock_code", "period", "trade_time", unique=True),
        # 按周期扫描某个交易日 / 日期区间（采集断点续采与在线回看）
        Index("idx_minute_period_date", "period", "trade_date"),
    )

    def __repr__(self):
        return f"<StockMinuteData(stock_code={self.stock_code}, period={self.period}, trade_time={self.trade_time}, close={self.close_price})>"
