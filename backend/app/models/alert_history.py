"""
预警历史模型
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from app.core.database import Base


class AlertHistory(Base):
    """预警触发历史记录表"""

    __tablename__ = "alert_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="用户ID",
    )
    stock_code = Column(String(10), nullable=False, index=True, comment="股票代码")
    stock_name = Column(String(50), nullable=True, comment="股票名称")
    alert_threshold = Column(Float, nullable=True, comment="触发时的阈值")
    current_price = Column(Float, nullable=False, comment="触发时的价格")
    change_pct = Column(Float, nullable=True, comment="触发时的涨跌幅")
    alert_direction = Column(String(4), nullable=True, comment="预警方向: buy / sell")
    density_value = Column(Float, nullable=True, comment="触发时的密度百分位(0-1)")
    peak_price = Column(Float, nullable=True, comment="触发时最近的GMM峰值价格")
    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
        comment="触发时间",
    )

    def __repr__(self):
        return f"<AlertHistory(id={self.id}, user_id={self.user_id}, stock_code={self.stock_code})>"
