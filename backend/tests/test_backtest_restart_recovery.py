"""VEW-30 回测任务重启恢复测试。

此前仅有 screener 侧 `ScreenerService.recover_stale_jobs_on_startup` 的恢复测试，
backtest 的 `BacktestJobManager.recover_stale_jobs_on_startup`（job_manager）完全
无覆盖。本用例用内存 SQLite 验证：
- 超时阈值前的 pending / running 任务被标记为 cancelled 并带恢复错误；
- 阈值内新任务不受影响；
- 终态（success / failed / cancelled）任务不被动；
- 返回被恢复的任务数量。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.backtest_job import BacktestJob
from app.models.user import User
from app.services.backtest.job_manager import (
    BacktestJobManager,
    RESTART_RECOVERY_ERROR,
    RESTART_RECOVERY_TIMEOUT_MINUTES,
)

TIMEOUT = RESTART_RECOVERY_TIMEOUT_MINUTES


def _make_session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[User.__table__, BacktestJob.__table__])
    return sessionmaker(bind=engine)


class BacktestRestartRecoveryTests(unittest.TestCase):
    def setUp(self):
        # 以"当前 UTC 时间"为基准，recover 用 datetime.utcnow() 计算 cutoff
        self.now = datetime.utcnow()
        self.session_factory = _make_session_factory()
        with self.session_factory() as db:
            db.add(
                User(
                    id=1,
                    username="u1",
                    hashed_password="x",
                    is_active=True,
                    alert_threshold=0.7,
                    created_at=self.now,
                    updated_at=self.now,
                )
            )
            db.commit()

        self.patcher = patch(
            "app.services.backtest.job_manager.SessionLocal", self.session_factory
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.manager = BacktestJobManager(backtest_service=None)

    def _add_job(self, job_id, status, updated_at):
        with self.session_factory() as db:
            db.add(
                BacktestJob(
                    job_id=job_id,
                    user_id=1,
                    status=status,
                    stage=status,
                    progress_pct=0,
                    total_symbols=0,
                    processed_symbols=0,
                    request_payload={},
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            )
            db.commit()

    def _get_job(self, job_id):
        with self.session_factory() as db:
            return db.query(BacktestJob).filter_by(job_id=job_id).first()

    def test_stale_running_job_is_recovered(self):
        stale = self.now - timedelta(minutes=TIMEOUT + 30)
        self._add_job("j_stale_run", "running", stale)

        count = self.manager.recover_stale_jobs_on_startup()
        self.assertEqual(count, 1)

        job = self._get_job("j_stale_run")
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.stage, "cancelled")
        self.assertEqual(job.error, RESTART_RECOVERY_ERROR)
        self.assertEqual(job.cancel_requested, 1)

    def test_stale_pending_job_is_recovered(self):
        stale = self.now - timedelta(minutes=TIMEOUT + 10)
        self._add_job("j_stale_pending", "pending", stale)
        count = self.manager.recover_stale_jobs_on_startup()
        self.assertEqual(count, 1)
        self.assertEqual(self._get_job("j_stale_pending").status, "cancelled")

    def test_recent_job_is_not_touched(self):
        recent = self.now - timedelta(minutes=1)
        self._add_job("j_recent", "running", recent)
        count = self.manager.recover_stale_jobs_on_startup()
        self.assertEqual(count, 0)
        self.assertEqual(self._get_job("j_recent").status, "running")

    def test_terminal_jobs_are_not_touched(self):
        stale = self.now - timedelta(minutes=TIMEOUT + 30)
        self._add_job("j_done", "success", stale)
        self._add_job("j_failed", "failed", stale)
        self._add_job("j_cancelled", "cancelled", stale)
        count = self.manager.recover_stale_jobs_on_startup()
        self.assertEqual(count, 0)
        self.assertEqual(self._get_job("j_done").status, "success")
        self.assertEqual(self._get_job("j_failed").status, "failed")
        self.assertEqual(self._get_job("j_cancelled").status, "cancelled")

    def test_no_stale_returns_zero(self):
        self.assertEqual(self.manager.recover_stale_jobs_on_startup(), 0)

    def test_mixed_returns_only_stale_count(self):
        stale = self.now - timedelta(minutes=TIMEOUT + 30)
        recent = self.now - timedelta(minutes=1)
        self._add_job("j_a", "running", stale)
        self._add_job("j_b", "pending", recent)
        count = self.manager.recover_stale_jobs_on_startup()
        self.assertEqual(count, 1)
        self.assertEqual(self._get_job("j_a").status, "cancelled")
        self.assertEqual(self._get_job("j_b").status, "pending")


if __name__ == "__main__":
    unittest.main()
