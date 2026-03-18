"""回测服务"""

from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from app.models.backtest import BacktestRun, BacktestRound
from app.models.security_universe import SecurityUniverse
from app.models.user import User
from app.schemas.backtest import BacktestRunRequest
from app.services.backtest.costs import CostModel
from app.services.backtest.engine import run_for_symbol
from app.services.backtest.metrics import calc_summary
from app.services.backtest.registry import list_strategies
from app.services.stock_service import stock_service


class BacktestService:
    def list_strategies(self) -> list[dict]:
        return list_strategies()

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
            result = self._run_strategy_select_mode(request, progress_callback)
        else:
            result = self._run_manual_symbols_mode(request, progress_callback)

        summary_payload = dict(result["summary"] or {})
        summary_payload["positions_snapshot"] = result.get("positions_snapshot", [])

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
            trades=result["trades"],
            warnings=result["warnings"],
        )

        db.add(run)
        db.commit()
        db.refresh(run)

        rounds = self._build_round_trips(result["trades"])
        self._persist_rounds(db=db, run_id=run.id, rounds=rounds)

        return {
            "run_id": run.id,
            "summary": result["summary"],
            "equity_curve": result["equity_curve"],
            "trades": result["trades"],
            "positions_snapshot": result.get("positions_snapshot", []),
            "warnings": result["warnings"],
            "diagnostics": result.get("diagnostics"),
        }

    def _run_manual_symbols_mode(
        self,
        request: BacktestRunRequest,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        symbols = [s.strip() for s in request.symbols if s.strip()]
        if not symbols:
            raise ValueError("manual_symbols 模式下 symbols 不能为空")

        capital_per_symbol = request.initial_cash / len(symbols)
        cost_model = CostModel(**request.cost_config.model_dump())

        all_trades: list[dict] = []
        all_warnings: list[str] = []
        symbol_curves: dict[str, list[dict]] = {}
        symbol_position_curves: dict[str, list[dict]] = {}
        final_positions: list[dict] = []

        total = len(symbols)
        for idx, symbol in enumerate(symbols, start=1):
            if progress_callback:
                progress_callback(
                    {
                        "stage": "running",
                        "total_symbols": total,
                        "processed_symbols": idx - 1,
                        "progress_pct": round((idx - 1) * 100 / total, 2),
                    }
                )
            try:
                df, _, _ = stock_service.get_daily_data(
                    symbol=symbol,
                    start_date=request.start_date.strftime("%Y-%m-%d"),
                    end_date=request.end_date.strftime("%Y-%m-%d"),
                )
            except Exception as e:
                all_warnings.append(f"{symbol}: 获取行情失败({str(e)})，已跳过")
                continue

            if df.empty:
                all_warnings.append(f"{symbol}: 无可用行情，已跳过")
                continue

            symbol_result = run_for_symbol(
                symbol=symbol,
                df=df,
                strategy_id=request.strategy_id,
                strategy_params=request.strategy_params,
                init_cash=capital_per_symbol,
                cost_model=cost_model,
            )

            symbol_curves[symbol] = symbol_result.equity_curve
            symbol_position_curves[symbol] = symbol_result.position_curve
            all_trades.extend(symbol_result.trades)
            all_warnings.extend(symbol_result.warnings)
            final_positions.append(
                {
                    "symbol": symbol,
                    "shares": symbol_result.final_position,
                    "equity": round(symbol_result.last_equity, 4),
                }
            )

        if progress_callback:
            progress_callback(
                {
                    "stage": "summarizing",
                    "total_symbols": total,
                    "processed_symbols": total,
                    "progress_pct": 100.0,
                }
            )

        portfolio_curve = self._merge_symbol_curves(symbol_curves)
        position_snapshots = self._merge_position_snapshots(symbol_position_curves)
        summary = calc_summary(portfolio_curve, all_trades, request.initial_cash)
        summary["final_positions"] = final_positions

        return {
            "summary": summary,
            "equity_curve": portfolio_curve,
            "trades": all_trades,
            "warnings": all_warnings,
            "positions_snapshot": position_snapshots,
            "symbols": symbols,
        }

    def _run_strategy_select_mode(
        self,
        request: BacktestRunRequest,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if request.strategy_id != "volume_shrink_drop_v1":
            raise ValueError(
                "strategy_select 模式当前仅支持 volume_shrink_drop_v1 策略"
            )

        params = request.strategy_params or {}
        min_price_drop_pct = float(params.get("min_price_drop_pct", -1.0))
        min_volume_shrink_pct = float(params.get("min_volume_shrink_pct", 10.0))
        consecutive_days = int(params.get("consecutive_days", 3))
        hold_days = int(params.get("hold_days", 5))
        position_size_pct = float(params.get("position_size_pct", 0.1))

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

        warnings: list[str] = []
        events: list[dict[str, Any]] = []
        data_available_count = 0
        data_empty_count = 0
        empty_symbols_preview: list[str] = []

        total = len(universe)
        for idx, symbol in enumerate(universe, start=1):
            if progress_callback:
                progress_callback(
                    {
                        "stage": "scanning",
                        "total_symbols": total,
                        "processed_symbols": idx - 1,
                        "progress_pct": round((idx - 1) * 100 / total, 2),
                    }
                )
            df, _, _ = stock_service.get_daily_data(
                symbol=symbol,
                start_date=request.start_date.strftime("%Y-%m-%d"),
                end_date=request.end_date.strftime("%Y-%m-%d"),
            )
            if df.empty or len(df) < (consecutive_days + hold_days + 2):
                data_empty_count += 1
                if len(empty_symbols_preview) < 30:
                    empty_symbols_preview.append(symbol)
                continue

            data_available_count += 1

            work = df.copy().reset_index(drop=True)
            work["close_prev"] = work["close"].shift(1)
            work["vol_prev"] = work["volume"].shift(1)
            work["price_drop_pct"] = (work["close"] / work["close_prev"] - 1.0) * 100
            work["volume_shrink_pct"] = (
                (work["vol_prev"] - work["volume"]) / work["vol_prev"]
            ) * 100
            work["daily_pass"] = (work["price_drop_pct"] <= min_price_drop_pct) & (
                work["volume_shrink_pct"] >= min_volume_shrink_pct
            )

            i = consecutive_days
            while i < len(work) - hold_days - 1:
                window = work.iloc[i - consecutive_days + 1 : i + 1]
                if bool(window["daily_pass"].all()):
                    buy_idx = i + 1
                    sell_idx = buy_idx + hold_days
                    if sell_idx >= len(work):
                        break

                    buy_row = work.iloc[buy_idx]
                    sell_row = work.iloc[sell_idx]
                    buy_price = float(buy_row["open"])
                    sell_price = float(sell_row["open"])
                    if buy_price <= 0:
                        i += 1
                        continue

                    ret = (sell_price - buy_price) / buy_price
                    events.append(
                        {
                            "symbol": symbol,
                            "signal_date": work.iloc[i]["datetime"],
                            "buy_datetime": buy_row["datetime"],
                            "sell_datetime": sell_row["datetime"],
                            "buy_price": round(buy_price, 4),
                            "sell_price": round(sell_price, 4),
                            "return": round(ret, 6),
                            "reason": f"连续{consecutive_days}天缩量下跌",
                        }
                    )
                    i = sell_idx + 1
                else:
                    i += 1

        diagnostics = {
            "universe_size": len(universe),
            "data_available_count": data_available_count,
            "data_empty_count": data_empty_count,
            "signal_hit_count": len(events),
            "effective_universe_filter": {
                "boards": boards,
                "exclude_st": exclude_st,
            },
        }

        if data_empty_count > 0:
            preview = ",".join(empty_symbols_preview)
            suffix = "" if data_empty_count <= len(empty_symbols_preview) else "..."
            warnings.append(
                f"无可用日线数据股票数: {data_empty_count}/{len(universe)}，示例: {preview}{suffix}"
            )

        if not events:
            warnings.append("未命中任何交易信号")
            return {
                "summary": calc_summary([], [], request.initial_cash),
                "equity_curve": [],
                "trades": [],
                "warnings": warnings,
                "positions_snapshot": [],
                "symbols": universe,
                "diagnostics": diagnostics,
            }

        if progress_callback:
            progress_callback(
                {
                    "stage": "summarizing",
                    "total_symbols": total,
                    "processed_symbols": total,
                    "progress_pct": 100.0,
                }
            )

        events = sorted(events, key=lambda x: x["sell_datetime"])
        equity = request.initial_cash
        equity_curve = [
            {
                "datetime": f"{request.start_date.strftime('%Y-%m-%d')} 00:00:00",
                "equity": round(equity, 4),
            }
        ]
        trades: list[dict[str, Any]] = []

        for ev in events:
            position_amount = equity * max(0.0, min(1.0, position_size_pct))
            pnl = position_amount * ev["return"]
            equity += pnl

            trades.append(
                {
                    "symbol": ev["symbol"],
                    "datetime": ev["buy_datetime"],
                    "side": "buy",
                    "price": ev["buy_price"],
                    "qty": 1,
                    "amount": round(position_amount, 4),
                    "fee": 0.0,
                    "reason": ev["reason"],
                }
            )
            trades.append(
                {
                    "symbol": ev["symbol"],
                    "datetime": ev["sell_datetime"],
                    "side": "sell",
                    "price": ev["sell_price"],
                    "qty": 1,
                    "amount": round(position_amount * (1 + ev["return"]), 4),
                    "fee": 0.0,
                    "pnl": round(pnl, 4),
                    "reason": ev["reason"],
                }
            )
            equity_curve.append(
                {"datetime": ev["sell_datetime"], "equity": round(equity, 4)}
            )

        summary = calc_summary(equity_curve, trades, request.initial_cash)
        warnings.append(
            f"strategy_select 扫描股票数: {len(universe)}，有效行情股票数: {data_available_count}，命中信号数: {len(events)}"
        )

        return {
            "summary": summary,
            "equity_curve": equity_curve,
            "trades": trades,
            "warnings": warnings,
            "positions_snapshot": [],
            "symbols": universe,
            "diagnostics": diagnostics,
        }

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

        return {
            "run_id": run.id,
            "name": run.name,
            "status": run.status,
            "strategy_id": run.strategy_id,
            "start_date": run.start_date,
            "end_date": run.end_date,
            "initial_cash": run.initial_cash,
            "benchmark": run.benchmark,
            "summary": run.summary or {},
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
        total = len(trades)
        if limit is None:
            return trades, total
        return trades[offset : offset + limit], total

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
            return [
                {
                    "symbol": r.symbol,
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
                for r in rows
            ], total

        trades, _ = self.get_run_trades(run_id=run_id, current_user=current_user, db=db)
        rounds = self._build_round_trips(trades)
        if rounds:
            self._persist_rounds(db=db, run_id=run_id, rounds=rounds)
            total = len(rounds)
            if limit is None:
                return rounds, total
            return rounds[offset : offset + limit], total
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
        total = len(snapshots)
        if limit is None:
            return snapshots, total
        return snapshots[offset : offset + limit], total

    def get_run_strategy_config(
        self, run_id: int, current_user: User, db: Session
    ) -> dict[str, Any] | None:
        run = self.get_run(run_id=run_id, current_user=current_user, db=db)
        if not run:
            return None
        return {
            "run_id": run.id,
            "strategy_id": run.strategy_id,
            "strategy_params": run.strategy_params or {},
            "cost_config": run.cost_config or {},
            "benchmark": run.benchmark,
            "symbols": run.symbols or [],
            "date_range": {
                "start_date": run.start_date,
                "end_date": run.end_date,
            },
            "meta": {
                "name": run.name,
                "status": run.status,
                "created_at": run.created_at,
            },
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
