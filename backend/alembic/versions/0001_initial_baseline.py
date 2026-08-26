"""initial baseline: v1.2.0 schema snapshot

Revision ID: 0001_initial_baseline
Revises:
Create Date: 2026-08-24

把散落在 backend/migration/db/v1/ 的零散脚本（migrate_add_password、
migrate_add_alert_direction、migrate_add_backtest_round_stock_name）以及
init_db() 自动建表覆盖的完整 schema，固化为 Alembic 基线迁移。

背景：v1.2.0 发布后基线稳定，本迁移即代表“当前生产 schema”。
存量数据库（已具备全部表与字段）使用 `alembic stamp head` 标记即可，
全新数据库使用 `alembic upgrade head` 建表；后续 schema 变更一律新增迁移版本。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ---- users ----
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False, comment="用户名"),
        sa.Column(
            "hashed_password", sa.String(length=255), nullable=False, comment="哈希密码"
        ),
        sa.Column(
            "wechat_openid", sa.String(length=100), nullable=True, comment="微信OpenID"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, comment="是否激活"),
        sa.Column(
            "alert_threshold", sa.Float(), nullable=False, comment="预警阈值（0-1）"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, comment="更新时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"], unique=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_wechat_openid", "users", ["wechat_openid"], unique=True)

    # ---- watchlists ----
    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="用户ID"),
        sa.Column(
            "stock_code", sa.String(length=10), nullable=False, comment="股票代码"
        ),
        sa.Column(
            "stock_name", sa.String(length=50), nullable=True, comment="股票名称"
        ),
        sa.Column(
            "alert_enabled", sa.Boolean(), nullable=False, comment="是否启用预警"
        ),
        sa.Column(
            "alert_threshold",
            sa.Float(),
            nullable=True,
            comment="个性化预警阈值（null则使用用户默认值）",
        ),
        sa.Column(
            "last_alerted_at", sa.DateTime(), nullable=True, comment="最后预警时间"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, comment="更新时间"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "stock_code", name="uq_user_stock"),
    )
    op.create_index("ix_watchlists_id", "watchlists", ["id"], unique=False)
    op.create_index("ix_watchlists_user_id", "watchlists", ["user_id"], unique=False)
    op.create_index(
        "ix_watchlists_stock_code", "watchlists", ["stock_code"], unique=False
    )

    # ---- stock_minute_data ----
    op.create_table(
        "stock_minute_data",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "stock_code", sa.String(length=10), nullable=False, comment="股票代码"
        ),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日期"),
        sa.Column("trade_time", sa.DateTime(), nullable=False, comment="交易时间"),
        sa.Column("open_price", sa.Float(), nullable=False, comment="开盘价"),
        sa.Column("high_price", sa.Float(), nullable=False, comment="最高价"),
        sa.Column("low_price", sa.Float(), nullable=False, comment="最低价"),
        sa.Column("close_price", sa.Float(), nullable=False, comment="收盘价"),
        sa.Column("volume", sa.Float(), nullable=False, comment="成交量"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stock_minute_data_id", "stock_minute_data", ["id"], unique=False
    )
    op.create_index(
        "idx_stock_date",
        "stock_minute_data",
        ["stock_code", "trade_date"],
        unique=False,
    )
    op.create_index(
        "idx_stock_time",
        "stock_minute_data",
        ["stock_code", "trade_time"],
        unique=False,
    )
    op.create_index(
        "idx_unique_data",
        "stock_minute_data",
        ["stock_code", "trade_time"],
        unique=True,
    )

    # ---- backtest_runs ----
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_params", sa.JSON(), nullable=False),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("initial_cash", sa.Float(), nullable=False),
        sa.Column("benchmark", sa.String(length=10), nullable=True),
        sa.Column("cost_config", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("equity_curve", sa.JSON(), nullable=False),
        sa.Column("trades", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_runs_id", "backtest_runs", ["id"], unique=False)
    op.create_index(
        "ix_backtest_runs_user_id", "backtest_runs", ["user_id"], unique=False
    )
    op.create_index(
        "ix_backtest_runs_status", "backtest_runs", ["status"], unique=False
    )

    # ---- backtest_rounds ----
    op.create_table(
        "backtest_rounds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("stock_name", sa.String(length=100), nullable=True),
        sa.Column("open_time", sa.String(length=32), nullable=True),
        sa.Column("open_price", sa.Float(), nullable=True),
        sa.Column("close_time", sa.String(length=32), nullable=True),
        sa.Column("close_price", sa.Float(), nullable=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("holding_days", sa.Integer(), nullable=True),
        sa.Column("pnl_amount", sa.Float(), nullable=False),
        sa.Column("pnl_ratio", sa.Float(), nullable=False),
        sa.Column("exit_reason", sa.String(length=128), nullable=True),
        sa.Column("max_favorable_excursion", sa.Float(), nullable=True),
        sa.Column("max_adverse_excursion", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_rounds_id", "backtest_rounds", ["id"], unique=False)
    op.create_index(
        "ix_backtest_rounds_run_id", "backtest_rounds", ["run_id"], unique=False
    )
    op.create_index(
        "ix_backtest_rounds_symbol", "backtest_rounds", ["symbol"], unique=False
    )

    # ---- backtest_jobs ----
    op.create_table(
        "backtest_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("progress_pct", sa.Float(), nullable=False),
        sa.Column("total_symbols", sa.Integer(), nullable=False),
        sa.Column("processed_symbols", sa.Integer(), nullable=False),
        sa.Column("eta_seconds", sa.Integer(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_jobs_id", "backtest_jobs", ["id"], unique=False)
    op.create_index("ix_backtest_jobs_job_id", "backtest_jobs", ["job_id"], unique=True)
    op.create_index(
        "ix_backtest_jobs_user_id", "backtest_jobs", ["user_id"], unique=False
    )
    op.create_index(
        "ix_backtest_jobs_status", "backtest_jobs", ["status"], unique=False
    )

    # ---- security_universe ----
    op.create_table(
        "security_universe",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stock_code", sa.String(length=16), nullable=False),
        sa.Column("stock_name", sa.String(length=64), nullable=True),
        sa.Column("market", sa.String(length=16), nullable=True),
        sa.Column("board", sa.String(length=16), nullable=False),
        sa.Column("is_st", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("list_date", sa.Date(), nullable=True),
        sa.Column("delist_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_security_universe_id", "security_universe", ["id"], unique=False
    )
    op.create_index(
        "ix_security_universe_stock_code",
        "security_universe",
        ["stock_code"],
        unique=True,
    )
    op.create_index(
        "ix_security_universe_board", "security_universe", ["board"], unique=False
    )
    op.create_index(
        "ix_security_universe_is_active",
        "security_universe",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        "idx_security_universe_board_st",
        "security_universe",
        ["board", "is_st"],
        unique=False,
    )
    op.create_index(
        "idx_security_universe_active",
        "security_universe",
        ["is_active"],
        unique=False,
    )

    # ---- alert_history ----
    op.create_table(
        "alert_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="用户ID"),
        sa.Column(
            "stock_code", sa.String(length=10), nullable=False, comment="股票代码"
        ),
        sa.Column(
            "stock_name", sa.String(length=50), nullable=True, comment="股票名称"
        ),
        sa.Column("alert_threshold", sa.Float(), nullable=True, comment="触发时的阈值"),
        sa.Column("current_price", sa.Float(), nullable=False, comment="触发时的价格"),
        sa.Column("change_pct", sa.Float(), nullable=True, comment="触发时的涨跌幅"),
        sa.Column(
            "alert_direction",
            sa.String(length=4),
            nullable=True,
            comment="预警方向: buy / sell",
        ),
        sa.Column(
            "density_value",
            sa.Float(),
            nullable=True,
            comment="触发时的密度百分位(0-1)",
        ),
        sa.Column(
            "peak_price", sa.Float(), nullable=True, comment="触发时最近的GMM峰值价格"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="触发时间"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_history_id", "alert_history", ["id"], unique=False)
    op.create_index(
        "ix_alert_history_user_id", "alert_history", ["user_id"], unique=False
    )
    op.create_index(
        "ix_alert_history_stock_code", "alert_history", ["stock_code"], unique=False
    )
    op.create_index(
        "ix_alert_history_created_at", "alert_history", ["created_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema（按依赖顺序逆序删除全部表）。"""
    op.drop_index("ix_alert_history_created_at", table_name="alert_history")
    op.drop_index("ix_alert_history_stock_code", table_name="alert_history")
    op.drop_index("ix_alert_history_user_id", table_name="alert_history")
    op.drop_index("ix_alert_history_id", table_name="alert_history")
    op.drop_table("alert_history")

    op.drop_index("idx_security_universe_active", table_name="security_universe")
    op.drop_index("idx_security_universe_board_st", table_name="security_universe")
    op.drop_index("ix_security_universe_is_active", table_name="security_universe")
    op.drop_index("ix_security_universe_board", table_name="security_universe")
    op.drop_index("ix_security_universe_stock_code", table_name="security_universe")
    op.drop_index("ix_security_universe_id", table_name="security_universe")
    op.drop_table("security_universe")

    op.drop_index("ix_backtest_jobs_status", table_name="backtest_jobs")
    op.drop_index("ix_backtest_jobs_user_id", table_name="backtest_jobs")
    op.drop_index("ix_backtest_jobs_job_id", table_name="backtest_jobs")
    op.drop_index("ix_backtest_jobs_id", table_name="backtest_jobs")
    op.drop_table("backtest_jobs")

    op.drop_index("ix_backtest_rounds_symbol", table_name="backtest_rounds")
    op.drop_index("ix_backtest_rounds_run_id", table_name="backtest_rounds")
    op.drop_index("ix_backtest_rounds_id", table_name="backtest_rounds")
    op.drop_table("backtest_rounds")

    op.drop_index("ix_backtest_runs_status", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_user_id", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_id", table_name="backtest_runs")
    op.drop_table("backtest_runs")

    op.drop_index("idx_unique_data", table_name="stock_minute_data")
    op.drop_index("idx_stock_time", table_name="stock_minute_data")
    op.drop_index("idx_stock_date", table_name="stock_minute_data")
    op.drop_index("ix_stock_minute_data_id", table_name="stock_minute_data")
    op.drop_table("stock_minute_data")

    op.drop_index("ix_watchlists_stock_code", table_name="watchlists")
    op.drop_index("ix_watchlists_user_id", table_name="watchlists")
    op.drop_index("ix_watchlists_id", table_name="watchlists")
    op.drop_table("watchlists")

    op.drop_index("ix_users_wechat_openid", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
