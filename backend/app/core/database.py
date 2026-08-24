"""
数据库连接和会话管理
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings

logger = logging.getLogger("vewealth.database")

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
    创建所有表
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

    Base.metadata.create_all(bind=engine)
    stamp_alembic_head_if_unversioned()


def stamp_alembic_head_if_unversioned():
    """
    init_db() 与 Alembic 迁移体系的衔接点。

    create_all 建表后，若数据库尚未纳入 Alembic 版本管理（无 alembic_version 表），
    则执行 `alembic stamp head` 把基线标记为最新，避免后续 `alembic upgrade`
    重复创建已存在的表；已纳入 Alembic 管理的数据库不做任何干预，
    以免跳过未应用的迁移。
    """
    try:
        inspector = inspect(engine)
        if "alembic_version" in inspector.get_table_names():
            return

        from alembic import command
        from alembic.config import Config

        # alembic.ini 位于 backend/ 目录；script_location 用绝对路径，
        # 保证 init_db() 在任意工作目录下调用时都能定位到迁移脚本
        backend_dir = Path(__file__).parent.parent.parent
        cfg = Config(str(backend_dir / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
        cfg.set_main_option("script_location", str(backend_dir / "alembic"))
        command.stamp(cfg, "head")
    except Exception as e:  # pragma: no cover - 降级路径，不影响建表
        logger.warning("Alembic stamp head 失败（不影响 init_db 建表）：%s", e)
