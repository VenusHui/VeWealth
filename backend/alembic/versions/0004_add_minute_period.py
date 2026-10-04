"""add period to stock_minute_data and widen the unique key (VEW-64)

Revision ID: 0004_add_minute_period
Revises: 0003_add_universe_snapshots
Create Date: 2026-10-04

分钟级回测 P0：`stock_minute_data` 补 `period` 列，并把唯一键从
`(stock_code, trade_time)` 扩到 `(stock_code, period, trade_time)`。

为什么必须改键：1min 与 5min 在 09:35 这类时点上 `trade_time` 相同，旧键会把两个
周期的 bar 判成重复行，批量 upsert 时互相覆盖。写入侧按 period 参数化后，唯一键
必须带上周期。

存量数据全部按 1 分钟粒度采集，`server_default='1'` 直接回填，无需数据迁移。
新增 `(period, trade_date)` 复合索引，供采集断点续采与在线按周期回看使用。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_add_minute_period"
down_revision: Union[str, None] = "0003_add_universe_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "stock_minute_data",
        sa.Column(
            "period",
            sa.String(length=8),
            nullable=False,
            server_default="1",
            comment="K线周期（分钟）：1/5/15/30/60",
        ),
    )
    op.create_index(
        "idx_minute_period_date",
        "stock_minute_data",
        ["period", "trade_date"],
        unique=False,
    )
    # 顺序说明：新键 (stock_code, period, trade_time) 是旧键 (stock_code, trade_time) 的
    # **更细划分** —— period 有 server_default '1' 回填，旧键的每一行都唯一映射到一个
    # 新键，所以先建新键也不会冲突。这里按「先删后建」写只是让语义读起来是替换。
    # 真正对顺序敏感的是 downgrade：从细键退回粗键时，若表内已存在同一 trade_time 的
    # 多周期数据，建旧唯一键会失败（故 downgrade 前必须先清理多周期数据）。
    op.drop_index("idx_unique_data", table_name="stock_minute_data")
    op.create_index(
        "idx_unique_data",
        "stock_minute_data",
        ["stock_code", "period", "trade_time"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_unique_data", table_name="stock_minute_data")
    op.create_index(
        "idx_unique_data",
        "stock_minute_data",
        ["stock_code", "trade_time"],
        unique=True,
    )
    op.drop_index("idx_minute_period_date", table_name="stock_minute_data")
    op.drop_column("stock_minute_data", "period")
