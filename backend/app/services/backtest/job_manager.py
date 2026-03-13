"""回测离线任务管理（内存版）"""

from __future__ import annotations

from datetime import datetime
from threading import Thread, Lock
from uuid import uuid4
from copy import deepcopy

from app.schemas.backtest import BacktestRunRequest


class BacktestJobManager:
    def __init__(self, backtest_service):
        self.backtest_service = backtest_service
        self._jobs: dict[str, dict] = {}
        self._lock = Lock()

    def create_job(self, request: BacktestRunRequest, current_user, db) -> dict:
        job_id = uuid4().hex[:12]
        now = datetime.utcnow()
        job = {
            "job_id": job_id,
            "status": "pending",
            "progress_pct": 0.0,
            "total_symbols": 0,
            "processed_symbols": 0,
            "eta_seconds": None,
            "stage": "pending",
            "error": None,
            "created_at": now,
            "updated_at": now,
            "request_payload": request.model_dump(mode="json"),
            "result": None,
        }
        with self._lock:
            self._jobs[job_id] = job

        req_copy = BacktestRunRequest(**request.model_dump())
        user_id = current_user.id

        def runner():
            self._update(job_id, status="running", stage="running")
            try:
                # 估算扫描数量
                if req_copy.mode == "strategy_select":
                    if req_copy.universe_type == "custom":
                        total_symbols = len(req_copy.pool_symbols)
                    else:
                        total_symbols = int(
                            req_copy.strategy_params.get("max_universe_size", 300)
                        )
                else:
                    total_symbols = len(req_copy.symbols)

                self._update(job_id, total_symbols=total_symbols)

                # 使用新Session避免跨线程复用请求session
                from app.core.database import SessionLocal
                from app.models.user import User

                thread_db = SessionLocal()
                try:
                    user = thread_db.query(User).filter(User.id == user_id).first()
                    result = self.backtest_service.run_backtest(
                        req_copy, user, thread_db
                    )
                    self._update(
                        job_id,
                        status="success",
                        stage="done",
                        progress_pct=100.0,
                        processed_symbols=total_symbols,
                        result=result,
                    )
                finally:
                    thread_db.close()
            except Exception as e:
                self._update(job_id, status="failed", stage="failed", error=str(e))

        Thread(target=runner, daemon=True).start()
        return deepcopy(job)

    def list_jobs(self) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda x: x["created_at"], reverse=True)
        return [deepcopy(j) for j in jobs]

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return deepcopy(job) if job else None

    def _update(self, job_id: str, **kwargs):
        with self._lock:
            if job_id not in self._jobs:
                return
            self._jobs[job_id].update(kwargs)
            self._jobs[job_id]["updated_at"] = datetime.utcnow()
