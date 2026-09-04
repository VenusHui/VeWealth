"""Point-in-time 股票池快照模型。

security_universe 是"当前"基础维表：is_active / is_st 反映最新信息，无法回答
"某历史交易时刻该股票是否在池中、是否 ST"。本表按 snapshot_date 落一份带日期的
股票池状态，回测按 as_of 选池即可消除幸存者偏差：

- snapshot_date 是状态生效日；
- 某股票在 as_of 时刻是否可交易，由 list_date / delist_date 判断；
- board / is_st 表示该股票在 snapshot_date 当日所属板块与是否为 ST。

说明：is_st 为历史事实，只能靠每日快照不断累积；数据库为空时回测会显式降级，
不会把"当前 ST 状态"冒充为历史点状态。
"""

from sqlalchemy import Boolean, Column, Date, Integer, String, Index
from app.core.database import Base


class UniverseSnapshot(Base):
    __tablename__ = "universe_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(16), nullable=False, index=True)
    stock_name = Column(String(64), nullable=True)
    market = Column(String(16), nullable=True)
    board = Column(String(16), nullable=False)
    is_st = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    list_date = Column(Date, nullable=True)
    delist_date = Column(Date, nullable=True)
    source = Column(String(32), nullable=True)

    __table_args__ = (
        # 便于"取某日、某板块、是否 ST"的股票池查询
        Index(
            "idx_universe_snapshot_date_board_st",
            "snapshot_date",
            "board",
            "is_st",
        ),
        # 每只股票从最新到最早的快照历史
        Index("idx_universe_snapshot_code_date", "stock_code", "snapshot_date"),
    )

    def __repr__(self):
        return (
            f"<UniverseSnapshot(date={self.snapshot_date}, "
            f"code={self.stock_code}, board={self.board}, st={self.is_st})>"
        )
