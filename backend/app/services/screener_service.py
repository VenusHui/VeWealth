"""Stock Screener Service — market-wide strategy-based stock screening.

选股任务持久化到 `screener_jobs` 表，配合有界后台执行器实现：
- 进度/结果可在刷新/重启后恢复；
- 取消（cancel_requested / CancelToken）在 5 秒内协作式停止取数与计算；
- 按用户隔离：get_scan 必须携带 user_id，跨用户查询返回 None（路由层 404）；
- 同用户幂等：相同请求复用进行中的扫描，不同请求拒绝重复提交。
"""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.screener_job import ScreenerJob
from app.models.security_universe import SecurityUniverse
from app.providers.astock_data import tencent_quote
from app.services.evaluator import (
    EvaluationCounters,
    attach_liquidity_dict,
    candidate_date,
    dedupe_by_symbol,
    determine_as_of_date,
    is_stale,
    normalize_score,
    rank_candidates,
)
from app.services.backtest.registry import get_strategy
from app.services.stock_service import stock_service
from app.services.task_executor import CancelToken, background_task_executor

logger = logging.getLogger(__name__)

# How many days of daily data to fetch per stock (generous window for any strategy)
_SCREENER_LOOKBACK_DAYS = 120

# Max parallel data-fetch workers (I/O bound, GIL not a bottleneck)
_MAX_FETCH_WORKERS = 20

# Batch size for GMM strategy (uses internal multiprocessing)
_GMM_BATCH_SIZE = 200

# Restart recovery: active (pending/running) scans touching updated_at older than
# this many minutes are marked cancelled, since their worker thread died with the process.
_RESTART_RECOVERY_TIMEOUT_MINUTES = 5
_RESTART_RECOVERY_ERROR = "服务重启中断，请手动重试"

# Active statuses used for idempotency / duplicate-submission checks
_ACTIVE_STATUSES = ("pending", "running")

