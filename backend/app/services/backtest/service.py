"""回测服务"""

from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.models.backtest import BacktestRun
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

    def run_backtest(
        self, request: BacktestRunRequest, current_user: User, db: Session
    ) -> dict[str, Any]:
        if request.start_date > request.end_date:
            raise ValueError("start_date 不能晚于 end_date")

        if request.mode == "strategy_select":
            result = self._run_strategy_select_mode(request)
        else:
            result = self._run_manual_symbols_mode(request)

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
            summary=result["summary"],
            equity_curve=result["equity_curve"],
            trades=result["trades"],
            warnings=result["warnings"],
        )

        db.add(run)
        db.commit()
        db.refresh(run)

        return {
            "run_id": run.id,
            "summary": result["summary"],
            "equity_curve": result["equity_curve"],
            "trades": result["trades"],
            "positions_snapshot": result.get("positions_snapshot", []),
            "warnings": result["warnings"],
        }

    def _run_manual_symbols_mode(self, request: BacktestRunRequest) -> dict[str, Any]:
        symbols = [s.strip() for s in request.symbols if s.strip()]
        if not symbols:
            raise ValueError("manual_symbols 模式下 symbols 不能为空")

        capital_per_symbol = request.initial_cash / len(symbols)
        cost_model = CostModel(**request.cost_config.model_dump())

        all_trades: list[dict] = []
        all_warnings: list[str] = []
        symbol_curves: dict[str, list[dict]] = {}
        final_positions: list[dict] = []

        for symbol in symbols:
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
            all_trades.extend(symbol_result.trades)
            all_warnings.extend(symbol_result.warnings)
            final_positions.append(
                {
                    "symbol": symbol,
                    "shares": symbol_result.final_position,
                    "equity": round(symbol_result.last_equity, 4),
                }
            )

        portfolio_curve = self._merge_symbol_curves(symbol_curves)
        summary = calc_summary(portfolio_curve, all_trades, request.initial_cash)

        return {
            "summary": summary,
            "equity_curve": portfolio_curve,
            "trades": all_trades,
            "warnings": all_warnings,
            "positions_snapshot": final_positions,
            "symbols": symbols,
        }

    def _run_strategy_select_mode(self, request: BacktestRunRequest) -> dict[str, Any]:
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
        max_universe_size = int(params.get("max_universe_size", 300))

        if request.universe_type == "custom":
            universe = [s.strip() for s in request.pool_symbols if s.strip()]
        else:
            universe = stock_service.get_all_stock_symbols(limit=max_universe_size)

        if not universe:
            raise ValueError(
                "选股池为空，无法执行 strategy_select 回测（可能是行情源连接失败）。请先改用 custom 股票池，或稍后重试。"
            )

        warnings: list[str] = []
        events: list[dict[str, Any]] = []

        for symbol in universe:
            df, _, _ = stock_service.get_daily_data(
                symbol=symbol,
                start_date=request.start_date.strftime("%Y-%m-%d"),
                end_date=request.end_date.strftime("%Y-%m-%d"),
            )
            if df.empty or len(df) < (consecutive_days + hold_days + 2):
                continue

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

        if not events:
            warnings.append("未命中任何交易信号")
            return {
                "summary": calc_summary([], [], request.initial_cash),
                "equity_curve": [],
                "trades": [],
                "warnings": warnings,
                "positions_snapshot": [],
                "symbols": universe,
            }

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
            f"strategy_select 扫描股票数: {len(universe)}，命中信号数: {len(events)}"
        )

        return {
            "summary": summary,
            "equity_curve": equity_curve,
            "trades": trades,
            "warnings": warnings,
            "positions_snapshot": [],
            "symbols": universe,
        }

    def list_runs(
        self, current_user: User, db: Session, limit: int = 20, offset: int = 0
    ):
        query = db.query(BacktestRun).filter(BacktestRun.user_id == current_user.id)
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
