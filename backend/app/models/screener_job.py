"""选股扫描任务模型 — 持久化扫描进度与结果，可供刷新/重启后恢复。

背景：screener_service 此前把任务/结果放在进程字典里，刷新/重启即丢，
且无法跨进程访问。此模型把每次扫描固化为数据库行，配合有界后台执行器
与取消标志，实现可恢复、可取消、按用户隔离的全市场选股任务。
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class ScreenerJob(Base):
    __tablename__ = "screener_jobs"
    # 同用户只允许一个 active（pending/running）扫描任务。部分唯一索引在数据库层
    # 兜底 start_scan 的查找-插入 TOCTOU；配合服务层捕获 IntegrityError 后重查，
    # 使并发请求不会各自插入一条 active 扫描。
    __table_args__ = (
        Index(
            "uq_screener_jobs_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    scan_id = Column(String(32), unique=True, index=True, nullable=False)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    strategy_id = Column(String(64), nullable=False, index=True)
    strategy_name = Column(String(128), nullable=True)
    strategy_params = Column(JSON, nullable=False, default=dict)
    boards = Column(JSON, nullable=False, default=list)
    exclude_st = Column(Integer, nullable=False, default=1)

    status = Column(String(20), nullable=False, default="pending", index=True)
    stage = Column(String(50), nullable=False, default="pending")
    progress = Column(JSON, nullable=False, default=dict)  # {total, scanned, hits}
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    cancel_requested = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User")
