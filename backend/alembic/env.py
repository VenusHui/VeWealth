"""
Alembic 迁移环境配置

- 数据库 URL 复用 app/core/config.py 的 settings.DATABASE_URL，
  保证迁移与应用运行使用同一数据库配置（含 ENV 环境变量选择）。
- target_metadata 绑定 app.core.database.Base.metadata，
  并显式导入全部 ORM 模型，使 autogenerate 能感知完整 schema。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 导入应用数据库基类与全部模型，确保表全部注册到 Base.metadata
from app.core.config import settings
from app.core.database import Base
from app.models.user import User  # noqa: F401
from app.models.watchlist import WatchList  # noqa: F401
from app.models.stock_data import StockMinuteData  # noqa: F401
from app.models.backtest import BacktestRun, BacktestRound  # noqa: F401
from app.models.backtest_job import BacktestJob  # noqa: F401
from app.models.security_universe import SecurityUniverse  # noqa: F401
from app.models.alert_history import AlertHistory  # noqa: F401

config = context.config

# 用应用配置覆盖 alembic.ini 中的 sqlalchemy.url
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
