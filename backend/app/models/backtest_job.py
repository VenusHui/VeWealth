"""回测离线任务模型"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Float
from sqlalchemy.orm import relationship

from app.core.database import Base


class BacktestJob(Base):
    __tablename__ = "backtest_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(String(32), unique=True, index=True, nullable=False)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status = Column(String(20), nullable=False, default="pending", index=True)
    stage = Column(String(50), nullable=False, default="pending")
    progress_pct = Column(Float, nullable=False, default=0)
    total_symbols = Column(Integer, nullable=False, default=0)
    processed_symbols = Column(Integer, nullable=False, default=0)
    eta_seconds = Column(Integer, nullable=True)

    request_payload = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    cancel_requested = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user = relationship("User")
