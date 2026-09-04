"""add universe_snapshots: dated point-in-time stock pool (VEW-26)

Revision ID: 0003_add_universe_snapshots
Revises: 0002_add_screener_jobs
Create Date: 2026-09-04

新增 `universe_snapshots` 表，按 snapshot_date 保存一份带日期的股票池状态
（板块 / ST / 上市退市），供回测按 as_of 选池，消除幸存者偏差。security_universe
维表仍是"当前"状态；本表累积历史快照。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_add_universe_snapshots"
down_revision: Union[str, None] = "0002_add_screener_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "universe_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("stock_code", sa.String(length=16), nullable=False),
        sa.Column("stock_name", sa.String(length=64), nullable=True),
        sa.Column("market", sa.String(length=16), nullable=True),
        sa.Column("board", sa.String(length=16), nullable=False),
        sa.Column("is_st", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("list_date", sa.Date(), nullable=True),
        sa.Column("delist_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_universe_snapshots_snapshot_date",
        "universe_snapshots",
        ["snapshot_date"],
        unique=False,
    )
    op.create_index(
        "ix_universe_snapshots_stock_code",
        "universe_snapshots",
        ["stock_code"],
        unique=False,
    )
    op.create_index(
        "idx_universe_snapshot_date_board_st",
        "universe_snapshots",
        ["snapshot_date", "board", "is_st"],
        unique=False,
    )
    op.create_index(
        "idx_universe_snapshot_code_date",
        "universe_snapshots",
        ["stock_code", "snapshot_date"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_universe_snapshot_code_date", table_name="universe_snapshots")
    op.drop_index(
        "idx_universe_snapshot_date_board_st", table_name="universe_snapshots"
    )
    op.drop_index("ix_universe_snapshots_stock_code", table_name="universe_snapshots")
    op.drop_index(
        "ix_universe_snapshots_snapshot_date", table_name="universe_snapshots"
    )
    op.drop_table("universe_snapshots")
