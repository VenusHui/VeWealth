"""VEW-25 选股持久任务与安全隔离相关测试。

覆盖：
- 有界后台执行器（submit / cancel / CancelToken）；
- ScreenerService 持久化、按用户归属隔离（跨用户 404）、同用户幂等、
  防重复提交、取消（pending 立即取消、running 置 cancel_requested、5s 协作式停止）；
- GMM 共享数据的线程/任务隔离，无跨任务 symbol 污染。
"""

from __future__ import annotations

import threading
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.screener_job import ScreenerJob
from app.models.user import User
from app.services.screener_service import ScreenerService
from app.services.task_executor import BackgroundTaskExecutor
import app.services.screener_service as screener_module


def _make_scanner_session(mock: bool = True):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[User.__table__, ScreenerJob.__table__])
    return sessionmaker(bind=engine)


def _insert_users(session_factory, user_ids):
    now = datetime.utcnow()
    with session_factory() as db:
        for uid in user_ids:
            db.add(
                User(
                    id=uid,
                    username=f"user{uid}",
                    hashed_password="x",
                    is_active=True,
                    alert_threshold=0.7,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()


class BackgroundTaskExecutorTests(unittest.TestCase):
    def test_submit_executes_task(self):
        ex = BackgroundTaskExecutor(max_workers=1)
        done = threading.Event()

        def task(token):
            done.set()

        token = ex.submit("job_y", task)
        self.assertIsInstance(token, object)
        self.assertTrue(done.wait(timeout=2))

    def test_cancel_signals_token(self):
        ex = BackgroundTaskExecutor(max_workers=1)
        started = threading.Event()
        released = threading.Event()

        def task(token):
            started.set()
            released.wait(timeout=5)

        token = ex.submit("job_x", task)
        self.assertTrue(started.wait(timeout=2))
        self.assertTrue(ex.cancel("job_x"))
        self.assertTrue(token.is_cancelled())
        released.set()

    def test_cancel_unknown_job_returns_false(self):
        ex = BackgroundTaskExecutor(max_workers=1)
        self.assertFalse(ex.cancel("nope"))


class ScreenerServicePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.service = ScreenerService()
        self.session_factory = _make_scanner_session()
        _insert_users(self.session_factory, [1, 2])  # user1 / user2
        patcher_session = patch.object(
            screener_module, "SessionLocal", self.session_factory
        )
        patcher_exec = patch.object(
            screener_module, "background_task_executor", autospec=True
        )
        patcher_symbols = patch.object(
            screener_module.stock_service,
            "get_all_stock_symbols",
            return_value=["000001", "600519", "300750"],
        )
        patcher_session.start()
        patcher_exec.start()
        patcher_symbols.start()
        self.addCleanup(patcher_session.stop)
        self.addCleanup(patcher_exec.stop)
        self.addCleanup(patcher_symbols.stop)
        self.executor = screener_module.background_task_executor

    # --- persistence ---

    def test_start_scan_persists_and_returns_state(self):
        resp = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        # 前端契约：进行中暴露为 "scanning"，DB 内部仍为 "pending"
        self.assertEqual(resp["status"], "scanning")
        self.assertTrue(resp["scan_id"].startswith("scan_"))
        self.assertEqual(resp["progress"]["total"], 3)
        self.assertEqual(resp["exclude_st"], True)

        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=resp["scan_id"]).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.user_id, 1)
            self.assertEqual(row.status, "pending")

        self.executor.submit.assert_called_once()

    # --- ownership / 404 isolation ---

    def test_get_scan_is_owner_scoped(self):
        resp = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        scan_id = resp["scan_id"]

        owner = self.service.get_scan(scan_id, user_id=1)
        self.assertIsNotNone(owner)
        self.assertEqual(owner["scan_id"], scan_id)

        # 用户2 查询用户1 的 scan → None（路由层映射为 404）
        self.assertIsNone(self.service.get_scan(scan_id, user_id=2))

    def test_list_scans_scoped_to_user(self):
        self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        self.service.start_scan("ma_cross_v1", {}, ["main"], True, 2)

        self.assertEqual(len(self.service.list_scans(1)), 1)
        self.assertEqual(len(self.service.list_scans(2)), 1)

    # --- idempotency / duplicate submission ---

    def test_start_scan_idempotent_for_same_request(self):
        r1 = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        r2 = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        self.assertEqual(r1["scan_id"], r2["scan_id"])

    def test_start_scan_rejects_different_active_request(self):
        self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        with self.assertRaises(ValueError):
            self.service.start_scan("ma_cross_v1", {}, ["main", "gem"], True, 1)

    def test_start_scan_allows_after_completion(self):
        r1 = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=r1["scan_id"]).first()
            row.status = "completed"
            row.stage = "done"
            db.commit()
        r2 = self.service.start_scan("ma_cross_v1", {}, ["main", "gem"], True, 1)
        self.assertNotEqual(r1["scan_id"], r2["scan_id"])

    # --- cancellation ---

    def test_cancel_pending_scan_flips_cancelled(self):
        resp = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        scan = self.service.cancel_scan(resp["scan_id"], 1)
        self.assertEqual(scan["status"], "cancelled")
        self.executor.cancel.assert_called_with(resp["scan_id"])

    def test_cancel_running_scan_marks_cancel_requested(self):
        resp = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=resp["scan_id"]).first()
            row.status = "running"
            row.stage = "running"
            db.commit()

        scan = self.service.cancel_scan(resp["scan_id"], 1)
        # 运行中不强制翻转 DB 状态；对外契约仍为 "scanning"
        self.assertEqual(scan["status"], "scanning")
        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=resp["scan_id"]).first()
            self.assertEqual(row.status, "running")
            self.assertEqual(row.cancel_requested, 1)

    def test_run_scan_stops_when_token_cancelled(self):
        resp = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        scan_id = resp["scan_id"]
        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=scan_id).first()
            row.status = "running"
            row.stage = "running"
            db.commit()

        token = MagicMock()
        token.is_cancelled.return_value = True
        self.service._run_scan(token, scan_id, "ma_cross_v1", {}, ["000001"])

        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=scan_id).first()
            self.assertEqual(row.status, "cancelled")

    def test_run_scan_checks_db_cancel_requested(self):
        resp = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        scan_id = resp["scan_id"]
        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=scan_id).first()
            row.status = "running"
            row.cancel_requested = 1
            db.commit()

        token = MagicMock()
        token.is_cancelled.return_value = False
        self.service._run_scan(token, scan_id, "ma_cross_v1", {}, ["000001"])

        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=scan_id).first()
            self.assertEqual(row.status, "cancelled")

    def test_recover_stale_jobs_on_startup(self):
        resp = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        scan_id = resp["scan_id"]
        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=scan_id).first()
            row.status = "running"
            row.updated_at = datetime(2020, 1, 1)
            db.commit()

        count = self.service.recover_stale_jobs_on_startup(timeout_minutes=5)
        self.assertEqual(count, 1)
        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=scan_id).first()
            self.assertEqual(row.status, "cancelled")

    # --- must-fix: queue-full orphan + frontend scanning contract ---

    def test_start_scan_exposes_scanning_contract(self):
        """进行中任务对外必须暴露 "scanning"，不能直接暴露 pending/running。"""
        resp = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)
        self.assertEqual(resp["status"], "scanning")

        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(scan_id=resp["scan_id"]).first()
            row.status = "running"
            row.stage = "running"
            db.commit()

        fetched = self.service.get_scan(resp["scan_id"], 1)
        self.assertEqual(fetched["status"], "scanning")

        listed = self.service.list_scans(1)
        self.assertEqual(listed[0]["status"], "scanning")

    def test_start_scan_queue_full_marks_failed_not_orphan(self):
        """队列满时入队抛 ValueError：已持久化的 pending 行必须标记 failed。"""
        self.executor.submit.side_effect = ValueError(
            "后台任务队列已满（pending >= 40），请稍后重试"
        )
        with self.assertRaises(ValueError):
            self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)

        with self.session_factory() as db:
            row = db.query(ScreenerJob).filter_by(user_id=1).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.status, "failed")
            self.assertIn("队列已满", row.error or "")

    # --- must-fix: same-user active scan TOCTOU (VEW-32 阻断项 3) ---

    def test_db_unique_index_rejects_duplicate_active_scan(self):
        """部分唯一索引在数据库层拒绝同一用户第二条 active 扫描。"""
        now = datetime.utcnow()
        with self.session_factory() as db:
            db.add(
                ScreenerJob(
                    scan_id="scan_a",
                    user_id=1,
                    strategy_id="ma_cross_v1",
                    strategy_name="x",
                    strategy_params={},
                    boards=["main"],
                    exclude_st=1,
                    status="pending",
                    stage="pending",
                    progress={"total": 1, "scanned": 0, "hits": 0},
                    cancel_requested=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.commit()

        with self.session_factory() as db:
            db.add(
                ScreenerJob(
                    scan_id="scan_b",
                    user_id=1,
                    strategy_id="ma_cross_v1",
                    strategy_name="x",
                    strategy_params={},
                    boards=["main"],
                    exclude_st=1,
                    status="running",
                    stage="running",
                    progress={"total": 1, "scanned": 0, "hits": 0},
                    cancel_requested=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_start_scan_recovers_from_concurrent_duplicate_insert(self):
        """并发查找-插入竞态：insert 触发 IntegrityError 后必须重查并幂等复用。"""
        now = datetime.utcnow()
        with self.session_factory() as db:
            db.add(
                ScreenerJob(
                    scan_id="scan_preexisting",
                    user_id=1,
                    strategy_id="ma_cross_v1",
                    strategy_name="x",
                    strategy_params={},
                    boards=["main"],
                    exclude_st=1,
                    status="pending",
                    stage="pending",
                    progress={"total": 3, "scanned": 0, "hits": 0},
                    cancel_requested=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.commit()

        calls = {"n": 0}
        real_find = self.service._find_active_for_user

        def fake_find(db, user_id):
            calls["n"] += 1
            # 第一次 find 未见并发线程的 active 行（模拟竞态窗口）；插入失败回滚后
            # 第二次 find 必须能看到它，走幂等复用分支。
            if calls["n"] == 1:
                return None
            return real_find(db, user_id)

        with patch.object(self.service, "_find_active_for_user", side_effect=fake_find):
            resp = self.service.start_scan("ma_cross_v1", {}, ["main"], True, 1)

        self.assertEqual(resp["scan_id"], "scan_preexisting")
        self.assertEqual(calls["n"], 2)
        with self.session_factory() as db:
            active = (
                db.query(ScreenerJob)
                .filter(
                    ScreenerJob.user_id == 1,
                    ScreenerJob.status.in_(["pending", "running"]),
                )
                .all()
            )
            self.assertEqual(len(active), 1)


def _make_market_df(symbol: str, n: int = 120) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    df = pd.DataFrame(
        {
            "datetime": dates,
            "open": [10.0] * n,
            "close": [10.0 + 0.01 * i for i in range(n)],
            "high": [11.0] * n,
            "low": [9.0] * n,
            "volume": [100000.0] * n,
        }
    )
    df["symbol"] = symbol
    return df


_GMM_PARAMS = {
    "lookback_days": 20,
    "threshold": 0.5,
    "max_components": 3,
    "refit_interval": 5,
    "max_workers": 1,
}


class GMMIsolationTests(unittest.TestCase):
    def test_gmm_shared_data_is_thread_local(self):
        from app.services.backtest.strategies.gmm_volume_v1 import (
            _get_shared_data,
            _set_shared_data,
            _SymbolData,
        )

        _set_shared_data([])
        _set_shared_data([_SymbolData("AAA", [])])
        try:
            self.assertEqual(len(_get_shared_data()), 1)

            other: dict = {}

            def read_other():
                other["n"] = len(_get_shared_data())

            t = threading.Thread(target=read_other)
            t.start()
            t.join()
            self.assertEqual(other["n"], 0)  # 线程 A 的数据对线程 B 不可见
        finally:
            _set_shared_data([])

    def test_gmm_candidates_scoped_to_input_symbols(self):
        from app.services.backtest.strategies.gmm_volume_v1 import GMMVolumeV1Strategy

        strat = GMMVolumeV1Strategy()
        cand_a = strat.generate_candidates(_make_market_df("AAA"), _GMM_PARAMS)
        cand_b = strat.generate_candidates(_make_market_df("BBB"), _GMM_PARAMS)

        if not cand_a.empty:
            self.assertTrue(set(cand_a["symbol"]).issubset({"AAA"}))
        if not cand_b.empty:
            self.assertTrue(set(cand_b["symbol"]).issubset({"BBB"}))

    def test_gmm_concurrent_calls_do_not_corrupt(self):
        from app.services.backtest.strategies.gmm_volume_v1 import GMMVolumeV1Strategy

        strat = GMMVolumeV1Strategy()
        df_a = _make_market_df("AAA")
        df_b = _make_market_df("BBB")
        results: dict = {}
        barrier = threading.Barrier(2)

        def run(name: str, df: pd.DataFrame):
            barrier.wait()
            cand = strat.generate_candidates(df, _GMM_PARAMS)
            results[name] = set(cand["symbol"]) if not cand.empty else set()

        threads = [
            threading.Thread(target=run, args=("a", df_a)),
            threading.Thread(target=run, args=("b", df_b)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 任一结果里的 symbol 都只能来自各自 job 的输入，绝无跨任务污染
        self.assertTrue(results["a"].issubset({"AAA"}))
        self.assertTrue(results["b"].issubset({"BBB"}))


if __name__ == "__main__":
    unittest.main()