# 前端契约：扫描进行中统一暴露为 "scanning"，终态为 completed/failed。
# pending/running 是后端持久化与取消协作所需的内部状态，不对前端暴露。
_FRONTEND_CONTRACT_STATUS = {
    "pending": "scanning",
    "running": "scanning",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


def _contract_status(status: str) -> str:
    """把内部持久化状态映射为前端已知的 status 契约。

    未升级前端仅在 status == "scanning" 时轮询刷新；若直接暴露
    pending/running，frontend 会因收到未知状态而停止轮询，扫描看似卡死。
    """
    return _FRONTEND_CONTRACT_STATUS.get(status, status)


class ScreenerService:
    """Market-wide stock screening using registered strategies."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_scan(
        self,
        strategy_id: str,
        params: dict[str, Any],
        boards: list[str],
        exclude_st: bool,
        user_id: int,
    ) -> dict:
        """Create a new scan job, enqueue background work, return initial state.

        幂等/防重：同一用户已有一个 active 扫描时——
        - 请求完全一致：复用该扫描（返回其当前状态）；
        - 请求不同：拒绝，避免并发的线程/资源堆叠。
        """
        try:
            strategy = get_strategy(strategy_id, require_usable=False)
        except ValueError as e:
            raise ValueError(f"无效策略: {e}")

        allowed = {"main", "gem", "star", "bse"}
        invalid = [b for b in boards if b not in allowed]
        if invalid:
            raise ValueError(f"无效板块: {', '.join(invalid)}")
        if not boards:
            raise ValueError("至少选择一个板块")

        universe = stock_service.get_all_stock_symbols(
            boards=boards, exclude_st=exclude_st
        )
        if not universe:
            raise ValueError("选股池为空，请检查板块设置或行情源连接")

        db: Session = SessionLocal()
        try:
            existing = self._find_active_for_user(db, user_id)
            if existing:
                if self._same_request(
                    existing, strategy_id, params, boards, exclude_st
                ):
                    logger.info(
                        "用户 %s 已有相同扫描 %s，幂等复用", user_id, existing.scan_id
                    )
                    return self._to_response(existing)
                raise ValueError("已有进行中的扫描任务，请先取消或等待其完成")

            scan_id = f"scan_{uuid.uuid4().hex[:12]}"
            now = datetime.utcnow()
            job = ScreenerJob(
                scan_id=scan_id,
                user_id=user_id,
                strategy_id=strategy_id,
                strategy_name=strategy.name,
                strategy_params=dict(params),
                boards=list(boards),
                exclude_st=1 if exclude_st else 0,
                status="pending",
                stage="pending",
                progress={
                    "total": len(universe),
                    "fetched": 0,
                    "data_ok": 0,
                    "data_failed": 0,
                    "evaluated": 0,
                    "signal_hits": 0,
                    "rejected": 0,
                    "stale_data_count": 0,
                    "as_of_date": None,
                },
                result=None,
                error=None,
                cancel_requested=0,
                created_at=now,
                updated_at=now,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            response = self._to_response(job)
        finally:
            db.close()

        try:
            background_task_executor.submit(
                scan_id,
                self._run_scan,
                scan_id,
                strategy_id,
                params,
                universe,
            )
        except ValueError as e:
            # 队列已满：任务无法入队。DB 里已持久化的 pending 行必须立刻标记为
            # failed，否则会成为一个"永不运行"的孤儿任务，且占住该用户的幂等名额。
            logger.warning("扫描 %s 入队失败，标记为 failed: %s", scan_id, e)
            self._mark_failed(scan_id, str(e))
            raise
        return response

    def get_scan(self, scan_id: str, user_id: int) -> dict | None:
        """Return current scan state for a user, or None if not found / not owned."""
        db: Session = SessionLocal()
        try:
            row = (
                db.query(ScreenerJob)
                .filter(ScreenerJob.scan_id == scan_id, ScreenerJob.user_id == user_id)
                .first()
            )
            return self._to_response(row) if row else None
        finally:
            db.close()

    def list_scans(self, user_id: int, limit: int = 50) -> list[dict]:
        """Return recent scan list for a user."""
        db: Session = SessionLocal()
        try:
            rows = (
                db.query(ScreenerJob)
                .filter(ScreenerJob.user_id == user_id)
                .order_by(ScreenerJob.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._to_list_item(r) for r in rows]
        finally:
            db.close()

    def cancel_scan(self, scan_id: str, user_id: int) -> dict | None:
        """Request cancel of a scan. Pending scans flip to cancelled immediately;
        running scans set cancel_requested and the worker cooperatively stops."""
        db: Session = SessionLocal()
        try:
            row = (
                db.query(ScreenerJob)
                .filter(ScreenerJob.scan_id == scan_id, ScreenerJob.user_id == user_id)
                .first()
            )
            if not row:
                return None
            row.cancel_requested = 1
            if row.status == "pending":
                row.status = "cancelled"
                row.stage = "cancelled"
                row.completed_at = datetime.utcnow()
                row.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(row)
        finally:
            db.close()

        background_task_executor.cancel(scan_id)
        return self.get_scan(scan_id, user_id)

    def recover_stale_jobs_on_startup(
        self,
        timeout_minutes: int = _RESTART_RECOVERY_TIMEOUT_MINUTES,
        error_message: str = _RESTART_RECOVERY_ERROR,
    ) -> int:
        """Mark active scans left dangling by a restart as cancelled."""
        cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        db: Session = SessionLocal()
        try:
            stale_rows = (
                db.query(ScreenerJob)
                .filter(
                    ScreenerJob.status.in_(_ACTIVE_STATUSES),
                    ScreenerJob.updated_at < cutoff,
                )
                .all()
            )
            if not stale_rows:
                logger.info(
                    "选股任务重启恢复完成，无需处理（阈值=%s分钟）", timeout_minutes
                )
                return 0

            now = datetime.utcnow()
            for row in stale_rows:
                row.status = "cancelled"
                row.stage = "cancelled"
                row.error = error_message
                row.cancel_requested = 1
                row.completed_at = now
                row.updated_at = now
            db.commit()
            logger.warning(
                "选股任务重启恢复完成，已取消 %s 个卡住任务（阈值=%s分钟）: %s",
                len(stale_rows),
                timeout_minutes,
                ", ".join(r.scan_id for r in stale_rows),
            )
            return len(stale_rows)
        except Exception:
            db.rollback()
            logger.error("选股任务重启恢复失败", exc_info=True)
            raise
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Background scan logic
    # ------------------------------------------------------------------

    def _run_scan(
        self,
        token: CancelToken,
        scan_id: str,
        strategy_id: str,
        params: dict,
        universe: list[str],
    ) -> None:
        try:
            strategy = get_strategy(strategy_id, require_usable=False)
            end_date = datetime.utcnow().strftime("%Y-%m-%d")
            start_date = (
                datetime.utcnow() - timedelta(days=_SCREENER_LOOKBACK_DAYS)
            ).strftime("%Y-%m-%d")

            self._mark_running(scan_id)
            if self._is_cancelled(token, scan_id):
                self._mark_cancelled(scan_id)
                return

            # Phase 1: parallel data fetching (chunked to bound memory)
            symbol_dfs: dict[str, pd.DataFrame] = {}
            total = len(universe)
            processed = 0
            counters = EvaluationCounters()
            _CHUNK = 200

            for chunk_start in range(0, total, _CHUNK):
                if self._is_cancelled(token, scan_id):
                    self._mark_cancelled(scan_id)
                    return
                chunk = universe[chunk_start : chunk_start + _CHUNK]
                executor = ThreadPoolExecutor(
                    max_workers=_MAX_FETCH_WORKERS, thread_name_prefix="vwe-fetch"
                )
                try:
                    future_to_symbol = {
                        executor.submit(
                            self._fetch_stock_data, symbol, start_date, end_date
                        ): symbol
                        for symbol in chunk
                    }
                    for future in as_completed(future_to_symbol):
                        # per-symbol 只检查内存 token（廉价）；DB 的 cancel_requested
                        # 在进度检查点（每50个）才查，避免每个 symbol 都开一次会话。
                        if token.is_cancelled():
                            self._mark_cancelled(scan_id)
                            return
                        symbol = future_to_symbol[future]
                        try:
                            df = future.result()
                            if df is not None and not df.empty:
                                symbol_dfs[symbol] = df
                        except Exception:
                            logger.warning(f"获取 {symbol} 数据失败，已跳过")
                        processed += 1

                        if processed % 50 == 0 or processed == total:
                            if self._is_cancelled(token, scan_id):
                                self._mark_cancelled(scan_id)
                                return
                            counters.fetched = len(symbol_dfs)
                            counters.data_failed = processed - counters.fetched
                            self._update_progress(scan_id, counters, total)
                finally:
                    # 不阻塞等待仍在跑的 fetch 线程，尽快让出 worker 槽位
                    executor.shutdown(wait=False, cancel_futures=True)

            logger.info(
                "Screener data fetch complete: %d/%d stocks have data",
                len(symbol_dfs),
                total,
            )

            if self._is_cancelled(token, scan_id):
                self._mark_cancelled(scan_id)
                return

            if not symbol_dfs:
                self._mark_failed(scan_id, "所有股票数据获取失败，请检查行情源连接")
                return

            # as_of_date：全市场最新数据日，后续只允许该日信号，杜绝旧信号冒充当前结果。
            as_of_date = determine_as_of_date(symbol_dfs)

            # Phase 2: signal generation + as-of/stale classification + ranking
            candidates, counters = self._evaluate(
                token,
                strategy_id,
                strategy,
                symbol_dfs,
                params,
                scan_id,
                as_of_date,
                total,
            )
            if self._is_cancelled(token, scan_id):
                self._mark_cancelled(scan_id)
                return

            # Phase 3: enrich with quotes, names and normalized strategy score
            enriched = self._enrich_candidates(candidates, strategy)

            # Phase 4: store
            self._complete_scan(scan_id, total, counters, as_of_date, enriched)
        except Exception as e:
            logger.exception("Scan %s failed", scan_id)
            self._mark_failed(scan_id, str(e))

    def _fetch_stock_data(
        self, symbol: str, start_date: str, end_date: str
    ) -> pd.DataFrame | None:
        """Fetch daily data for a single stock. Returns None on failure."""
        try:
            df, _, _ = stock_service.get_daily_data(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
            if df is None or df.empty:
                return None
            df = df.copy()
            df["symbol"] = symbol
            return df
        except Exception:
            return None

    def _evaluate(
        self,
        token: CancelToken,
        strategy_id: str,
        strategy,
        symbol_dfs: dict[str, pd.DataFrame],
        params: dict,
        scan_id: str,
        as_of_date: str | None,
        total: int,
    ) -> tuple[list[dict], EvaluationCounters]:
        """生成候选并按 as_of_date 分类、去重、排序。

        只有 `as_of_date` 当日信号才算候选；数据末日落后者计入 stale_data_count。
        返回 (排名后的候选, 计数)。counters.signal_hits 为 as_of_date 当日命中的去重
        股票数，signal_hits + rejected == evaluated。
        """
        counters = EvaluationCounters()
        all_candidates: list[dict] = []
        counters.fetched = len(symbol_dfs)
        counters.data_failed = total - counters.fetched

        if strategy_id == "gmm_volume_v1":
            # GMM: concatenate into one big DataFrame and use internal multiprocessing
            all_dfs = list(symbol_dfs.values())
            for i in range(0, len(all_dfs), _GMM_BATCH_SIZE):
                if self._is_cancelled(token, scan_id):
                    return [], counters
                batch = all_dfs[i : i + _GMM_BATCH_SIZE]
                big_df = pd.concat(batch, ignore_index=True)
                try:
                    cand_df = strategy.generate_candidates(big_df, params)
                    if cand_df is not None and not cand_df.empty:
                        all_candidates.extend(cand_df.to_dict("records"))
                except Exception:
                    logger.exception("GMM generate_candidates batch failed")
        else:
            # MA Cross, VSD: per-stock processing (fast enough)
            for symbol, df in symbol_dfs.items():
                if self._is_cancelled(token, scan_id):
                    return [], counters
                try:
                    cand_df = strategy.generate_candidates(df, params)
                    if cand_df is not None and not cand_df.empty:
                        all_candidates.extend(cand_df.to_dict("records"))
                except Exception:
                    logger.warning(f"策略计算失败 {symbol}")

        if not as_of_date:
            # 无法确定 as_of_date（数据缺 datetime/trade_date 的退化路径），不产出任何
            # 候选。为保持 data_ok == fetched - stale_data_count 不变式（此时 data_ok==0、
            # fetched>0），把全量已取数归为 stale（无参考日期 → 数据不可用）。
            counters.stale_data_count = counters.fetched
            counters.data_ok = 0
            self._update_progress(scan_id, counters, total)
            return [], counters

        # as-of 过滤 + 按 symbol 去重（保留 signal_strength 最高）
        as_of_candidates: dict[str, dict] = {}
        for c in all_candidates:
            sym = str(c.get("symbol", "")).strip()
            if not sym:
                continue
            d = candidate_date(c)
            if d != as_of_date:
                # 非 as_of_date 当日信号：一律丢弃，不冒充当前结果。
                continue
            score = float(c.get("signal_strength", 0) or 0)
            cur = as_of_candidates.get(sym)
            if cur is None or score > float(cur.get("signal_strength", 0) or 0):
                as_of_candidates[sym] = c

        # 逐 symbol 分类计数：stale / data_ok / evaluated / signal_hits / rejected
        for symbol, df in symbol_dfs.items():
            if is_stale(df, as_of_date):
                counters.stale_data_count += 1
                continue
            counters.data_ok += 1
            counters.evaluated += 1
            if symbol in as_of_candidates:
                counters.signal_hits += 1
            else:
                counters.rejected += 1

        self._update_progress(scan_id, counters, total)
        # 注入真实流动性（as_of_date 窗口内日均成交额），使 score → liquidity → symbol
        # 的业务意义 tie-break 真正生效，而非 liquidity 恒为 0。
        as_of_hits = list(as_of_candidates.values())
        with_liquidity = attach_liquidity_dict(as_of_hits, symbol_dfs, as_of_date)
        ranked = rank_candidates(with_liquidity)
        return dedupe_by_symbol(ranked), counters

    def _enrich_candidates(self, candidates: list[dict], strategy) -> list[dict]:
        """Add stock names, real-time quotes and normalized strategy score."""
        if not candidates:
            return []

        score_range = getattr(strategy, "score_range", None)

        symbols = [
            str(c.get("symbol", "")).strip()
            for c in candidates
            if str(c.get("symbol", "")).strip()
        ]
        unique_symbols = list(dict.fromkeys(symbols))

        # Batch real-time quotes
        quotes: dict[str, dict] = {}
        try:
            quotes = tencent_quote(unique_symbols)
        except Exception:
            logger.warning("批量获取实时行情失败")

        # Batch stock names from SecurityUniverse
        names: dict[str, str] = {}
        try:
            db: Session = SessionLocal()
            try:
                rows = (
                    db.query(SecurityUniverse.stock_code, SecurityUniverse.stock_name)
                    .filter(SecurityUniverse.stock_code.in_(unique_symbols))
                    .all()
                )
                names = {str(r[0]).zfill(6): str(r[1]) for r in rows if r[0] and r[1]}
            finally:
                db.close()
        except Exception:
            logger.warning("获取股票名称失败")

        enriched: list[dict] = []
        for c in candidates:
            sym = str(c.get("symbol", "")).strip().zfill(6)
            raw_score = round(float(c.get("signal_strength", 0)), 6)
            q = quotes.get(sym, {})
            item = {
                "symbol": sym,
                "stock_name": names.get(sym) or q.get("name"),
                # 原始单调强度（用于排序/内部比较）
                "signal_strength": raw_score,
                # 归一化 [0,1] 策略评分（前端条形与数值同尺度展示）
                "strategy_score": round(normalize_score(raw_score, score_range), 4),
                "reason": str(c.get("reason", "")),
                "current_price": q.get("price"),
                "change_pct": q.get("change_pct"),
            }
            enriched.append(item)

        return enriched

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _find_active_for_user(self, db: Session, user_id: int) -> ScreenerJob | None:
        return (
            db.query(ScreenerJob)
            .filter(
                ScreenerJob.user_id == user_id,
                ScreenerJob.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(ScreenerJob.created_at.desc())
            .first()
        )

    @staticmethod
    def _same_request(
        job: ScreenerJob,
        strategy_id: str,
        params: dict[str, Any],
        boards: list[str],
        exclude_st: bool,
    ) -> bool:
        return (
            job.strategy_id == strategy_id
            and json.dumps(job.strategy_params or {}, sort_keys=True, default=str)
            == json.dumps(params or {}, sort_keys=True, default=str)
            and list(job.boards or []) == list(boards)
            and bool(job.exclude_st) == bool(exclude_st)
        )

    def _is_cancelled(self, token: CancelToken, scan_id: str) -> bool:
        if token.is_cancelled():
            return True
        db: Session = SessionLocal()
        try:
            row = (
                db.query(ScreenerJob.cancel_requested)
                .filter(ScreenerJob.scan_id == scan_id)
                .first()
            )
            return bool(row and row[0])
        except Exception:
            return False
        finally:
            db.close()

    def _mark_running(self, scan_id: str) -> None:
        self._apply_status(scan_id, "running", stage="running")

    def _mark_failed(self, scan_id: str, error: str) -> None:
        db: Session = SessionLocal()
        try:
            self._apply_status(scan_id, "failed", stage="failed", error=error, db=db)
        finally:
            db.close()

    def _mark_cancelled(self, scan_id: str) -> None:
        db: Session = SessionLocal()
        try:
            self._apply_status(scan_id, "cancelled", stage="cancelled", db=db)
        finally:
            db.close()

    def _update_progress(
        self, scan_id: str, counters: EvaluationCounters, total: int
    ) -> None:
        db: Session = SessionLocal()
        try:
            self._apply_progress(scan_id, counters, total, db=db)
        finally:
            db.close()

    def _complete_scan(
        self,
        scan_id: str,
        total: int,
        counters: EvaluationCounters,
        as_of_date: str | None,
        enriched: list[dict],
    ) -> None:
        db: Session = SessionLocal()
        try:
            row = db.query(ScreenerJob).filter(ScreenerJob.scan_id == scan_id).first()
            if not row:
                return
            if row.cancel_requested:
                row.status = "cancelled"
                row.stage = "cancelled"
            else:
                row.status = "completed"
                row.stage = "done"
                row.progress = counters.to_progress(total)
                row.progress["as_of_date"] = as_of_date
                row.result = enriched
            row.completed_at = datetime.utcnow()
            row.updated_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()

    def _apply_status(
        self,
        scan_id: str,
        status: str,
        stage: str | None = None,
        error: str | None = None,
        db: Session | None = None,
    ) -> None:
        owns_db = db is None
        session = db or SessionLocal()
        try:
            row = (
                session.query(ScreenerJob)
                .filter(ScreenerJob.scan_id == scan_id)
                .first()
            )
            if not row:
                return
            row.status = status
            if stage:
                row.stage = stage
            if error is not None:
                row.error = error
            if status in ("completed", "failed", "cancelled"):
                row.completed_at = datetime.utcnow()
            row.updated_at = datetime.utcnow()
            session.commit()
        finally:
            if owns_db:
                session.close()

    def _apply_progress(
        self,
        scan_id: str,
        counters: EvaluationCounters,
        total: int,
        db: Session | None = None,
    ) -> None:
        owns_db = db is None
        session = db or SessionLocal()
        try:
            row = (
                session.query(ScreenerJob)
                .filter(ScreenerJob.scan_id == scan_id)
                .first()
            )
            if not row:
                return
            row.progress = counters.to_progress(total)
            row.updated_at = datetime.utcnow()
            session.commit()
        finally:
            if owns_db:
                session.close()

    @staticmethod
    def _fmt_progress(progress: dict) -> dict:
        """前端可见进度：漏斗字段 + as_of_date，字段不可复用。"""
        return {
            "total": progress.get("total", 0),
            "fetched": progress.get("fetched", 0),
            "data_ok": progress.get("data_ok", 0),
            "data_failed": progress.get("data_failed", 0),
            "evaluated": progress.get("evaluated", 0),
            "signal_hits": progress.get("signal_hits", 0),
            "rejected": progress.get("rejected", 0),
            "stale_data_count": progress.get("stale_data_count", 0),
            "as_of_date": progress.get("as_of_date"),
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt(value: datetime | None) -> str | None:
        return value.strftime("%Y-%m-%dT%H:%M:%S") if value else None

    def _to_response(self, job: ScreenerJob | None) -> dict | None:
        if not job:
            return None
        progress = job.progress or {}
        return {
            "scan_id": job.scan_id,
            "status": _contract_status(job.status),
            "strategy_id": job.strategy_id,
            "strategy_name": job.strategy_name,
            "boards": job.boards or [],
            "exclude_st": bool(job.exclude_st),
            "progress": self._fmt_progress(progress),
            "as_of_date": progress.get("as_of_date"),
            "results": job.result or [],
            "error": job.error,
            "created_at": self._fmt(job.created_at) or "",
            "completed_at": self._fmt(job.completed_at),
        }

    def _to_list_item(self, job: ScreenerJob) -> dict:
        progress = job.progress or {}
        return {
            "scan_id": job.scan_id,
            "strategy_id": job.strategy_id,
            "strategy_name": job.strategy_name or "",
            "status": _contract_status(job.status),
            "boards": job.boards or [],
            "exclude_st": bool(job.exclude_st),
            "total_scanned": progress.get("total", 0),
            "total_hits": progress.get("signal_hits", 0),
            "created_at": self._fmt(job.created_at) or "",
        }


# Global singleton
screener_service = ScreenerService()
