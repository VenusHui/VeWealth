"""add screener_jobs: persistent stock-screening jobs (VEW-25)

Revision ID: 0002_add_screener_jobs
Revises: 0001_initial_baseline
Create Date: 2026-09-03

把选股扫描任务从进程内存字典迁移到数据库持久化，支持进度/结果在刷新或重启后
恢复、按用户隔离、取消与幂等复用。新增 `screener_jobs` 表。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_add_screener_jobs"
down_revision: Union[str, None] = "0001_initial_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "screener_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scan_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_name", sa.String(length=128), nullable=True),
        sa.Column("strategy_params", sa.JSON(), nullable=False),
        sa.Column("boards", sa.JSON(), nullable=False),
        sa.Column("exclude_st", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_screener_jobs_id", "screener_jobs", ["id"], unique=False)
    op.create_index(
        "ix_screener_jobs_scan_id", "screener_jobs", ["scan_id"], unique=True
    )
    op.create_index(
        "ix_screener_jobs_user_id", "screener_jobs", ["user_id"], unique=False
    )
    op.create_index(
        "ix_screener_jobs_strategy_id", "screener_jobs", ["strategy_id"], unique=False
    )
    op.create_index(
        "ix_screener_jobs_status", "screener_jobs", ["status"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_screener_jobs_status", table_name="screener_jobs")
    op.drop_index("ix_screener_jobs_strategy_id", table_name="screener_jobs")
    op.drop_index("ix_screener_jobs_user_id", table_name="screener_jobs")
    op.drop_index("ix_screener_jobs_scan_id", table_name="screener_jobs")
    op.drop_index("ix_screener_jobs_id", table_name="screener_jobs")
    op.drop_table("screener_jobs")
