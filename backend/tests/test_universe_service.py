"""测试 point-in-time 股票池解析服务（VEW-26）。"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.security_universe import SecurityUniverse
from app.models.universe_snapshot import UniverseSnapshot
from app.services import universe_service
from app.services.universe_service import get_universe_as_of, capture_snapshot


def _make_session():
    engine = create_engine("sqlite://")
    UniverseSnapshot.__table__.create(engine)
    SecurityUniverse.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _universe_row(
    code: str,
    name: str | None = None,
    board: str = "main",
    is_st: bool = False,
    is_active: bool = True,
    list_date: date | None = None,
    delist_date: date | None = None,
) -> SecurityUniverse:
    return SecurityUniverse(
        stock_code=code,
        stock_name=name,
        market="SH" if code.startswith(("6", "9")) else "SZ",
        board=board,
        is_st=is_st,
        is_active=is_active,
        list_date=list_date,
        delist_date=delist_date,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


class GetUniverseAsOfTests(unittest.TestCase):
    def test_snapshot_used_when_available(self):
        db = _make_session()
        db.add_all(
            [
                _universe_row("000001", board="main", is_st=False),
                _universe_row("600519", board="main", is_st=True),
                _universe_row("300001", board="gem", is_st=False),
            ]
        )
        db.commit()
        # 落一份 2026-01-10 的快照
        capture_snapshot(db, as_of=date(2026, 1, 10))

        pool = get_universe_as_of(
            db, date(2026, 1, 15), boards=["main"], exclude_st=True
        )
        self.assertEqual(pool.source, "snapshot")
        self.assertEqual(pool.snapshot_date, date(2026, 1, 10))
        self.assertTrue(pool.st_point_in_time)
        self.assertTrue(pool.st_filter_effective)
        # ST 的 600519 被排除，仅剩 000001
        self.assertEqual(pool.symbols, ["000001"])
        self.assertIsNone(pool.warning)

    def test_current_universe_fallback_when_no_snapshot(self):
        db = _make_session()
        db.add_all(
            [
                _universe_row("000001", board="main", is_st=False),
                _universe_row("600519", board="main", is_st=True),
            ]
        )
        db.commit()

        pool = get_universe_as_of(
            db, date(2026, 1, 15), boards=["main"], exclude_st=True
        )
        self.assertEqual(pool.source, "current_universe")
        self.assertIsNone(pool.snapshot_date)
        self.assertFalse(pool.st_point_in_time)
        self.assertTrue(pool.st_filter_effective)
        self.assertEqual(pool.symbols, ["000001"])
        self.assertIn("快照", pool.warning)

    def test_list_delist_filters_are_point_in_time(self):
        db = _make_session()
        db.add_all(
            [
                # 2026-01-20 才上市，早于/晚于 as_of 判断：在 2026-01-15 不应入选
                _universe_row("000001", board="main", list_date=date(2026, 1, 20)),
                # 2026-01-10 已退市，在 2026-01-15 不应入选
                _universe_row("000002", board="main", delist_date=date(2026, 1, 10)),
                # 正常
                _universe_row("000003", board="main", list_date=date(2020, 1, 1)),
            ]
        )
        db.commit()
        capture_snapshot(db, as_of=date(2026, 1, 15))

        pool = get_universe_as_of(
            db, date(2026, 1, 15), boards=["main"], exclude_st=False
        )
        self.assertEqual(pool.symbols, ["000003"])

    def test_empty_when_no_universe_rows(self):
        db = _make_session()
        pool = get_universe_as_of(
            db, date(2026, 1, 15), boards=["main"], exclude_st=True
        )
        self.assertEqual(pool.source, "empty")
        self.assertEqual(pool.symbols, [])
        self.assertFalse(pool.st_filter_effective)
        self.assertIn("empty", pool.source)

    def test_capture_snapshot_upserts_in_place(self):
        db = _make_session()
        db.add(_universe_row("000001", board="main", is_st=False))
        db.commit()
        first = capture_snapshot(db, as_of=date(2026, 1, 10))
        self.assertEqual(first, 1)

        # 再次捕获同一天：ST 状态更新为 True，行数不新增
        row = db.query(SecurityUniverse).filter_by(stock_code="000001").one()
        row.is_st = True
        db.commit()
        second = capture_snapshot(db, as_of=date(2026, 1, 10))
        self.assertEqual(second, 1)
        rows = (
            db.query(UniverseSnapshot)
            .filter(UniverseSnapshot.snapshot_date == date(2026, 1, 10))
            .all()
        )
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_st)

    def test_capture_snapshot_uses_static_fallback_when_universe_empty(self):
        db = _make_session()
        with patch.object(
            universe_service, "_load_static_codes", return_value=["000001", "300001"]
        ):
            count = capture_snapshot(db, as_of=date(2026, 1, 10))
        self.assertEqual(count, 2)
        pool = get_universe_as_of(
            db, date(2026, 1, 10), boards=["main", "gem"], exclude_st=False
        )
        # 板块过滤基于代码推断；000001->main, 300001->gem
        self.assertEqual(sorted(pool.symbols), ["000001", "300001"])


if __name__ == "__main__":
    unittest.main()
