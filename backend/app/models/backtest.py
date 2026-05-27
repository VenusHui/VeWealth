"""回测运行记录模型"""

from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Date,
    Float,
    Text,
    JSON,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="completed", index=True)

    strategy_id = Column(String(64), nullable=False)
    strategy_params = Column(JSON, nullable=False, default=dict)
    symbols = Column(JSON, nullable=False, default=list)

    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    initial_cash = Column(Float, nullable=False)
    benchmark = Column(String(10), nullable=True)

    cost_config = Column(JSON, nullable=False, default=dict)
    summary = Column(JSON, nullable=True)
    equity_curve = Column(JSON, nullable=False, default=list)
    trades = Column(JSON, nullable=False, default=list)
    warnings = Column(JSON, nullable=False, default=list)

    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user = relationship("User", back_populates="backtest_runs")
    rounds = relationship(
        "BacktestRound", back_populates="run", cascade="all, delete-orphan"
    )


class BacktestRound(Base):
    __tablename__ = "backtest_rounds"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    run_id = Column(
        Integer,
        ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    symbol = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100), nullable=True)

    open_time = Column(String(32), nullable=True)
    open_price = Column(Float, nullable=True)
    close_time = Column(String(32), nullable=True)
    close_price = Column(Float, nullable=True)
    qty = Column(Float, nullable=False, default=0)

    holding_days = Column(Integer, nullable=True)
    pnl_amount = Column(Float, nullable=False, default=0)
    pnl_ratio = Column(Float, nullable=False, default=0)
    exit_reason = Column(String(128), nullable=True)

    max_favorable_excursion = Column(Float, nullable=True)
    max_adverse_excursion = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    run = relationship("BacktestRun", back_populates="rounds")
