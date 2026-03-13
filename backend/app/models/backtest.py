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
