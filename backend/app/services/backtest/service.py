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

        symbols = [s.strip() for s in request.symbols if s.strip()]
        if not symbols:
            raise ValueError("symbols 不能为空")

        capital_per_symbol = request.initial_cash / len(symbols)
        cost_model = CostModel(**request.cost_config.model_dump())

        all_trades: list[dict] = []
        all_warnings: list[str] = []
        symbol_curves: dict[str, list[dict]] = {}
        final_positions: list[dict] = []

        for symbol in symbols:
            try:
                df, _, _ = stock_service.get_minute_data(
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

        run = BacktestRun(
            user_id=current_user.id,
            name=request.name,
            status="completed",
            strategy_id=request.strategy_id,
            strategy_params=request.strategy_params,
            symbols=symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_cash=request.initial_cash,
            benchmark=request.benchmark,
            cost_config=request.cost_config.model_dump(),
            summary=summary,
            equity_curve=portfolio_curve,
            trades=all_trades,
            warnings=all_warnings,
        )

        db.add(run)
        db.commit()
        db.refresh(run)

        return {
            "run_id": run.id,
            "summary": summary,
            "equity_curve": portfolio_curve,
            "trades": all_trades,
            "positions_snapshot": final_positions,
            "warnings": all_warnings,
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
