"""股票基础维表"""

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Index
from sqlalchemy.sql import func

from app.core.database import Base


class SecurityUniverse(Base):
    __tablename__ = "security_universe"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String(16), unique=True, nullable=False, index=True)
    stock_name = Column(String(64), nullable=True)
    market = Column(String(16), nullable=True)
    board = Column(String(16), nullable=False, index=True)
    is_st = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    list_date = Column(Date, nullable=True)
    delist_date = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_security_universe_board_st", "board", "is_st"),
        Index("idx_security_universe_active", "is_active"),
    )
