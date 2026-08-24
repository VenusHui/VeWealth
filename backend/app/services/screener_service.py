"""Stock Screener Service — market-wide strategy-based stock screening."""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.security_universe import SecurityUniverse
from app.providers.astock_data import tencent_quote
from app.services.backtest.registry import get_strategy
from app.services.stock_service import stock_service

logger = logging.getLogger(__name__)

# How many days of daily data to fetch per stock (generous window for any strategy)
_SCREENER_LOOKBACK_DAYS = 120

# Max parallel data-fetch workers (I/O bound, GIL not a bottleneck)
_MAX_FETCH_WORKERS = 20

# Batch size for GMM strategy (uses internal multiprocessing)
_GMM_BATCH_SIZE = 200


class ScreenerService:
    """Market-wide stock screening using registered strategies."""

    def __init__(self):
        self._scans: dict[str, dict] = {}
        self._lock = threading.Lock()

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
        """Create a new scan job, start background thread, return initial state."""
        scan_id = f"scan_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

        # Validate strategy exists
        try:
            strategy = get_strategy(strategy_id, require_usable=False)
        except ValueError as e:
            raise ValueError(f"无效策略: {e}")

        # Validate boards
        allowed = {"main", "gem", "star", "bse"}
        invalid = [b for b in boards if b not in allowed]
        if invalid:
            raise ValueError(f"无效板块: {', '.join(invalid)}")
        if not boards:
            raise ValueError("至少选择一个板块")

        # Get universe
        universe = stock_service.get_all_stock_symbols(
            boards=boards, exclude_st=exclude_st
        )
        if not universe:
            raise ValueError("选股池为空，请检查板块设置或行情源连接")

        state: dict[str, Any] = {
            "scan_id": scan_id,
            "status": "scanning",
            "strategy_id": strategy_id,
            "strategy_name": strategy.name,
            "boards": boards,
            "exclude_st": exclude_st,
            "progress": {"total": len(universe), "scanned": 0, "hits": 0},
            "results": [],
            "error": None,
            "created_at": now,
            "completed_at": None,
            "user_id": user_id,
        }

        with self._lock:
            # Cancel any existing scan for this user
            to_remove = [
                sid
                for sid, s in self._scans.items()
                if s.get("user_id") == user_id and s.get("status") == "scanning"
            ]
            for sid in to_remove:
                del self._scans[sid]
            self._scans[scan_id] = state

        thread = threading.Thread(
            target=self._run_scan,
            args=(scan_id, strategy_id, params, universe),
            daemon=True,
        )
        thread.start()

        return self._state_to_response(state)

    def get_scan(self, scan_id: str) -> dict | None:
        """Return current scan state, or None if not found."""
        with self._lock:
            state = self._scans.get(scan_id)
            if state is None:
                return None
            return self._state_to_response(state)

    def list_scans(self, user_id: int) -> list[dict]:
        """Return recent scan list for a user."""
        with self._lock:
            user_scans = [
                s for s in self._scans.values() if s.get("user_id") == user_id
            ]
        user_scans.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        return [
            {
                "scan_id": s["scan_id"],
                "strategy_id": s["strategy_id"],
                "strategy_name": s.get("strategy_name", ""),
                "status": s["status"],
                "boards": s.get("boards", []),
                "exclude_st": s.get("exclude_st", True),
                "total_scanned": s["progress"]["total"],
                "total_hits": s["progress"]["hits"],
                "created_at": s.get("created_at", ""),
            }
            for s in user_scans
        ]

    # ------------------------------------------------------------------
    # Background scan logic
    # ------------------------------------------------------------------

    def _run_scan(
        self,
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

            # Phase 1: parallel data fetching (chunked to bound memory)
            symbol_dfs: dict[str, pd.DataFrame] = {}
            total = len(universe)
            scanned = 0
            _CHUNK = 200

            for chunk_start in range(0, total, _CHUNK):
                chunk = universe[chunk_start : chunk_start + _CHUNK]
                with ThreadPoolExecutor(max_workers=_MAX_FETCH_WORKERS) as executor:
                    future_to_symbol = {
                        executor.submit(
                            self._fetch_stock_data, symbol, start_date, end_date
                        ): symbol
                        for symbol in chunk
                    }
                    for future in as_completed(future_to_symbol):
                        symbol = future_to_symbol[future]
                        try:
                            df = future.result()
                            if df is not None and not df.empty:
                                symbol_dfs[symbol] = df
                        except Exception:
                            logger.warning(f"获取 {symbol} 数据失败，已跳过")
                        scanned += 1

                        if scanned % 50 == 0 or scanned == total:
                            self._update_progress(
                                scan_id, total, scanned, len(symbol_dfs)
                            )

            logger.info(
                "Screener data fetch complete: %d/%d stocks have data",
                len(symbol_dfs),
                total,
            )

            if not symbol_dfs:
                self._mark_failed(scan_id, "所有股票数据获取失败，请检查行情源连接")
                return

            # Phase 2: signal generation
            candidates = self._generate_signals(
                strategy_id, strategy, symbol_dfs, params, scan_id, total
            )

            # Phase 3: enrich with quotes and names
            enriched = self._enrich_candidates(candidates)

            # Phase 4: sort and store
            enriched.sort(key=lambda r: r["signal_strength"], reverse=True)

            with self._lock:
                state = self._scans.get(scan_id)
                if state:
                    state["status"] = "completed"
                    state["progress"]["scanned"] = total
                    state["progress"]["hits"] = len(enriched)
                    state["results"] = enriched
                    state["completed_at"] = datetime.utcnow().strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )

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

    def _generate_signals(
        self,
        strategy_id: str,
        strategy,
        symbol_dfs: dict[str, pd.DataFrame],
        params: dict,
        scan_id: str,
        total: int,
    ) -> list[dict]:
        """Run strategy.generate_candidates() and collect latest-date candidates."""
        all_candidates: list[dict] = []

        if strategy_id == "gmm_volume_v1":
            # GMM: concatenate into one big DataFrame and use internal multiprocessing
            all_dfs = list(symbol_dfs.values())
            for i in range(0, len(all_dfs), _GMM_BATCH_SIZE):
                batch = all_dfs[i : i + _GMM_BATCH_SIZE]
                big_df = pd.concat(batch, ignore_index=True)
                try:
                    cand_df = strategy.generate_candidates(big_df, params)
                    if cand_df is not None and not cand_df.empty:
                        all_candidates.extend(cand_df.to_dict("records"))
                except Exception:
                    logger.exception("GMM generate_candidates batch failed")
                self._update_progress(scan_id, total, total, len(all_candidates))
        else:
            # MA Cross, VSD: per-stock processing (fast enough)
            processed = 0
            for symbol, df in symbol_dfs.items():
                try:
                    cand_df = strategy.generate_candidates(df, params)
                    if cand_df is not None and not cand_df.empty:
                        all_candidates.extend(cand_df.to_dict("records"))
                except Exception:
                    logger.warning(f"策略计算失败 {symbol}")
                processed += 1
                if processed % 100 == 0:
                    self._update_progress(scan_id, total, total, len(all_candidates))

        # Filter to latest trade_date only
        if not all_candidates:
            return []

        latest_date = max(
            str(c.get("trade_date", ""))[:10]
            for c in all_candidates
            if c.get("trade_date")
        )

        latest = [
            c
            for c in all_candidates
            if str(c.get("trade_date", ""))[:10] == latest_date
        ]

        # Deduplicate by symbol (keep highest signal_strength)
        seen: dict[str, dict] = {}
        for c in latest:
            sym = str(c.get("symbol", "")).strip()
            if not sym:
                continue
            strength = float(c.get("signal_strength", 0))
            if sym not in seen or strength > float(seen[sym].get("signal_strength", 0)):
                seen[sym] = c

        return list(seen.values())

    def _enrich_candidates(self, candidates: list[dict]) -> list[dict]:
        """Add stock names and real-time quotes to candidate list."""
        if not candidates:
            return []

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
            q = quotes.get(sym, {})
            item = {
                "symbol": sym,
                "stock_name": names.get(sym) or q.get("name"),
                "signal_strength": round(float(c.get("signal_strength", 0)), 6),
                "reason": str(c.get("reason", "")),
                "current_price": q.get("price"),
                "change_pct": q.get("change_pct"),
            }
            enriched.append(item)

        return enriched

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_progress(
        self, scan_id: str, total: int, scanned: int, hits: int
    ) -> None:
        with self._lock:
            state = self._scans.get(scan_id)
            if state:
                state["progress"] = {
                    "total": total,
                    "scanned": scanned,
                    "hits": hits,
                }

    def _mark_failed(self, scan_id: str, error: str) -> None:
        with self._lock:
            state = self._scans.get(scan_id)
            if state:
                state["status"] = "failed"
                state["error"] = error
                state["completed_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    def _state_to_response(self, state: dict) -> dict:
        return {
            "scan_id": state["scan_id"],
            "status": state["status"],
            "strategy_id": state.get("strategy_id", ""),
            "boards": state.get("boards", []),
            "exclude_st": state.get("exclude_st", True),
            "progress": state.get("progress", {"total": 0, "scanned": 0, "hits": 0}),
            "results": state.get("results", []),
            "error": state.get("error"),
            "created_at": state.get("created_at", ""),
            "completed_at": state.get("completed_at"),
        }


# Global singleton
screener_service = ScreenerService()
