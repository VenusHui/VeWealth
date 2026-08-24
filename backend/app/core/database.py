"""
数据库连接和会话管理
"""

import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings

logger = logging.getLogger("vewealth.database")

# postgres advisory lock key：串行化多 worker 并发启动时的迁移/建表
_ALEMBIC_LOCK_KEY = 726291

# 创建数据库引擎
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,  # 连接池健康检查
    echo=False,  # 生产环境设为False
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基类
Base = declarative_base()


def get_db():
    """
    获取数据库会话
    FastAPI依赖项，用于路由中注入数据库会话
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    初始化数据库

    与 Alembic 迁移体系并存，保证 schema 变更在应用启动路径上生效：
    - 数据库已纳入 Alembic 管理（存在 alembic_version 表）：
      执行 `alembic upgrade head` 应用待执行迁移。create_all 只创建缺失的表、
      不会给已有表加列，无法承担后续 schema 变更，因此升级路径必须走迁移。
    - 数据库未纳入 Alembic 管理：按 ORM 模型 `create_all` 建表，
      再 `stamp head` 把基线标记为已应用。
    用 postgres advisory lock 串行化，避免多 worker 并发启动时的竞争。
    """
    from app.models import (
        user,
        watchlist,
        stock_data,
        backtest,
        backtest_job,
        security_universe,
        alert_history,
    )  # noqa

    with _alembic_lock():
        if _has_alembic_version():
            _run_alembic_upgrade()
        else:
            Base.metadata.create_all(bind=engine)
            _stamp_alembic_head()


def _has_alembic_version() -> bool:
    """数据库是否已纳入 Alembic 版本管理（存在 alembic_version 表）。"""
    inspector = inspect(engine)
    return "alembic_version" in inspector.get_table_names()


def _alembic_config():
    """
    构造 Alembic Config。

    sqlalchemy.url 用应用配置覆盖；script_location 用绝对路径，
    保证 init_db() 在任意工作目录下调用时都能定位到迁移脚本。
    """
    from alembic.config import Config

    backend_dir = Path(__file__).parent.parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return cfg


def _run_alembic_upgrade() -> None:
    """对已纳入 Alembic 管理的库执行 `alembic upgrade head` 应用待执行迁移。

    迁移失败直接抛出，阻止应用在缺列的 schema 上启动。
    """
    from alembic import command

    command.upgrade(_alembic_config(), "head")


def _stamp_alembic_head() -> None:
    """create_all 建表后把基线标记为已应用；失败降级 warning，不影响建表。"""
    try:
        from alembic import command

        command.stamp(_alembic_config(), "head")
    except Exception as e:  # pragma: no cover - 降级路径，不影响建表
        logger.warning("Alembic stamp head 失败（不影响 init_db 建表）：%s", e)


@contextmanager
def _alembic_lock():
    """postgres 会话级 advisory lock，串行化并发启动时的迁移/建表。

    锁在独立连接上持有，迁移/建表在主连接执行期间，其它 init_db() 会阻塞在
    pg_advisory_lock；持锁进程退出/连接关闭后锁自动释放，不会死锁。
    """
    conn = None
    locked = False
    try:
        conn = engine.connect()
        if engine.dialect.name == "postgresql":
            conn.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": _ALEMBIC_LOCK_KEY}
            )
            locked = True
        yield
    finally:
        if locked:
            try:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": _ALEMBIC_LOCK_KEY}
                )
            except Exception:
                pass
        if conn is not None:
            conn.close()
