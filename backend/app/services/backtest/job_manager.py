"""回测离线任务管理（数据库持久化）"""

from __future__ import annotations

from datetime import datetime
from threading import Thread, Lock
from uuid import uuid4
from copy import deepcopy
from typing import Any

from app.core.database import SessionLocal
from app.models.backtest_job import BacktestJob
from app.models.user import User
from app.schemas.backtest import BacktestRunRequest


class BacktestJobManager:
    def __init__(self, backtest_service):
        self.backtest_service = backtest_service
        self._running_locks: dict[str, Lock] = {}

    def create_job(self, request: BacktestRunRequest, current_user: User) -> dict:
        db = SessionLocal()
        try:
            job_id = uuid4().hex[:12]
            now = datetime.utcnow()
            job = BacktestJob(
                job_id=job_id,
                user_id=current_user.id,
                status="pending",
                stage="pending",
                progress_pct=0,
                total_symbols=0,
                processed_symbols=0,
                request_payload=request.model_dump(mode="json"),
                created_at=now,
                updated_at=now,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            data = self._to_dict(job)
        finally:
            db.close()

        self._spawn_runner(job_id)
        return data

    def list_jobs(self, user_id: int) -> list[dict]:
        db = SessionLocal()
        try:
            rows = (
                db.query(BacktestJob)
                .filter(BacktestJob.user_id == user_id)
                .order_by(BacktestJob.created_at.desc())
                .all()
            )
            return [self._to_dict(r) for r in rows]
        finally:
            db.close()

    def get_job(self, job_id: str, user_id: int) -> dict | None:
        db = SessionLocal()
        try:
            row = (
                db.query(BacktestJob)
                .filter(BacktestJob.job_id == job_id, BacktestJob.user_id == user_id)
                .first()
            )
            return self._to_dict(row) if row else None
        finally:
            db.close()

    def cancel_job(self, job_id: str, user_id: int) -> dict | None:
        db = SessionLocal()
        try:
            row = (
                db.query(BacktestJob)
                .filter(BacktestJob.job_id == job_id, BacktestJob.user_id == user_id)
                .first()
            )
            if not row:
                return None
            row.cancel_requested = 1
            if row.status in ["pending"]:
                row.status = "cancelled"
                row.stage = "cancelled"
            db.commit()
            db.refresh(row)
            return self._to_dict(row)
        finally:
            db.close()

    def retry_job(self, job_id: str, user_id: int) -> dict | None:
        db = SessionLocal()
        try:
            row = (
                db.query(BacktestJob)
                .filter(BacktestJob.job_id == job_id, BacktestJob.user_id == user_id)
                .first()
            )
            if not row:
                return None
            if row.status not in ["failed", "cancelled"]:
                return self._to_dict(row)

            row.status = "pending"
            row.stage = "pending"
            row.progress_pct = 0
            row.total_symbols = 0
            row.processed_symbols = 0
            row.eta_seconds = None
            row.error = None
            row.result = None
            row.cancel_requested = 0
            db.commit()
            db.refresh(row)
            data = self._to_dict(row)
        finally:
            db.close()

        self._spawn_runner(job_id)
        return data

    def _spawn_runner(self, job_id: str):
        if job_id not in self._running_locks:
            self._running_locks[job_id] = Lock()

        def runner():
            lock = self._running_locks[job_id]
            if not lock.acquire(blocking=False):
                return
            try:
                self._run_job(job_id)
            finally:
                lock.release()

        Thread(target=runner, daemon=True).start()

    def _run_job(self, job_id: str):
        db = SessionLocal()
        try:
            job = db.query(BacktestJob).filter(BacktestJob.job_id == job_id).first()
            if not job:
                return
            if job.cancel_requested:
                job.status = "cancelled"
                job.stage = "cancelled"
                db.commit()
                return

            job.status = "running"
            job.stage = "running"
            db.commit()

            req = BacktestRunRequest(**job.request_payload)
            user = db.query(User).filter(User.id == job.user_id).first()

            def progress_cb(update: dict[str, Any]):
                row = db.query(BacktestJob).filter(BacktestJob.job_id == job_id).first()
                if not row:
                    return
                if row.cancel_requested:
                    raise RuntimeError("任务已取消")
                for k, v in update.items():
                    if hasattr(row, k):
                        setattr(row, k, v)
                row.updated_at = datetime.utcnow()
                db.commit()

            try:
                result = self.backtest_service.run_backtest(req, user, db, progress_cb)
                row = db.query(BacktestJob).filter(BacktestJob.job_id == job_id).first()
                if row.cancel_requested:
                    row.status = "cancelled"
                    row.stage = "cancelled"
                else:
                    row.status = "success"
                    row.stage = "done"
                    row.progress_pct = 100
                    row.result = result
                db.commit()
            except Exception as e:
                row = db.query(BacktestJob).filter(BacktestJob.job_id == job_id).first()
                if row:
                    if row.cancel_requested or "任务已取消" in str(e):
                        row.status = "cancelled"
                        row.stage = "cancelled"
                    else:
                        row.status = "failed"
                        row.stage = "failed"
                        row.error = str(e)
                    db.commit()
        finally:
            db.close()

    @staticmethod
    def _to_dict(row: BacktestJob | None) -> dict | None:
        if not row:
            return None
        return deepcopy(
            {
                "job_id": row.job_id,
                "status": row.status,
                "progress_pct": row.progress_pct,
                "total_symbols": row.total_symbols,
                "processed_symbols": row.processed_symbols,
                "eta_seconds": row.eta_seconds,
                "stage": row.stage,
                "error": row.error,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "request_payload": row.request_payload,
                "result": row.result,
            }
        )
