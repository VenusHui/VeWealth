"""回测服务"""

from collections import defaultdict, deque
from datetime import datetime, timedelta
import re
from typing import Any, Callable

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from app.models.backtest import BacktestRun, BacktestRound
from app.models.backtest_job import BacktestJob
from app.models.security_universe import SecurityUniverse
from app.models.user import User
from app.schemas.backtest import BacktestRunRequest
from app.services.backtest.costs import CostModel
from app.services.backtest.engine import SecurityRule, run_portfolio
from app.services.backtest.metrics import calc_summary
from app.services.backtest.policies.base import PolicyContext
from app.services.backtest.policies.registry import resolve_profile
from app.services.backtest.registry import (
    STRATEGY_REGISTRY,
    get_strategy,
    list_strategies,
)
from app.services.stock_service import stock_service

#: 在 min_history_bars 之上追加的 warmup 缓冲（覆盖滚动指标首段/重拟合）
WARMUP_BUFFER_BARS = 30
#: 交易日约占自然日比例，用于把 bar 数换算成回拉自然日（含节假日冗余）
_CALENDAR_FACTOR = 1.6


class BacktestService:
    def list_strategies(self) -> list[dict]:
        return list_strategies()

    def _normalize_symbol_code(self, symbol: Any) -> str:
        raw = str(symbol or "").strip()
        if not raw:
            return ""
        match = re.search(r"(\d{6})", raw)
        return match.group(1) if match else ""

    def _get_symbol_name_map(self, db: Session, symbols: list[str]) -> dict[str, str]:
        normalized = [self._normalize_symbol_code(s) for s in symbols if str(s).strip()]
        normalized = [s for s in normalized if s]
        if not normalized:
            return {}

        rows = (
            db.query(SecurityUniverse.stock_code, SecurityUniverse.stock_name)
            .filter(SecurityUniverse.stock_code.in_(list(set(normalized))))
            .all()
        )
        return {
            self._normalize_symbol_code(code): str(name)
            for code, name in rows
            if code and name and self._normalize_symbol_code(code)
        }

    def _enrich_trades_with_stock_name(
        self, db: Session, trades: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        symbol_map = self._get_symbol_name_map(
            db,
            [t.get("symbol") for t in trades if isinstance(t, dict)],
        )
        enriched: list[dict[str, Any]] = []
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            item = dict(trade)
            code = self._normalize_symbol_code(item.get("symbol"))
            if code and symbol_map.get(code):
                item["stock_name"] = symbol_map.get(code)
            enriched.append(item)
        return enriched

    def _enrich_rounds_with_stock_name(
        self, db: Session, rounds: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        symbol_map = self._get_symbol_name_map(
            db,
            [r.get("symbol") for r in rounds if isinstance(r, dict)],
        )
        enriched: list[dict[str, Any]] = []
        for row in rounds:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            code = self._normalize_symbol_code(item.get("symbol"))
            if code and symbol_map.get(code):
                item["stock_name"] = symbol_map.get(code)
            enriched.append(item)
        return enriched

    def _enrich_snapshots_with_stock_name(
        self, db: Session, snapshots: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        all_symbols: list[str] = []
        for snapshot in snapshots:
            for h in (
                (snapshot.get("holdings") or []) if isinstance(snapshot, dict) else []
            ):
                if isinstance(h, dict) and h.get("symbol"):
                    all_symbols.append(str(h.get("symbol")))

        symbol_map = self._get_symbol_name_map(db, all_symbols)
        enriched_snapshots: list[dict[str, Any]] = []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            holdings = []
            for h in snapshot.get("holdings") or []:
                if not isinstance(h, dict):
                    continue
                item = dict(h)
                code = self._normalize_symbol_code(item.get("symbol"))
                if code and symbol_map.get(code):
                    item["stock_name"] = symbol_map.get(code)
                holdings.append(item)
            enriched_snapshots.append({**snapshot, "holdings": holdings})
        return enriched_snapshots

    def _extract_trade_date(self, value: Any) -> str | None:
        text = str(value or "").strip()
        if len(text) >= 10:
            return text[:10]
        return None

    def get_universe_stats(self, db: Session) -> dict[str, Any]:
        base_query = db.query(SecurityUniverse).filter(
            SecurityUniverse.is_active.is_(True)
        )

        total = base_query.count()
        st_total = base_query.filter(SecurityUniverse.is_st.is_(True)).count()

        board_rows = (
            db.query(
                SecurityUniverse.board,
                func.count(SecurityUniverse.id).label("count"),
            )
            .filter(SecurityUniverse.is_active.is_(True))
            .group_by(SecurityUniverse.board)
            .all()
        )
        by_board = {str(board): int(count) for board, count in board_rows}

        board_rows_ex_st = (
            db.query(
                SecurityUniverse.board,
                func.count(SecurityUniverse.id).label("count"),
            )
            .filter(
                SecurityUniverse.is_active.is_(True),
                SecurityUniverse.is_st.is_(False),
            )
            .group_by(SecurityUniverse.board)
            .all()
        )
        by_board_ex_st = {str(board): int(count) for board, count in board_rows_ex_st}

        return {
            "total_active": total,
            "st_active": st_total,
            "non_st_active": max(total - st_total, 0),
            "by_board": {
                "main": by_board.get("main", 0),
                "gem": by_board.get("gem", 0),
                "star": by_board.get("star", 0),
                "bse": by_board.get("bse", 0),
            },
            "by_board_exclude_st": {
                "main": by_board_ex_st.get("main", 0),
                "gem": by_board_ex_st.get("gem", 0),
                "star": by_board_ex_st.get("star", 0),
                "bse": by_board_ex_st.get("bse", 0),
            },
            "defaults": {
                "boards": ["main"],
                "exclude_st": True,
            },
        }

    def get_scan_observability(
        self, current_user: User, db: Session, recent_limit: int = 50
    ) -> dict[str, Any]:
        """全市场扫描运行观测聚合。

        一次性返回观测面板所需的完整视图：
        - 股票池覆盖统计（universe）
        - 进行中的回测任务（active_jobs，含进度与阶段）
        - 最近完成的全市场扫描记录（recent_scan_runs，含诊断字段）
        - 任务/记录汇总计数（counters）
        """
        user_id = current_user.id

        active_rows = (
            db.query(BacktestJob)
            .filter(
                BacktestJob.user_id == user_id,
                BacktestJob.status.in_(["pending", "running"]),
            )
            .order_by(BacktestJob.created_at.desc())
            .limit(20)
            .all()
        )
        active_jobs: list[dict[str, Any]] = []
        for r in active_rows:
            payload = r.request_payload or {}
            active_jobs.append(
                {
                    "job_id": r.job_id,
                    "name": payload.get("name") or r.job_id,
                    "strategy_id": payload.get("strategy_id"),
                    "status": r.status,
                    "stage": r.stage,
                    "progress_pct": r.progress_pct,
                    "total_symbols": r.total_symbols,
                    "processed_symbols": r.processed_symbols,
                    "eta_seconds": r.eta_seconds,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
            )

        job_status_counts: dict[str, int] = {
            "pending": 0,
            "running": 0,
            "success": 0,
            "failed": 0,
            "cancelled": 0,
        }
        job_status_rows = (
            db.query(BacktestJob.status, func.count(BacktestJob.id))
            .filter(BacktestJob.user_id == user_id)
            .group_by(BacktestJob.status)
            .all()
        )
        for status_value, cnt in job_status_rows:
            job_status_counts[str(status_value)] = int(cnt)

        recent_rows = (
            db.query(BacktestRun)
            .options(
                load_only(
                    BacktestRun.id,
                    BacktestRun.name,
                    BacktestRun.status,
                    BacktestRun.strategy_id,
                    BacktestRun.start_date,
                    BacktestRun.end_date,
                    BacktestRun.summary,
                    BacktestRun.warnings,
                    BacktestRun.created_at,
                )
            )
            .filter(BacktestRun.user_id == user_id)
            .order_by(BacktestRun.created_at.desc())
            .limit(recent_limit)
            .all()
        )
        recent_scan_runs: list[dict[str, Any]] = []
        for r in recent_rows:
            summary = r.summary or {}
            diagnostics = None
            if isinstance(summary, dict):
                diagnostics = summary.get("diagnostics")
            if not diagnostics:
                continue
            recent_scan_runs.append(
                {
                    "run_id": r.id,
                    "name": r.name,
                    "strategy_id": r.strategy_id,
                    "status": r.status,
                    "start_date": r.start_date,
                    "end_date": r.end_date,
                    "summary": summary,
                    "diagnostics": diagnostics,
                    "warnings": r.warnings or [],
                    "created_at": r.created_at,
                }
            )

        total_runs = (
            db.query(func.count(BacktestRun.id))
            .filter(BacktestRun.user_id == user_id)
            .scalar()
        )

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "universe": self.get_universe_stats(db),
            "active_jobs": active_jobs,
            "recent_scan_runs": recent_scan_runs,
            "counters": {
                "jobs": job_status_counts,
                "runs": {
                    "total": int(total_runs or 0),
                    "recent_scan_count": len(recent_scan_runs),
                },
            },
        }

    def _history_min_bars(self, strategy_id: str) -> int:
        """返回策略声明的最小历史 bar 数（用于 warmup 回拉）。"""
        strategy_cls = STRATEGY_REGISTRY.get(strategy_id)
        if not strategy_cls:
            return 0
        value = getattr(strategy_cls, "min_history_bars", 0)
        return int(value) if isinstance(value, int) else 0

    def _fetch_daily_with_warmup(
        self, symbol: str, start_date: str, end_date: str, strategy_id: str
    ):
        """按策略 min_history_bars + warmup 自动回拉起始日期，保证足量 bar。

        返回 (df, effective_start_date)。df 含 start_date 之前的 warmup bar，
        供 MA250 / GMM250 等长周期策略计算指标；effective_start_date 供执行层
        以 trade_start 切片，避免 warmup 污染净值/交易。
        """
        min_bars = self._history_min_bars(strategy_id)
        if min_bars <= 0:
            df, _, _ = stock_service.get_daily_data(symbol, start_date, end_date)
            return df, start_date

        warmup_bars = min_bars + WARMUP_BUFFER_BARS
        cal_days = int(warmup_bars * _CALENDAR_FACTOR)
        eff_start = (
            datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=cal_days)
        ).strftime("%Y-%m-%d")

        run_span_days = max(
            0,
            (
                datetime.strptime(end_date, "%Y-%m-%d")
                - datetime.strptime(start_date, "%Y-%m-%d")
            ).days,
        )
        run_span_bars = int(run_span_days / 1.4)
        count = max(500, warmup_bars + run_span_bars + 50)

        df, _, _ = stock_service.get_daily_data(
            symbol, eff_start, end_date, count=count
        )
        return df, eff_start

    def run_backtest(
        self,
        request: BacktestRunRequest,
        current_user: User,
        db: Session,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if request.start_date > request.end_date:
            raise ValueError("start_date 不能晚于 end_date")

        if request.mode == "strategy_select":
            result = self._run_strategy_select_mode(request, progress_callback, db)
        else:
            result = self._run_manual_symbols_mode(request, progress_callback, db)

        enriched_trades = self._enrich_trades_with_stock_name(db, result["trades"])
        enriched_snapshots = self._enrich_snapshots_with_stock_name(
            db, result.get("positions_snapshot", [])
        )
        rounds = self._build_round_trips(enriched_trades)
        enriched_rounds = self._enrich_rounds_with_stock_name(db, rounds)

        summary_payload = dict(result["summary"] or {})
        summary_payload["positions_snapshot"] = enriched_snapshots
        summary_payload["mode"] = request.mode
        if result.get("diagnostics") is not None:
            summary_payload["diagnostics"] = result["diagnostics"]

        run = BacktestRun(
            user_id=current_user.id,
            name=request.name,
            status="completed",
            strategy_id=request.strategy_id,
            strategy_params=request.strategy_params,
            symbols=result.get("symbols", []),
            start_date=request.start_date,
            end_date=request.end_date,
            initial_cash=request.initial_cash,
            benchmark=request.benchmark,
            cost_config=request.cost_config.model_dump(),
            summary=summary_payload,
            equity_curve=result["equity_curve"],
            trades=enriched_trades,
            warnings=result["warnings"],
        )

        db.add(run)
        db.commit()
        db.refresh(run)

        self._persist_rounds(db=db, run_id=run.id, rounds=enriched_rounds)

        response_summary = dict(result["summary"] or {})
        response_summary["positions_snapshot"] = enriched_snapshots
        return {
            "run_id": run.id,
            "summary": response_summary,
            "equity_curve": result["equity_curve"],
            "trades": enriched_trades,
            "positions_snapshot": enriched_snapshots,
            "warnings": result["warnings"],
            "diagnostics": result.get("diagnostics"),
        }

    def _run_manual_symbols_mode(
        self,
        request: BacktestRunRequest,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        db: Session | None = None,
    ) -> dict[str, Any]:
        symbols = [s.strip() for s in request.symbols if s.strip()]
        if not symbols:
            raise ValueError("manual_symbols 模式下 symbols 不能为空")
        strategy = get_strategy(request.strategy_id, require_usable=True)
        return self._run_unified_portfolio(
            request=request,
            symbols=symbols,
            strategy=strategy,
            progress_callback=progress_callback,
            db=db,
            include_diagnostics=False,
        )

    def _run_strategy_select_mode(
        self,
        request: BacktestRunRequest,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        db: Session | None = None,
    ) -> dict[str, Any]:
        strategy = get_strategy(request.strategy_id, require_usable=True)
        params = request.strategy_params or {}
        raw_boards = params.get("boards", ["main"])
        if isinstance(raw_boards, str):
            raw_boards = [x.strip() for x in raw_boards.split(",") if x.strip()]
        if not isinstance(raw_boards, list):
            raise ValueError("boards 参数格式错误，应为字符串数组")
        boards = [str(x).strip().lower() for x in raw_boards if str(x).strip()]
        allowed_boards = {"main", "gem", "star", "bse"}
        invalid = [b for b in boards if b not in allowed_boards]
        if invalid:
            raise ValueError(f"boards 包含非法值: {', '.join(invalid)}")
        if not boards:
            raise ValueError("boards 至少选择一个板块")

        raw_exclude_st = params.get("exclude_st", True)
        if isinstance(raw_exclude_st, str):
            exclude_st = raw_exclude_st.strip().lower() not in {
                "false",
                "0",
                "no",
                "off",
            }
        else:
            exclude_st = bool(raw_exclude_st)

        if request.universe_type == "custom":
            universe = [s.strip() for s in request.pool_symbols if s.strip()]
        else:
            universe = stock_service.get_all_stock_symbols(
                boards=boards,
                exclude_st=exclude_st,
            )

        if not universe:
            raise ValueError(
                "选股池为空，无法执行 strategy_select 回测（可能是行情源连接失败）。请先改用 custom 股票池，或稍后重试。"
            )
        return self._run_unified_portfolio(
            request=request,
            symbols=universe,
            strategy=strategy,
            progress_callback=progress_callback,
            db=db,
            include_diagnostics=True,
            universe_filter={"boards": boards, "exclude_st": exclude_st},
        )

    def _load_security_rules(
        self, db: Session | None, symbols: list[str]
    ) -> dict[str, SecurityRule]:
        if db is None or not symbols:
            return {}
        normalized = [self._normalize_symbol_code(symbol) for symbol in symbols]
        normalized = [symbol for symbol in normalized if symbol]
        if not normalized:
            return {}
        try:
            rows = (
                db.query(
                    SecurityUniverse.stock_code,
                    SecurityUniverse.board,
                    SecurityUniverse.is_st,
                )
                .filter(SecurityUniverse.stock_code.in_(list(set(normalized))))
                .all()
            )
        except Exception:
            return {}
        return {
            str(code): SecurityRule(board=str(board or "main"), is_st=bool(is_st))
            for code, board, is_st in rows
        }

    def _run_unified_portfolio(
        self,
        request: BacktestRunRequest,
        symbols: list[str],
        strategy: Any,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        db: Session | None,
        include_diagnostics: bool,
        universe_filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """两种模式共享的候选、Policy 与组合逐日撮合链。"""

        params = request.strategy_params or {}
        required_columns = set(strategy.required_columns())
        warnings: list[str] = []
        market_data_map: dict[str, pd.DataFrame] = {}
        candidate_frames: list[pd.DataFrame] = []
        empty_symbols_preview: list[str] = []
        data_empty_count = 0
        total = len(symbols)

        for idx, symbol in enumerate(symbols, start=1):
            if progress_callback:
                progress_callback(
                    {
                        "stage": "scanning",
                        "total_symbols": total,
                        "processed_symbols": idx - 1,
                        "progress_pct": round((idx - 1) * 100 / total, 2),
                    }
                )
            try:
                df, _ = self._fetch_daily_with_warmup(
                    symbol=symbol,
                    start_date=request.start_date.strftime("%Y-%m-%d"),
                    end_date=request.end_date.strftime("%Y-%m-%d"),
                    strategy_id=request.strategy_id,
                )
            except Exception as exc:
                warnings.append(f"{symbol}: 获取行情失败({exc})，已跳过")
                data_empty_count += 1
                continue
            if df is None or df.empty:
                data_empty_count += 1
                if len(empty_symbols_preview) < 30:
                    empty_symbols_preview.append(symbol)
                continue
            missing_cols = sorted(required_columns - set(df.columns))
            if missing_cols:
                warnings.append(f"{symbol}: 缺少必需列 {missing_cols}，已跳过")
                continue

            work = df.copy().reset_index(drop=True)
            work["symbol"] = symbol
            market_data_map[symbol] = work
            candidates = strategy.generate_candidates(work, params)
            if candidates is None or candidates.empty:
                continue
            required_candidate_cols = {"trade_date", "symbol"}
            missing_candidate_cols = required_candidate_cols - set(candidates.columns)
            if missing_candidate_cols:
                warnings.append(
                    f"{symbol}: candidates 缺少列 {sorted(missing_candidate_cols)}"
                )
                continue
            cdf = candidates.copy()
            cdf["trade_date"] = pd.to_datetime(cdf["trade_date"]).dt.normalize()
            if "signal_strength" not in cdf.columns:
                cdf["signal_strength"] = 0.0
            if "reason" not in cdf.columns:
                cdf["reason"] = "strategy_candidate"
            candidate_frames.append(cdf)

        if data_empty_count:
            preview = ",".join(empty_symbols_preview)
            suffix = "" if data_empty_count <= len(empty_symbols_preview) else "..."
            warnings.append(
                f"无可用日线数据股票数: {data_empty_count}/{len(symbols)}"
                + (f"，示例: {preview}{suffix}" if preview else "")
            )

        columns = ["trade_date", "symbol", "signal_strength", "reason"]
        candidates_df = (
            pd.concat(candidate_frames, ignore_index=True)
            if candidate_frames
            else pd.DataFrame(columns=columns)
        )
        policy_profile_id = str(
            params.get("policy_profile") or strategy.default_policy_profile()
        )
        pipeline = resolve_profile(policy_profile_id)
        context = PolicyContext(
            strategy_id=request.strategy_id,
            params=params,
            extras={"market_data_map": market_data_map},
        )
        ranked_df = pipeline["ranking"].rank(candidates_df, context)
        selected_df = pipeline["selection"].select(
            ranked_df, portfolio_state={}, context=context
        )
        allocated_df = pipeline["allocation"].allocate(
            selected_df,
            equity=float(request.initial_cash),
            risk_state={},
            context=context,
        )
        orders_df = pipeline["risk"].check_pre_trade(
            allocated_df, portfolio_state={}, context=context
        )

        raw_rules = self._load_security_rules(db, list(market_data_map))
        security_rules = {
            symbol: raw_rules.get(self._normalize_symbol_code(symbol))
            for symbol in market_data_map
            if raw_rules.get(self._normalize_symbol_code(symbol)) is not None
        }
        portfolio = run_portfolio(
            market_data_map=market_data_map,
            orders_df=orders_df,
            initial_cash=float(request.initial_cash),
            cost_model=CostModel(**request.cost_config.model_dump()),
            hold_days=int(params.get("hold_days", 5) or 5),
            max_total_position_pct=float(params.get("max_total_position_pct", 1.0)),
            default_position_size_pct=float(params.get("position_size_pct", 0.1)),
            security_rules=security_rules,
            trade_start=request.start_date.strftime("%Y-%m-%d"),
        )
        warnings.extend(portfolio.warnings)
        if not candidate_frames:
            warnings.append("未命中任何交易信号")

        if progress_callback:
            progress_callback(
                {
                    "stage": "summarizing",
                    "total_symbols": total,
                    "processed_symbols": total,
                    "progress_pct": 100.0,
                }
            )

        summary = calc_summary(
            portfolio.equity_curve, portfolio.trades, request.initial_cash
        )
        summary["final_positions"] = portfolio.final_positions
        result: dict[str, Any] = {
            "summary": summary,
            "equity_curve": portfolio.equity_curve,
            "trades": portfolio.trades,
            "warnings": warnings,
            "positions_snapshot": portfolio.positions_snapshot,
            "symbols": symbols,
        }
        if include_diagnostics:
            diagnostics = {
                "universe_size": len(symbols),
                "data_available_count": len(market_data_map),
                "data_empty_count": data_empty_count,
                "candidate_count": int(len(candidates_df)),
                "ranked_count": int(len(ranked_df)),
                "selected_count": int(len(selected_df)),
                "ordered_count": int(len(orders_df)),
                "event_count": int(len(portfolio.trades)),
                "policy_profile": policy_profile_id,
                "effective_universe_filter": universe_filter or {},
            }
            result["diagnostics"] = diagnostics
            warnings.append(
                f"strategy_select 扫描股票数: {len(symbols)}，有效行情股票数: "
                f"{len(market_data_map)}，候选数: {len(candidates_df)}，成交数: "
                f"{len(portfolio.trades)}"
            )
        return result

    def list_runs(
        self, current_user: User, db: Session, limit: int = 20, offset: int = 0
    ):
        query = (
            db.query(BacktestRun)
            .options(
                load_only(
                    BacktestRun.id,
                    BacktestRun.name,
                    BacktestRun.status,
                    BacktestRun.strategy_id,
                    BacktestRun.symbols,
                    BacktestRun.start_date,
                    BacktestRun.end_date,
                    BacktestRun.initial_cash,
                    BacktestRun.summary,
                    BacktestRun.created_at,
                )
            )
            .filter(BacktestRun.user_id == current_user.id)
        )
        total = query.count()
        runs = (
            query.order_by(BacktestRun.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return runs, total

    def get_run(self, run_id: int, current_user: User, db: Session):
        run = (
            db.query(BacktestRun)
            .filter(BacktestRun.id == run_id, BacktestRun.user_id == current_user.id)
            .first()
        )
        return run

    def get_run_overview(self, run_id: int, current_user: User, db: Session):
        run = self.get_run(run_id=run_id, current_user=current_user, db=db)
        if not run:
            return None

        summary = run.summary or {}
        return {
            "run_id": run.id,
            "name": run.name,
            "status": run.status,
            "strategy_id": run.strategy_id,
            "start_date": run.start_date,
            "end_date": run.end_date,
            "initial_cash": run.initial_cash,
            "benchmark": run.benchmark,
            "summary": summary,
            "diagnostics": (
                summary.get("diagnostics") if isinstance(summary, dict) else None
            ),
            "equity_curve": run.equity_curve or [],
            "warnings": run.warnings or [],
            "created_at": run.created_at,
        }

    def get_run_trades(
        self,
        run_id: int,
        current_user: User,
        db: Session,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        row = (
            db.query(BacktestRun)
            .options(load_only(BacktestRun.id, BacktestRun.user_id, BacktestRun.trades))
            .filter(BacktestRun.id == run_id, BacktestRun.user_id == current_user.id)
            .first()
        )
        if not row:
            return [], 0

        trades = row.trades or []
        enriched = self._enrich_trades_with_stock_name(db, trades)

        if enriched != trades:
            row.trades = enriched
            db.commit()

        total = len(enriched)
        if limit is None:
            return enriched, total
        return enriched[offset : offset + limit], total

    def get_run_rounds(
        self,
        run_id: int,
        current_user: User,
        db: Session,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        base_query = (
            db.query(BacktestRound)
            .join(BacktestRun, BacktestRound.run_id == BacktestRun.id)
            .filter(
                BacktestRound.run_id == run_id, BacktestRun.user_id == current_user.id
            )
        )
        total = base_query.count()

        if total > 0:
            query = base_query.order_by(BacktestRound.id.asc())
            if limit is not None:
                query = query.offset(offset).limit(limit)
            rows = query.all()
            symbol_map = self._get_symbol_name_map(db, [r.symbol for r in rows])
            needs_backfill = False
            data: list[dict[str, Any]] = []
            for r in rows:
                code = self._normalize_symbol_code(r.symbol)
                stock_name = r.stock_name or symbol_map.get(code)
                if stock_name and r.stock_name != stock_name:
                    r.stock_name = stock_name
                    needs_backfill = True
                data.append(
                    {
                        "symbol": r.symbol,
                        "stock_name": stock_name,
                        "open_time": r.open_time,
                        "open_price": r.open_price,
                        "close_time": r.close_time,
                        "close_price": r.close_price,
                        "qty": r.qty,
                        "holding_days": r.holding_days,
                        "pnl_amount": r.pnl_amount,
                        "pnl_ratio": r.pnl_ratio,
                        "exit_reason": r.exit_reason,
                        "max_favorable_excursion": r.max_favorable_excursion,
                        "max_adverse_excursion": r.max_adverse_excursion,
                    }
                )
            if needs_backfill:
                db.commit()
            return data, total

        trades, _ = self.get_run_trades(run_id=run_id, current_user=current_user, db=db)
        rounds = self._build_round_trips(trades)
        if rounds:
            enriched_rounds = self._enrich_rounds_with_stock_name(db, rounds)
            self._persist_rounds(db=db, run_id=run_id, rounds=enriched_rounds)
            total = len(enriched_rounds)
            if limit is None:
                return enriched_rounds, total
            return enriched_rounds[offset : offset + limit], total
        return [], 0

    def get_run_snapshots(
        self,
        run_id: int,
        current_user: User,
        db: Session,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        row = (
            db.query(BacktestRun)
            .options(
                load_only(BacktestRun.id, BacktestRun.user_id, BacktestRun.summary)
            )
            .filter(BacktestRun.id == run_id, BacktestRun.user_id == current_user.id)
            .first()
        )
        if not row:
            return [], 0
        summary = row.summary or {}
        positions = summary.get("positions_snapshot") or []
        snapshots = positions if isinstance(positions, list) else []

        enriched_snapshots = self._enrich_snapshots_with_stock_name(db, snapshots)
        if enriched_snapshots != snapshots:
            updated_summary = dict(summary)
            updated_summary["positions_snapshot"] = enriched_snapshots
            row.summary = updated_summary
            db.commit()

        total = len(enriched_snapshots)
        if limit is None:
            return enriched_snapshots, total
        return enriched_snapshots[offset : offset + limit], total

    def _normalize_curve_to_base_one(
        self, values_by_date: dict[str, float], base_dates: list[str]
    ) -> list[dict[str, Any]]:
        base_value: float | None = None
        result: list[dict[str, Any]] = []
        for d in base_dates:
            raw = values_by_date.get(d)
            if raw is None:
                continue
            if base_value is None and raw != 0:
                base_value = raw
            norm = None
            if base_value is not None and base_value != 0:
                norm = round(raw / base_value, 6)
            result.append({"trade_date": d, "value_raw": raw, "value_norm": norm})
        return result

    def _load_benchmark_series(
        self, benchmark_code: str, start_date: str, end_date: str
    ) -> tuple[dict[str, float], str, str | None]:
        code = (benchmark_code or "").strip()
        if not code:
            return {}, "price", None

        tr_candidates = [f"{code}_TR", f"{code}.TR"]
        for tr_code in tr_candidates:
            try:
                tr_df, _, _ = stock_service.get_daily_data(
                    symbol=tr_code, start_date=start_date, end_date=end_date
                )
                if tr_df is not None and not tr_df.empty:
                    tr_values: dict[str, float] = {}
                    for _, row in tr_df.iterrows():
                        d = self._extract_trade_date(row.get("datetime"))
                        if not d:
                            continue
                        tr_values[d] = float(row.get("close", 0) or 0)
                    if tr_values:
                        return tr_values, "tr", None
            except Exception:
                continue

        try:
            px_df, _, _ = stock_service.get_daily_data(
                symbol=code, start_date=start_date, end_date=end_date
            )
            px_values: dict[str, float] = {}
            if px_df is not None and not px_df.empty:
                for _, row in px_df.iterrows():
                    d = self._extract_trade_date(row.get("datetime"))
                    if not d:
                        continue
                    px_values[d] = float(row.get("close", 0) or 0)
            if px_values:
                return (
                    px_values,
                    "price",
                    "TR不可用，回退价格指数（未含分红）",
                )
        except Exception:
            pass

        return {}, "price", "指数数据不可用"

    def get_run_facts(
        self,
        run_id: int,
        current_user: User,
        db: Session,
        benchmark_code: str | None = None,
        compare_run_id: int | None = None,
    ) -> dict[str, Any]:
        run = self.get_run(run_id=run_id, current_user=current_user, db=db)
        if not run:
            return {
                "run_id": run_id,
                "summary": {},
                "equity_curve_daily": [],
                "positions_daily_eod": [],
                "instrument_meta": [],
                "benchmark_curve_daily": [],
                "benchmark_meta": None,
                "compare_run_curve_daily": [],
                "compare_run_meta": None,
                "data_quality": {
                    "missing_equity_dates": [],
                    "missing_snapshot_dates": [],
                },
            }

        summary = run.summary or {}
        snapshots_raw = summary.get("positions_snapshot") or []
        snapshots = snapshots_raw if isinstance(snapshots_raw, list) else []
        snapshots = self._enrich_snapshots_with_stock_name(db, snapshots)

        trades_raw = run.trades or []
        trades = self._enrich_trades_with_stock_name(db, trades_raw)

        equity_curve_raw = run.equity_curve or []
        equity_by_date: dict[str, float] = {}
        for point in equity_curve_raw:
            if not isinstance(point, dict):
                continue
            d = self._extract_trade_date(point.get("datetime"))
            if not d:
                continue
            eq = point.get("equity")
            try:
                equity_by_date[d] = round(float(eq), 4)
            except (TypeError, ValueError):
                continue

        equity_dates = sorted(equity_by_date.keys())
        equity_curve_daily: list[dict[str, Any]] = []
        prev_equity: float | None = None
        for d in equity_dates:
            curr_equity = equity_by_date[d]
            daily_return = None
            if prev_equity is not None and prev_equity != 0:
                daily_return = round((curr_equity - prev_equity) / prev_equity, 6)
            equity_curve_daily.append(
                {
                    "trade_date": d,
                    "equity": curr_equity,
                    "daily_return": daily_return,
                }
            )
            prev_equity = curr_equity

        snapshot_by_date: dict[str, dict[str, Any]] = {}
        all_symbols: set[str] = set()
        for snap in snapshots:
            if not isinstance(snap, dict):
                continue
            d = self._extract_trade_date(snap.get("snapshot_time"))
            if not d:
                continue
            snapshot_by_date[d] = snap
            for h in snap.get("holdings") or []:
                if isinstance(h, dict) and h.get("symbol"):
                    all_symbols.add(str(h.get("symbol")))

        sells_by_date: dict[str, set[str]] = defaultdict(set)
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            if str(trade.get("side") or "").lower() != "sell":
                continue
            d = self._extract_trade_date(trade.get("datetime"))
            symbol = str(trade.get("symbol") or "").strip()
            if not d or not symbol:
                continue
            sells_by_date[d].add(symbol)
            all_symbols.add(symbol)

        symbol_name_map = self._get_symbol_name_map(db, list(all_symbols))
        positions_daily_eod: list[dict[str, Any]] = []
        snapshot_dates = sorted(snapshot_by_date.keys())
        for d in snapshot_dates:
            snap = snapshot_by_date[d]
            holdings = snap.get("holdings") or []
            existing_symbols: set[str] = set()
            for h in holdings:
                if not isinstance(h, dict):
                    continue
                symbol = str(h.get("symbol") or "").strip()
                if not symbol:
                    continue
                existing_symbols.add(symbol)
                code = self._normalize_symbol_code(symbol)
                stock_name = h.get("stock_name") or symbol_name_map.get(code)
                qty = int(h.get("qty", 0) or 0)
                last_price = float(h.get("last_price", 0) or 0)
                market_value = float(h.get("market_value", 0) or 0)
                weight = float(h.get("weight", 0) or 0)
                positions_daily_eod.append(
                    {
                        "trade_date": d,
                        "symbol": symbol,
                        "stock_name": stock_name,
                        "qty": qty,
                        "last_price": round(last_price, 4),
                        "market_value": round(market_value, 4),
                        "weight": round(weight, 6),
                        "position_status": "holding",
                    }
                )

            for symbol in sorted(sells_by_date.get(d, set())):
                if symbol in existing_symbols:
                    continue
                code = self._normalize_symbol_code(symbol)
                stock_name = symbol_name_map.get(code)
                positions_daily_eod.append(
                    {
                        "trade_date": d,
                        "symbol": symbol,
                        "stock_name": stock_name,
                        "qty": 0,
                        "last_price": 0.0,
                        "market_value": 0.0,
                        "weight": 0.0,
                        "position_status": "closed_today",
                    }
                )

        equity_date_set = set(equity_dates)
        snapshot_date_set = set(snapshot_dates)

        benchmark_curve_daily: list[dict[str, Any]] = []
        benchmark_meta: dict[str, Any] | None = None
        if benchmark_code and run.start_date and run.end_date:
            benchmark_values, source_type, source_note = self._load_benchmark_series(
                benchmark_code=benchmark_code,
                start_date=run.start_date.strftime("%Y-%m-%d"),
                end_date=run.end_date.strftime("%Y-%m-%d"),
            )
            benchmark_curve_daily = self._normalize_curve_to_base_one(
                benchmark_values, equity_dates
            )
            benchmark_meta = {
                "benchmark_code": benchmark_code,
                "source_type": source_type,
                "source_note": source_note,
            }

        compare_run_curve_daily: list[dict[str, Any]] = []
        compare_run_meta: dict[str, Any] | None = None
        if compare_run_id and compare_run_id != run.id:
            compare_run = self.get_run(
                run_id=compare_run_id,
                current_user=current_user,
                db=db,
            )
            if compare_run:
                compare_values: dict[str, float] = {}
                for point in compare_run.equity_curve or []:
                    if not isinstance(point, dict):
                        continue
                    d = self._extract_trade_date(point.get("datetime"))
                    if not d:
                        continue
                    try:
                        compare_values[d] = float(point.get("equity", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                compare_run_curve_daily = self._normalize_curve_to_base_one(
                    compare_values, equity_dates
                )
                compare_run_meta = {
                    "run_id": compare_run.id,
                    "run_name": compare_run.name,
                }

        return {
            "run_id": run.id,
            "summary": summary,
            "equity_curve_daily": equity_curve_daily,
            "positions_daily_eod": positions_daily_eod,
            "instrument_meta": [
                {
                    "symbol": s,
                    "stock_name": symbol_name_map.get(self._normalize_symbol_code(s)),
                }
                for s in sorted(all_symbols)
            ],
            "benchmark_curve_daily": benchmark_curve_daily,
            "benchmark_meta": benchmark_meta,
            "compare_run_curve_daily": compare_run_curve_daily,
            "compare_run_meta": compare_run_meta,
            "data_quality": {
                "missing_equity_dates": sorted(snapshot_date_set - equity_date_set),
                "missing_snapshot_dates": sorted(equity_date_set - snapshot_date_set),
            },
        }

    def get_run_strategy_config(
        self, run_id: int, current_user: User, db: Session
    ) -> dict[str, Any] | None:
        run = self.get_run(run_id=run_id, current_user=current_user, db=db)
        if not run:
            return None

        strategy_params = run.strategy_params or {}
        boards = strategy_params.get("boards", ["main"])
        if isinstance(boards, str):
            boards = [x.strip() for x in boards.split(",") if x.strip()]
        boards = [str(x).lower() for x in boards if str(x).strip()]
        exclude_st = bool(strategy_params.get("exclude_st", True))

        symbol_list = run.symbols or []
        symbol_count = len(symbol_list)

        sql_preview = (
            "SELECT stock_code FROM security_universe "
            "WHERE is_active = true "
            f"AND board IN ({', '.join([repr(b) for b in boards or ['main']])}) "
            f"AND is_st = {'false' if exclude_st else 'ANY'} "
            "ORDER BY stock_code;"
        )

        return {
            "run_id": run.id,
            "strategy_id": run.strategy_id,
            "strategy_params": strategy_params,
            "cost_config": run.cost_config or {},
            "benchmark": run.benchmark,
            "symbols": {
                "count": symbol_count,
                "preview": symbol_list[:50],
                "truncated": symbol_count > 50,
            },
            "date_range": {
                "start_date": run.start_date,
                "end_date": run.end_date,
            },
            "meta": {
                "name": run.name,
                "status": run.status,
                "created_at": run.created_at,
            },
            "filter_summary": {
                "boards": boards or ["main"],
                "exclude_st": exclude_st,
                "active_only": True,
                "candidate_count": symbol_count,
            },
            "filter_dsl": {
                "table": "security_universe",
                "where": {
                    "is_active": True,
                    "boards": boards or ["main"],
                    "exclude_st": exclude_st,
                },
            },
            "sql_preview": sql_preview,
        }

    def _parse_trade_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value), fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def _persist_rounds(self, db: Session, run_id: int, rounds: list[dict[str, Any]]):
        db.query(BacktestRound).filter(BacktestRound.run_id == run_id).delete()
        if not rounds:
            db.commit()
            return

        db_rows = [
            BacktestRound(
                run_id=run_id,
                symbol=str(r.get("symbol", "")),
                stock_name=r.get("stock_name"),
                open_time=r.get("open_time"),
                open_price=float(r.get("open_price", 0) or 0),
                close_time=r.get("close_time"),
                close_price=float(r.get("close_price", 0) or 0),
                qty=float(r.get("qty", 0) or 0),
                holding_days=r.get("holding_days"),
                pnl_amount=float(r.get("pnl_amount", 0) or 0),
                pnl_ratio=float(r.get("pnl_ratio", 0) or 0),
                exit_reason=r.get("exit_reason"),
                max_favorable_excursion=r.get("max_favorable_excursion"),
                max_adverse_excursion=r.get("max_adverse_excursion"),
            )
            for r in rounds
        ]
        db.bulk_save_objects(db_rows)
        db.commit()

    def _build_round_trips(self, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not trades:
            return []

        sorted_trades = sorted(trades, key=lambda x: str(x.get("datetime", "")))
        open_lots: dict[str, deque] = defaultdict(deque)
        rounds: list[dict[str, Any]] = []

        for trade in sorted_trades:
            symbol = str(trade.get("symbol", "")).strip()
            side = str(trade.get("side", "")).lower()
            qty = float(trade.get("qty", 1) or 1)
            price = float(trade.get("price", 0) or 0)
            amount = float(trade.get("amount", price * qty) or (price * qty))
            trade_dt = self._parse_trade_datetime(trade.get("datetime"))

            if not symbol or qty <= 0:
                continue

            if side == "buy":
                open_lots[symbol].append(
                    {
                        "datetime": trade.get("datetime"),
                        "trade_dt": trade_dt,
                        "qty": qty,
                        "price": price,
                        "amount": amount,
                        "reason": trade.get("reason"),
                    }
                )
                continue

            if side != "sell":
                continue

            remaining = qty
            while remaining > 0 and open_lots[symbol]:
                lot = open_lots[symbol][0]
                matched_qty = min(remaining, lot["qty"])
                open_amount = lot["price"] * matched_qty
                close_amount = price * matched_qty
                pnl_amount = close_amount - open_amount
                pnl_ratio = (pnl_amount / open_amount) if open_amount > 0 else 0.0

                holding_days = None
                if lot.get("trade_dt") and trade_dt:
                    holding_days = max(
                        (trade_dt.date() - lot["trade_dt"].date()).days, 0
                    )

                rounds.append(
                    {
                        "symbol": symbol,
                        "open_time": lot.get("datetime"),
                        "open_price": round(lot["price"], 4),
                        "close_time": trade.get("datetime"),
                        "close_price": round(price, 4),
                        "qty": round(matched_qty, 4),
                        "holding_days": holding_days,
                        "pnl_amount": round(pnl_amount, 4),
                        "pnl_ratio": round(pnl_ratio, 6),
                        "exit_reason": trade.get("reason") or lot.get("reason"),
                        "max_favorable_excursion": None,
                        "max_adverse_excursion": None,
                    }
                )

                lot["qty"] -= matched_qty
                remaining -= matched_qty
                if lot["qty"] <= 0:
                    open_lots[symbol].popleft()

        return rounds

    def _merge_position_snapshots(
        self, symbol_position_curves: dict[str, list[dict]]
    ) -> list[dict[str, Any]]:
        if not symbol_position_curves:
            return []

        time_set = set()
        for curve in symbol_position_curves.values():
            for p in curve:
                time_set.add(p["datetime"])

        times = sorted(time_set)
        per_symbol_dict = {
            symbol: {p["datetime"]: p for p in curve}
            for symbol, curve in symbol_position_curves.items()
        }
        latest = {symbol: None for symbol in per_symbol_dict.keys()}

        snapshots: list[dict[str, Any]] = []
        for ts in times:
            holdings = []
            total_mv = 0.0
            total_cash = 0.0
            total_equity = 0.0

            for symbol, mapping in per_symbol_dict.items():
                if ts in mapping:
                    latest[symbol] = mapping[ts]
                row = latest[symbol]
                if not row:
                    continue
                shares = int(row.get("shares", 0) or 0)
                close = float(row.get("close", 0) or 0)
                mv = float(row.get("market_value", shares * close) or (shares * close))
                cash = float(row.get("cash", 0) or 0)
                equity = float(row.get("equity", cash + mv) or (cash + mv))

                total_mv += mv
                total_cash += cash
                total_equity += equity

                if shares > 0:
                    holdings.append(
                        {
                            "symbol": symbol,
                            "qty": shares,
                            "last_price": round(close, 4),
                            "market_value": round(mv, 4),
                        }
                    )

            denominator = total_mv if total_mv > 0 else 1.0
            for h in holdings:
                h["weight"] = round(h["market_value"] / denominator, 6)

            snapshots.append(
                {
                    "snapshot_time": ts,
                    "equity": round(total_equity, 4),
                    "cash": round(total_cash, 4),
                    "position_value": round(total_mv, 4),
                    "holdings": holdings,
                }
            )

        return snapshots

    def _merge_symbol_curves(self, symbol_curves: dict[str, list[dict]]) -> list[dict]:
        if not symbol_curves:
            return []

        time_set = set()
        for curve in symbol_curves.values():
            for p in curve:
                time_set.add(p["datetime"])

        times = sorted(time_set)
        per_symbol_dict = {
            symbol: {p["datetime"]: p["equity"] for p in curve}
            for symbol, curve in symbol_curves.items()
        }

        latest_equity = defaultdict(float)
        merged = []
        for ts in times:
            total = 0.0
            for symbol, mapping in per_symbol_dict.items():
                if ts in mapping:
                    latest_equity[symbol] = mapping[ts]
                total += latest_equity[symbol]
            merged.append({"datetime": ts, "equity": round(total, 4)})

        return merged


backtest_service = BacktestService()
