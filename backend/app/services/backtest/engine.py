"""组合级、逐交易日回测执行引擎。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import pandas as pd

from app.services.backtest.costs import CostModel


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
    board = (resolved.board or infer_board(symbol)).lower()
    if board == "bse":
        return 0.30
    if board == "star":
        return 0.20
    if board == "gem":
        if pd.Timestamp(trade_date) >= pd.Timestamp("2020-08-24"):
            return 0.20
        return 0.05 if resolved.is_st else 0.10
    if resolved.is_st:
        return 0.05
    return 0.10


def _limit_price(previous_close: float, rate: float, direction: int) -> float:
    value = Decimal(str(previous_close)) * (
        Decimal("1") + Decimal(str(rate)) * Decimal(direction)
    )
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def price_limit_bounds(
    previous_close: float,
    symbol: str,
    trade_date: date | datetime | pd.Timestamp,
    rule: SecurityRule | None = None,
) -> tuple[float, float]:
    rate = price_limit_rate(symbol, trade_date, rule)
    return (
        _limit_price(previous_close, rate, -1),
        _limit_price(previous_close, rate, 1),
    )


def is_limit_up(
    open_price: float,
    previous_close: float,
    symbol: str,
    trade_date: date | datetime | pd.Timestamp,
    rule: SecurityRule | None = None,
) -> bool:
    _, upper_limit = price_limit_bounds(previous_close, symbol, trade_date, rule)
    return open_price >= upper_limit - 1e-9


def is_limit_down(
    open_price: float,
    previous_close: float,
    symbol: str,
    trade_date: date | datetime | pd.Timestamp,
    rule: SecurityRule | None = None,
) -> bool:
    lower_limit, _ = price_limit_bounds(previous_close, symbol, trade_date, rule)
    return open_price <= lower_limit + 1e-9


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
        work = work.sort_values("datetime")
        # 行情契约是日线；若上游意外返回日内多 bar，也必须使用首根的
        # open 与末根的 close，不能把尾盘 bar 的 open 当成次日开盘价。
        first_bars = work.drop_duplicates(
            subset=["trade_date"], keep="first"
        ).set_index("trade_date")
        work = work.drop_duplicates(subset=["trade_date"], keep="last").set_index(
            "trade_date"
        )
        work["open"] = first_bars["open"]
        work["datetime"] = first_bars["datetime"]
        work = work.reset_index()
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
    trade_start: str | None = None,
) -> PortfolioRunResult:
    """按交易日推进共享现金账户；订单日期是信号日，最早次日开盘成交。

    trade_start：warmup 取数时信号可能落在 start_date 之前的 bar。为避免 warmup
    污染净值/交易，仅对执行日 >= trade_start 的订单成交；warmup 最后一 bar 的信号
    若在首日开盘成交，则被保留（不丢弃首日交易）。
    """

    if initial_cash <= 0:
        raise ValueError("initial_cash 必须大于 0")
    if lot_size <= 0:
        raise ValueError("lot_size 必须大于 0")

    data = _prepare_market_data(market_data_map)
    trade_start_ts = pd.to_datetime(trade_start) if trade_start else None
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
            if trade_start_ts is not None and buy_date < trade_start_ts:
                continue
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
            lower_limit, _ = price_limit_bounds(
                previous_close, symbol, trade_date, rules.get(symbol)
            )
            deal_price = max(cost_model.apply_sell_slippage(raw_open), lower_limit)
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
            _, upper_limit = price_limit_bounds(
                previous_close, symbol, trade_date, rules.get(symbol)
            )
            deal_price = min(cost_model.apply_buy_slippage(raw_open), upper_limit)
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
        # trade_start：warmup 交易日（< trade_start）不写入净值/持仓快照，避免前置
        # 一批 0% 收益日拉长年化跨度、稀释 Sharpe（与上方订单门保持一致）。
        if trade_start_ts is None or trade_date >= trade_start_ts:
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
