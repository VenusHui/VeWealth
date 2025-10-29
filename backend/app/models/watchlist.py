"""
监控列表模型
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.core.database import Base


class WatchList(Base):
    """监控列表表"""
    __tablename__ = "watchlists"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True, comment="用户ID")
    stock_code = Column(String(10), nullable=False, index=True, comment="股票代码")
    stock_name = Column(String(50), nullable=True, comment="股票名称")
    alert_enabled = Column(Boolean, default=True, nullable=False, comment="是否启用预警")
    alert_threshold = Column(Float, nullable=True, comment="个性化预警阈值（null则使用用户默认值）")
    last_alerted_at = Column(DateTime, nullable=True, comment="最后预警时间")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, comment="更新时间")
    
    # 关联
    user = relationship("User", back_populates="watchlists")
    
    # 唯一约束：同一用户不能重复添加同一股票
    __table_args__ = (
        UniqueConstraint('user_id', 'stock_code', name='uq_user_stock'),
    )
    
    def __repr__(self):
        return f"<WatchList(id={self.id}, user_id={self.user_id}, stock_code={self.stock_code})>"

