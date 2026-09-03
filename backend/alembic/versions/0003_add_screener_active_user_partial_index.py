"""add partial unique index: one active screener_jobs row per user (VEW-32)

Revision ID: 0003_add_screener_active_user_partial_index
Revises: 0002_add_screener_jobs
Create Date: 2026-09-03

修复 VEW-32 阻断项 3（TOCTOU）：`start_scan` 的"查找 active → 插入"之间无锁、无
唯一约束，两个并发请求同一 user_id 会各建一条 active 扫描。在数据库层加部分唯一
索引 `UNIQUE(user_id) WHERE status IN ('pending','running')` 兜底，配合服务层捕获
IntegrityError 后重查，保证同一用户至多一条进行中的扫描。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "0003_add_screener_active_user_partial_index"
down_revision: Union[str, None] = "0002_add_screener_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 先取消历史遗留的重复 active 扫描（保留最后一条），否则建唯一索引会失败。
    op.execute("""
        UPDATE screener_jobs
           SET status = 'cancelled', stage = 'cancelled'
         WHERE id IN (
             SELECT id FROM (
                 SELECT id, ROW_NUMBER() OVER (
                     PARTITION BY user_id ORDER BY created_at DESC
                 ) AS rn
                 FROM screener_jobs
                 WHERE status IN ('pending', 'running')
             ) ranked
             WHERE rn > 1
         )
        """)
    op.create_index(
        "uq_screener_jobs_active_user",
        "screener_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_screener_jobs_active_user", table_name="screener_jobs")
