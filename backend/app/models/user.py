"""
用户模型
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class User(Base):
    """用户表"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(
        String(50), unique=True, index=True, nullable=False, comment="用户名"
    )
    hashed_password = Column(String(255), nullable=False, comment="哈希密码")
    wechat_openid = Column(
        String(100), unique=True, index=True, nullable=True, comment="微信OpenID"
    )
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")
    alert_threshold = Column(
        Float, default=0.7, nullable=False, comment="预警阈值（0-1）"
    )
    created_at = Column(
        DateTime, default=datetime.utcnow, nullable=False, comment="创建时间"
    )
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间",
    )

    # 关联
    watchlists = relationship(
        "WatchList", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username})>"
