"""在线分时表（PG ``stock_minute_data``）批量 upsert 的单元测试。

本表是在线查询路径的近期窗口（分时图 / 告警 / 选股信号），与已退役的全市场分钟归档
（VEW-71 移除的 Parquet 分钟库）无关。覆盖的契约：

- 批量 upsert 幂等（sqlite 走非 PG 退化路径）；
- 唯一键含 ``period``，多周期数据互不覆盖；
- 迁移 ``0004_add_minute_period`` 挂在 ``0003`` 之后，且 upgrade/downgrade 均可用。
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.stock_data import StockMinuteData
from app.services.data_collector import DataCollector

TRADE_DATE = date(2026, 10, 2)


def _collector_df(symbol: str, period: str = "1") -> pd.DataFrame:
    """构造一段分钟 bar，列名用 ``_bulk_upsert`` 期望的 PG 列名。"""
    stamps = pd.date_range(f"{TRADE_DATE.isoformat()} 09:30:00", periods=3, freq="1min")
    return pd.DataFrame(
        {
            "trade_time": stamps,
            "stock_code": [symbol] * 3,
            "period": [period] * 3,
            "trade_date": [TRADE_DATE] * 3,
            "open_price": [10.0] * 3,
            "high_price": [10.1] * 3,
            "low_price": [9.9] * 3,
            "close_price": [10.0, 10.01, 10.02],
            "volume": [100.0, 200.0, 300.0],
        }
    )


@pytest.fixture
def sqlite_session():
    engine = create_engine("sqlite://")
    StockMinuteData.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_bulk_upsert_is_idempotent(sqlite_session):
    collector = DataCollector(sqlite_session)
    df = _collector_df("000001")

    first = collector._bulk_upsert(df, "000001", TRADE_DATE, "1")
    second = collector._bulk_upsert(df, "000001", TRADE_DATE, "1")

    assert first == 3
    assert second == 0  # 非 PG 退化路径：已存在的 key 不重复插入
    assert sqlite_session.query(StockMinuteData).count() == 3


def test_bulk_upsert_keeps_periods_separate(sqlite_session):
    collector = DataCollector(sqlite_session)
    collector._bulk_upsert(_collector_df("000001", "1"), "000001", TRADE_DATE, "1")
    collector._bulk_upsert(_collector_df("000001", "5"), "000001", TRADE_DATE, "5")

    assert sqlite_session.query(StockMinuteData).count() == 6


def test_unique_index_includes_period():
    """回归守卫：唯一键必须是 (stock_code, period, trade_time)，否则多周期互相覆盖。"""
    unique_indexes = [idx for idx in StockMinuteData.__table__.indexes if idx.unique]
    assert len(unique_indexes) == 1
    assert [c.name for c in unique_indexes[0].columns] == [
        "stock_code",
        "period",
        "trade_time",
    ]


def test_migration_revision_chain():
    """迁移必须挂在 0003 之后，且 upgrade/downgrade 均可用。"""
    # 迁移文件名以数字开头，不能作为模块名 import，按路径加载
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0004_add_minute_period.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0004", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0004_add_minute_period"
    assert module.down_revision == "0003_add_universe_snapshots"
    assert callable(module.upgrade) and callable(module.downgrade)
