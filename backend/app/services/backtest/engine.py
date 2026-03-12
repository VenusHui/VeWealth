"""回测执行引擎（MVP）"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from app.services.backtest.costs import CostModel
from app.services.backtest.registry import get_strategy


@dataclass
class SymbolRunResult:
    symbol: str
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    warnings: list[str]
    last_equity: float
    final_position: int


def _is_limit_up(curr_open: float, prev_close: float) -> bool:
    return curr_open >= prev_close * 1.10


def _is_limit_down(curr_open: float, prev_close: float) -> bool:
    return curr_open <= prev_close * 0.90


def run_for_symbol(
    symbol: str,
    df: pd.DataFrame,
    strategy_id: str,
    strategy_params: dict,
    init_cash: float,
    cost_model: CostModel,
) -> SymbolRunResult:
    warnings: list[str] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    if df.empty or len(df) < 3:
        return SymbolRunResult(symbol, [], [], [f"{symbol}: 可用数据不足"], init_cash, 0)

    work_df = df.copy()
    work_df["datetime"] = pd.to_datetime(work_df["datetime"])
    work_df = work_df.sort_values("datetime").reset_index(drop=True)

    strategy = get_strategy(strategy_id)
    signal_df = strategy.generate_signals(work_df, strategy_params)

    cash = init_cash
    shares = 0
    entry_price = 0.0
    entry_date = None

    for i in range(0, len(signal_df) - 1):
        curr = signal_df.iloc[i]
        nxt = signal_df.iloc[i + 1]
        ts = curr["datetime"]

        curr_close = float(curr["close"])
        curr_equity = cash + shares * curr_close
        equity_curve.append({"datetime": ts.strftime("%Y-%m-%d %H:%M:%S"), "equity": round(curr_equity, 4)})

        buy_signal = bool(curr.get("buy_signal", False))
        sell_signal = bool(curr.get("sell_signal", False))

        exec_open = float(nxt["open"])
        prev_close = curr_close
        trade_dt: datetime = nxt["datetime"]

        if buy_signal and shares == 0:
            if _is_limit_up(exec_open, prev_close):
                warnings.append(f"{symbol} {trade_dt}: 触发买入但次日开盘涨停，未成交")
                continue

            deal_price = cost_model.apply_buy_slippage(exec_open)
            lot_size = 100
            max_lots = int(cash // (deal_price * lot_size))
            qty = max_lots * lot_size

            if qty <= 0:
                warnings.append(f"{symbol} {trade_dt}: 现金不足，买入失败")
                continue

            amount = qty * deal_price
            fee = cost_model.buy_cost(amount)
            total_cost = amount + fee

            while qty > 0 and total_cost > cash:
                qty -= lot_size
                amount = qty * deal_price
                fee = cost_model.buy_cost(amount)
                total_cost = amount + fee

            if qty <= 0:
                warnings.append(f"{symbol} {trade_dt}: 扣除费用后可买数量为0")
                continue

            cash -= total_cost
            shares += qty
            entry_price = deal_price
            entry_date = trade_dt.date()
            trades.append(
                {
                    "symbol": symbol,
                    "datetime": trade_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "side": "buy",
                    "price": round(deal_price, 4),
                    "qty": qty,
                    "amount": round(amount, 4),
                    "fee": round(fee, 4),
                }
            )

        if sell_signal and shares > 0:
            if entry_date is not None and trade_dt.date() <= entry_date:
                warnings.append(f"{symbol} {trade_dt}: 触发卖出但受T+1限制")
                continue

            if _is_limit_down(exec_open, prev_close):
                warnings.append(f"{symbol} {trade_dt}: 触发卖出但次日开盘跌停，未成交")
                continue

            deal_price = cost_model.apply_sell_slippage(exec_open)
            qty = shares
            amount = qty * deal_price
            fee = cost_model.sell_cost(amount)

            cash += amount - fee
            pnl = (deal_price - entry_price) * qty - fee
            shares = 0
            entry_price = 0.0
            entry_date = None

            trades.append(
                {
                    "symbol": symbol,
                    "datetime": trade_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "side": "sell",
                    "price": round(deal_price, 4),
                    "qty": qty,
                    "amount": round(amount, 4),
                    "fee": round(fee, 4),
                    "pnl": round(pnl, 4),
                }
            )

    last = signal_df.iloc[-1]
    final_equity = cash + shares * float(last["close"])
    equity_curve.append(
        {
            "datetime": last["datetime"].strftime("%Y-%m-%d %H:%M:%S"),
            "equity": round(final_equity, 4),
        }
    )

    return SymbolRunResult(
        symbol=symbol,
        equity_curve=equity_curve,
        trades=trades,
        warnings=warnings,
        last_equity=final_equity,
        final_position=shares,
    )
