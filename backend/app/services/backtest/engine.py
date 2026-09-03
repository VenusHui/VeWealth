"""组合级、逐交易日回测执行引擎。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import pandas as pd

from app.services.backtest.costs import CostModel
from app.services.backtest.registry import get_strategy


@dataclass(frozen=True)
class SecurityRule:
    """影响 A 股成交限制的静态证券属性。"""

    board: str = "main"
    is_st: bool = False


@dataclass
class PortfolioRunResult:
    equity_curve: list[dict[str, Any]]
    positions_snapshot: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    warnings: list[str]
    final_positions: list[dict[str, Any]]


@dataclass
class _Position:
    shares: int
    entry_price: float
    entry_fee: float
    entry_date: pd.Timestamp
    exit_index: int
    reason: str


def infer_board(symbol: str) -> str:
    code = str(symbol or "").strip().split(".")[0]
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "gem"
    if code.startswith(("4", "8", "92")):
        return "bse"
    return "main"


def price_limit_rate(
    symbol: str,
    trade_date: date | datetime | pd.Timestamp,
    rule: SecurityRule | None = None,
) -> float:
    """返回指定交易日适用的涨跌停比例。"""

    resolved = rule or SecurityRule(board=infer_board(symbol))
    if resolved.is_st:
        return 0.05
    board = (resolved.board or infer_board(symbol)).lower()
    if board == "bse":
        return 0.30
    if board == "star":
        return 0.20
    if board == "gem" and pd.Timestamp(trade_date) >= pd.Timestamp("2020-08-24"):
        return 0.20
    return 0.10


def _limit_price(previous_close: float, rate: float, direction: int) -> float:
    value = Decimal(str(previous_close)) * (
        Decimal("1") + Decimal(str(rate)) * Decimal(direction)
    )
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def is_limit_up(
    open_price: float,
    previous_close: float,
    symbol: str,
    trade_date: date | datetime | pd.Timestamp,
    rule: SecurityRule | None = None,
) -> bool:
    rate = price_limit_rate(symbol, trade_date, rule)
    return open_price >= _limit_price(previous_close, rate, 1) - 1e-9


def is_limit_down(
    open_price: float,
    previous_close: float,
    symbol: str,
    trade_date: date | datetime | pd.Timestamp,
    rule: SecurityRule | None = None,
) -> bool:
    rate = price_limit_rate(symbol, trade_date, rule)
    return open_price <= _limit_price(previous_close, rate, -1) + 1e-9


def _prepare_market_data(
    market_data_map: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    prepared: dict[str, pd.DataFrame] = {}
    for raw_symbol, frame in market_data_map.items():
        symbol = str(raw_symbol).strip()
        if not symbol or frame is None or frame.empty:
            continue
        if not {"datetime", "open", "close"}.issubset(frame.columns):
            continue
        work = frame.copy()
        work["datetime"] = pd.to_datetime(work["datetime"])
        work["trade_date"] = work["datetime"].dt.normalize()
        work = (
            work.sort_values("datetime")
            .drop_duplicates(subset=["trade_date"], keep="last")
            .reset_index(drop=True)
        )
        work["bar_index"] = work.index
        prepared[symbol] = work
    return prepared


def _max_affordable_quantity(
    cash: float,
    exposure_budget: float,
    deal_price: float,
    cost_model: CostModel,
    lot_size: int,
) -> int:
    if cash <= 0 or exposure_budget <= 0 or deal_price <= 0:
        return 0
    qty = int(min(cash, exposure_budget) // (deal_price * lot_size)) * lot_size
    while qty > 0:
        amount = qty * deal_price
        if amount + cost_model.buy_cost(amount) <= cash + 1e-9:
            return qty
        qty -= lot_size
    return 0


def run_portfolio(
    market_data_map: dict[str, pd.DataFrame],
    orders_df: pd.DataFrame,
    initial_cash: float,
    cost_model: CostModel,
    *,
    hold_days: int = 5,
    max_total_position_pct: float = 1.0,
    default_position_size_pct: float = 0.1,
    security_rules: dict[str, SecurityRule] | None = None,
    lot_size: int = 100,
) -> PortfolioRunResult:
    """按交易日推进共享现金账户；订单日期是信号日，最早次日开盘成交。"""

    if initial_cash <= 0:
        raise ValueError("initial_cash 必须大于 0")
    if lot_size <= 0:
        raise ValueError("lot_size 必须大于 0")

    data = _prepare_market_data(market_data_map)
    rules = security_rules or {}
    warnings: list[str] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    positions: dict[str, _Position] = {}
    last_prices: dict[str, float] = {}
    cash = float(initial_cash)
    hold_days = max(1, int(hold_days))
    max_total_position_pct = max(0.0, min(1.0, float(max_total_position_pct)))
    default_position_size_pct = max(0.0, min(1.0, float(default_position_size_pct)))

    bars_by_date: dict[pd.Timestamp, dict[str, pd.Series]] = {}
    for symbol, frame in data.items():
        for _, bar in frame.iterrows():
            bars_by_date.setdefault(bar["trade_date"], {})[symbol] = bar

    buys_by_date: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    if orders_df is not None and not orders_df.empty:
        ordered = orders_df.copy().reset_index(drop=True)
        ordered["_priority"] = ordered.index
        ordered["trade_date"] = pd.to_datetime(ordered["trade_date"]).dt.normalize()
        for _, order in ordered.iterrows():
            symbol = str(order.get("symbol") or "").strip()
            frame = data.get(symbol)
            if frame is None:
                continue
            signal_date = order["trade_date"]
            future = frame[frame["trade_date"] > signal_date]
            if future.empty:
                warnings.append(
                    f"{symbol} {signal_date.date()}: 信号后无下一交易日，未成交"
                )
                continue
            buy_date = future.iloc[0]["trade_date"]
            payload = order.to_dict()
            payload["signal_date"] = signal_date
            buys_by_date.setdefault(buy_date, []).append(payload)

    for trade_date in sorted(bars_by_date):
        day_bars = bars_by_date[trade_date]

        # 到期卖单先成交；跌停时在后续该证券交易日继续尝试。
        for symbol in sorted(list(positions)):
            position = positions[symbol]
            bar = day_bars.get(symbol)
            if bar is None or int(bar["bar_index"]) < position.exit_index:
                continue
            frame = data[symbol]
            idx = int(bar["bar_index"])
            if idx <= 0 or trade_date <= position.entry_date:
                continue
            raw_open = float(bar["open"])
            previous_close = float(frame.iloc[idx - 1]["close"])
            if is_limit_down(
                raw_open, previous_close, symbol, trade_date, rules.get(symbol)
            ):
                warnings.append(f"{symbol} {trade_date.date()}: 卖出遇跌停，顺延")
                continue
            deal_price = cost_model.apply_sell_slippage(raw_open)
            amount = position.shares * deal_price
            fee = cost_model.sell_cost(amount)
            cash += amount - fee
            pnl = (
                amount
                - fee
                - (position.shares * position.entry_price + position.entry_fee)
            )
            trades.append(
                {
                    "symbol": symbol,
                    "datetime": pd.Timestamp(bar["datetime"]).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "side": "sell",
                    "price": round(deal_price, 6),
                    "qty": position.shares,
                    "amount": round(amount, 6),
                    "fee": round(fee, 6),
                    "pnl": round(pnl, 6),
                    "reason": position.reason,
                }
            )
            del positions[symbol]

        orders = sorted(
            buys_by_date.get(trade_date, []), key=lambda row: int(row["_priority"])
        )
        for order in orders:
            symbol = str(order["symbol"])
            if symbol in positions:
                warnings.append(
                    f"{symbol} {trade_date.date()}: 已持仓，忽略重叠买入信号"
                )
                continue
            bar = day_bars.get(symbol)
            if bar is None:
                continue
            frame = data[symbol]
            idx = int(bar["bar_index"])
            if idx <= 0:
                continue
            raw_open = float(bar["open"])
            previous_close = float(frame.iloc[idx - 1]["close"])
            if is_limit_up(
                raw_open, previous_close, symbol, trade_date, rules.get(symbol)
            ):
                warnings.append(f"{symbol} {trade_date.date()}: 买入遇涨停，未成交")
                continue

            open_prices = {
                held_symbol: (
                    float(day_bars[held_symbol]["open"])
                    if held_symbol in day_bars
                    else last_prices.get(held_symbol, held_position.entry_price)
                )
                for held_symbol, held_position in positions.items()
            }
            held_value = sum(
                positions[held_symbol].shares * price
                for held_symbol, price in open_prices.items()
            )
            pretrade_equity = cash + held_value
            remaining_exposure = max(
                0.0, pretrade_equity * max_total_position_pct - held_value
            )
            raw_requested_pct = order.get("position_size_pct")
            requested_pct = (
                default_position_size_pct
                if raw_requested_pct is None or pd.isna(raw_requested_pct)
                else float(raw_requested_pct)
            )
            requested_pct = max(0.0, min(1.0, requested_pct))
            exposure_budget = min(pretrade_equity * requested_pct, remaining_exposure)
            deal_price = cost_model.apply_buy_slippage(raw_open)
            qty = _max_affordable_quantity(
                cash, exposure_budget, deal_price, cost_model, lot_size
            )
            while qty > 0:
                prospective_amount = qty * deal_price
                prospective_fee = cost_model.buy_cost(prospective_amount)
                post_fee_equity = pretrade_equity - prospective_fee
                if (
                    held_value + prospective_amount
                    <= post_fee_equity * max_total_position_pct + 1e-9
                ):
                    break
                qty -= lot_size
            if qty <= 0:
                warnings.append(
                    f"{symbol} {trade_date.date()}: 现金或仓位额度不足，未成交"
                )
                continue
            amount = qty * deal_price
            fee = cost_model.buy_cost(amount)
            cash -= amount + fee
            if cash < -1e-8:
                raise RuntimeError("组合回测现金变为负数")
            cash = max(cash, 0.0)
            positions[symbol] = _Position(
                shares=qty,
                entry_price=deal_price,
                entry_fee=fee,
                entry_date=trade_date,
                exit_index=idx + hold_days,
                reason=str(order.get("reason") or "strategy_candidate"),
            )
            trades.append(
                {
                    "symbol": symbol,
                    "datetime": pd.Timestamp(bar["datetime"]).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "side": "buy",
                    "price": round(deal_price, 6),
                    "qty": qty,
                    "amount": round(amount, 6),
                    "fee": round(fee, 6),
                    "reason": str(order.get("reason") or "strategy_candidate"),
                    "signal_datetime": pd.Timestamp(order["signal_date"]).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )

        for symbol, bar in day_bars.items():
            last_prices[symbol] = float(bar["close"])

        holdings: list[dict[str, Any]] = []
        position_value = 0.0
        for symbol in sorted(positions):
            position = positions[symbol]
            last_price = last_prices.get(symbol, position.entry_price)
            market_value = position.shares * last_price
            position_value += market_value
            holdings.append(
                {
                    "symbol": symbol,
                    "qty": position.shares,
                    "last_price": round(last_price, 6),
                    "market_value": round(market_value, 6),
                }
            )
        equity = cash + position_value
        for holding in holdings:
            holding["weight"] = (
                round(float(holding["market_value"]) / equity, 10)
                if equity > 0
                else 0.0
            )
        ts_text = trade_date.strftime("%Y-%m-%d 00:00:00")
        equity_curve.append({"datetime": ts_text, "equity": round(equity, 10)})
        snapshots.append(
            {
                "snapshot_time": ts_text,
                "equity": round(equity, 10),
                "cash": round(cash, 10),
                "position_value": round(position_value, 10),
                "holdings": holdings,
            }
        )

    final_positions = [
        {
            "symbol": symbol,
            "shares": position.shares,
            "equity": round(
                position.shares * last_prices.get(symbol, position.entry_price), 10
            ),
        }
        for symbol, position in sorted(positions.items())
    ]
    return PortfolioRunResult(
        equity_curve=equity_curve,
        positions_snapshot=snapshots,
        trades=trades,
        warnings=warnings,
        final_positions=final_positions,
    )


@dataclass
class SymbolRunResult:
    """旧逐标的接口的返回结构；服务层不再使用。"""

    symbol: str
    equity_curve: list[dict[str, Any]]
    position_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    warnings: list[str]
    last_equity: float
    final_position: int


def run_for_symbol(
    symbol: str,
    df: pd.DataFrame,
    strategy_id: str,
    strategy_params: dict,
    init_cash: float,
    cost_model: CostModel,
) -> SymbolRunResult:
    """兼容旧调用方，并委托给同一组合级引擎。"""

    strategy = get_strategy(strategy_id, require_usable=True)
    work = df.copy()
    work["symbol"] = symbol
    candidates = strategy.generate_candidates(work, strategy_params)
    result = run_portfolio(
        {symbol: work},
        candidates,
        init_cash,
        cost_model,
        hold_days=int(strategy_params.get("hold_days", 5) or 5),
        max_total_position_pct=1.0,
        default_position_size_pct=1.0,
    )
    final_position = next(
        (p["shares"] for p in result.final_positions if p["symbol"] == symbol), 0
    )
    last_equity = (
        float(result.equity_curve[-1]["equity"])
        if result.equity_curve
        else float(init_cash)
    )
    position_curve = []
    for snapshot in result.positions_snapshot:
        holding = next((h for h in snapshot["holdings"] if h["symbol"] == symbol), None)
        position_curve.append(
            {
                "datetime": snapshot["snapshot_time"],
                "shares": int(holding["qty"]) if holding else 0,
                "close": float(holding["last_price"]) if holding else 0.0,
                "market_value": float(holding["market_value"]) if holding else 0.0,
                "cash": snapshot["cash"],
                "equity": snapshot["equity"],
            }
        )
    return SymbolRunResult(
        symbol=symbol,
        equity_curve=result.equity_curve,
        position_curve=position_curve,
        trades=result.trades,
        warnings=result.warnings,
        last_equity=last_equity,
        final_position=final_position,
    )
