"""分钟级、逐 bar 的组合撮合引擎。

与 ``backtest.engine.run_portfolio`` **同构**：同一份 orders 契约（``symbol`` +
``signal_datetime`` 或 ``trade_date``）、同一个 ``PortfolioRunResult`` 返回，差别只在
bar 粒度以及由此带来的 A 股微观约束：

- **集合竞价**：信号在收盘后产生（或落在收盘集合竞价 14:57–15:00）时，最早只能在
  **下一交易日的开盘集合竞价**成交。开盘集合竞价的结果就是当日第一根 bar 的 open，
  因此这类成交记 ``fill_phase="call_auction"``，其余记 ``"continuous"``。
- **涨跌停**：以**前一交易日收盘价**（由分钟序列自身聚合，见
  ``session.previous_close_by_session``）算上下限。买入遇涨停不成交、卖出遇跌停不
  成交；分钟级下不直接放弃，而是在**当日后续 bar** 继续尝试，当日仍未成交则作废并
  告警（``defer_to_next_bar`` 控制）。
- **停牌**：某标的当日无 bar 或整日成交量为 0，当日不可成交 —— 持仓卖不出（顺延至
  下一有行情的交易日），买单作废并告警，不静默跳过。成交量字段**缺失或全为 NaN**
  不算停牌（那是源没给数据，不是确实没成交）。
- **T+1**：当日买入当日不可卖出（要求卖出 bar 所在交易日严格晚于建仓日）。
- **止损 / 止盈**（``stop_loss_pct`` / ``take_profit_pct``，可选）：按 bar 的
  low/high 触发 —— 这是分钟粒度相对日线唯一能新增的退出语义。

同一次顺延在同一 (标的, 交易日, 原因) 上只告警一次：全天封板是 240 根 bar，逐 bar
告警会把近似重复的字符串灌进 API 响应，把真正的信息淹掉。

净值曲线是分钟粒度；持仓快照默认按交易日收盘落一份（``snapshot_every_bar`` 可改为
逐 bar），避免长区间产生几十万条快照。绩效指标由调用方在**日频**曲线上计算
（见 ``minute.service``），否则 ``metrics.calc_summary`` 的 sqrt(252) 会把分钟 bar
当成交易日。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.services.backtest.costs import CostModel
from app.services.backtest.engine import (
    PortfolioRunResult,
    SecurityRule,
    is_limit_down,
    is_limit_up,
    price_limit_bounds,
    size_entry_order,
)
from app.services.backtest.minute.session import (
    is_closing_auction,
    is_halted_day,
    previous_close_by_session,
)

#: 日线候选契约里的 ``trade_date`` 是「当日收盘才可知」的信号，故按收盘时刻处理
_SESSION_CLOSE = pd.Timestamp("1970-01-01 15:00:00").time()


@dataclass(frozen=True)
class MinuteExecutionConfig:
    """分钟撮合的成交约束。"""

    hold_days: int = 1
    lot_size: int = 100
    max_total_position_pct: float = 1.0
    default_position_size_pct: float = 0.1
    #: 相对建仓价的止损比例（如 0.03 = -3%），None 表示不启用
    stop_loss_pct: float | None = None
    #: 相对建仓价的止盈比例（如 0.05 = +5%），None 表示不启用
    take_profit_pct: float | None = None
    #: 当日因涨停/停牌未成交时，是否在当日后续 bar 继续尝试
    defer_to_next_bar: bool = True
    #: 是否逐 bar 落持仓快照（默认按交易日收盘落一份）
    snapshot_every_bar: bool = False


@dataclass
class _MinutePosition:
    shares: int
    entry_price: float
    entry_fee: float
    entry_session: pd.Timestamp
    exit_session_index: int
    reason: str


@dataclass
class _PendingOrder:
    symbol: str
    payload: dict[str, Any]
    priority: int
    expires_session: pd.Timestamp
    warnings: list[str] = field(default_factory=list)


def _prepare_minute_market_data(
    market_data_map: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """归一化分钟帧：时间升序去重 + bar_index / session_date 派生列。"""
    prepared: dict[str, pd.DataFrame] = {}
    for raw_symbol, frame in market_data_map.items():
        symbol = str(raw_symbol).strip()
        if not symbol or frame is None or frame.empty:
            continue
        if not {"datetime", "open", "close"}.issubset(frame.columns):
            continue
        work = frame.copy()
        work["datetime"] = pd.to_datetime(work["datetime"])
        work = work.dropna(subset=["datetime"]).sort_values("datetime")
        work = work.drop_duplicates(subset=["datetime"], keep="last")
        work = work.reset_index(drop=True)
        if work.empty:
            continue
        if "high" not in work.columns:
            work["high"] = work[["open", "close"]].max(axis=1)
        if "low" not in work.columns:
            work["low"] = work[["open", "close"]].min(axis=1)
        # volume 缺失时不补 0：那是「源没给成交量」，不是「确实零成交」，补 0 会让该
        # 标的整天被判停牌（见 session.is_halted_day）
        work["session_date"] = work["datetime"].dt.normalize()
        work["bar_index"] = work.index
        prepared[symbol] = work
    return prepared


def _parse_signal_time(order: pd.Series | dict) -> pd.Timestamp | None:
    """订单信号时刻。

    ``signal_datetime`` 优先（分钟级信号）；缺省回退到日线契约的 ``trade_date``，并按
    **当日收盘**（15:00）处理 —— 日线 bar 的收盘价在收盘那一刻才可知。
    """
    raw = None
    if isinstance(order, pd.Series):
        raw = order.get("signal_datetime")
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            raw = order.get("trade_date")
    else:
        raw = order.get("signal_datetime") or order.get("trade_date")
    if raw is None:
        return None
    try:
        stamp = pd.Timestamp(raw)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    if stamp.time() == pd.Timestamp("1970-01-01").time():
        stamp = pd.Timestamp.combine(stamp.date(), _SESSION_CLOSE)
    return stamp


def _session_open_bar_indices(frame: pd.DataFrame) -> set[int]:
    """每交易日第一根 bar 的 bar_index（= 开盘集合竞价成交 bar）。"""
    first = frame.drop_duplicates(subset=["session_date"], keep="first")
    return {int(idx) for idx in first["bar_index"].tolist()}


def _equity_holdings(
    positions: dict[str, _MinutePosition],
    day_bars: dict[str, pd.Series],
    last_prices: dict[str, float],
    cash: float,
) -> tuple[list[dict[str, Any]], float, float]:
    holdings: list[dict[str, Any]] = []
    position_value = 0.0
    for symbol in sorted(positions):
        position = positions[symbol]
        bar = day_bars.get(symbol)
        last_price = (
            float(bar["close"])
            if bar is not None
            else last_prices.get(symbol, position.entry_price)
        )
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
            round(float(holding["market_value"]) / equity, 10) if equity > 0 else 0.0
        )
    return holdings, position_value, equity


def run_minute_portfolio(
    market_data_map: dict[str, pd.DataFrame],
    orders_df: pd.DataFrame | None,
    initial_cash: float,
    cost_model: CostModel,
    *,
    config: MinuteExecutionConfig | None = None,
    security_rules: dict[str, SecurityRule] | None = None,
    trade_start: str | None = None,
) -> PortfolioRunResult:
    """按分钟 bar 推进共享现金账户；信号 bar 之后最早一根 bar 成交。

    trade_start：warmup 取数时信号可能落在 start_date 之前。执行日早于 trade_start 的
    订单被丢弃，且该日不写入净值/快照，避免前置的 0% 收益 bar 稀释年化与 Sharpe
    （与日线 ``run_portfolio`` 同口径）。
    """
    if initial_cash <= 0:
        raise ValueError("initial_cash 必须大于 0")
    cfg = config or MinuteExecutionConfig()
    if cfg.lot_size <= 0:
        raise ValueError("lot_size 必须大于 0")
    hold_days = max(1, int(cfg.hold_days))  # T+1：持仓至少跨一个交易日
    max_total_position_pct = max(0.0, min(1.0, float(cfg.max_total_position_pct)))
    default_position_size_pct = max(0.0, min(1.0, float(cfg.default_position_size_pct)))
    stop_loss_pct = cfg.stop_loss_pct if (cfg.stop_loss_pct or 0) > 0 else None
    take_profit_pct = cfg.take_profit_pct if (cfg.take_profit_pct or 0) > 0 else None

    data = _prepare_minute_market_data(market_data_map)
    trade_start_ts = pd.to_datetime(trade_start).normalize() if trade_start else None
    rules = security_rules or {}
    warnings: list[str] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    positions: dict[str, _MinutePosition] = {}
    last_prices: dict[str, float] = {}
    cash = float(initial_cash)

    # 全市场交易日并集 → 统一会话序号（多标的停牌日不影响序号连续性）
    session_values: set[pd.Timestamp] = set()
    for frame in data.values():
        session_values.update(frame["session_date"].unique())
    sessions = sorted(pd.Timestamp(value) for value in session_values)
    session_index = {value: idx for idx, value in enumerate(sessions)}

    bars_by_time: dict[pd.Timestamp, dict[str, pd.Series]] = {}
    session_last_time: dict[pd.Timestamp, pd.Timestamp] = {}
    session_open_indices: dict[str, set[int]] = {}
    prev_close_map: dict[str, dict[pd.Timestamp, float]] = {}
    halted_sessions: dict[str, set[pd.Timestamp]] = {}
    for symbol, frame in data.items():
        for _, bar in frame.iterrows():
            bars_by_time.setdefault(bar["datetime"], {})[symbol] = bar
            # 当日最晚 bar 取全市场最大值：只按遍历顺序覆盖会让收盘快照落在「最后一个
            # 被遍历到的标的」的最后一根 bar 上 —— 该标的若缺尾 bar，快照时点就错了，
            # 且结果依赖标的顺序
            current_last = session_last_time.get(bar["session_date"])
            if current_last is None or bar["datetime"] > current_last:
                session_last_time[bar["session_date"]] = bar["datetime"]
        session_open_indices[symbol] = _session_open_bar_indices(frame)
        prev_close_map[symbol] = previous_close_by_session(frame)
        # 有 bar 但整日零成交同样是停牌（一字停牌无成交），不能只看 bar 是否存在
        halted_sessions[symbol] = {
            pd.Timestamp(session)
            for session, day_frame in frame.groupby("session_date")
            if is_halted_day(day_frame)
        }
    times = sorted(bars_by_time)

    # 窗口首日拿不到前收盘价 → 当日涨跌停约束缺失。调用方（minute.service）默认会向前
    # 多取若干自然日来覆盖，真被源侧缺数据挡掉时必须显式说出来，不能静默放宽
    if trade_start_ts is not None:
        for symbol, frame in data.items():
            first_session = pd.Timestamp(frame["session_date"].min())
            if first_session < trade_start_ts:
                continue  # 回测区间之前有行情，首日约束正常
            if first_session not in prev_close_map.get(symbol, {}):
                warnings.append(
                    f"{symbol} {first_session.date()}: 窗口首日无前收盘价，"
                    "当日涨跌停约束缺失"
                )

    # 同一次顺延在同一 (标的, 交易日, 原因) 上只告警一次，与停牌路径口径一致
    warned: set[tuple[str, pd.Timestamp, str]] = set()

    def warn_once(
        symbol: str, session: pd.Timestamp, reason: str, message: str
    ) -> None:
        key = (symbol, session, reason)
        if key in warned:
            return
        warned.add(key)
        warnings.append(message)

    scheduled: dict[pd.Timestamp, list[_PendingOrder]] = {}
    if orders_df is not None and not orders_df.empty:
        ordered = orders_df.copy().reset_index(drop=True)
        ordered["_priority"] = ordered.index
        for _, order in ordered.iterrows():
            symbol = str(order.get("symbol") or "").strip()
            frame = data.get(symbol)
            if frame is None:
                continue
            signal_time = _parse_signal_time(order)
            if signal_time is None:
                warnings.append(
                    f"{symbol}: 订单缺少 signal_datetime / trade_date，已跳过"
                )
                continue
            # 信号 bar 之后最早的一根 bar：收盘信号自然落到下一交易日的开盘集合竞价
            pos = bisect_right(times, signal_time)
            if is_closing_auction(signal_time):
                # 收盘集合竞价（14:57–15:00）内不再有连续竞价成交，当日已无法成交；
                # 跳过当日剩余 bar，最早进下一交易日的开盘集合竞价
                session = signal_time.normalize()
                while pos < len(times) and times[pos].normalize() == session:
                    pos += 1
            if pos >= len(times):
                warnings.append(f"{symbol} {signal_time}: 信号后无下一 bar，未成交")
                continue
            exec_time = times[pos]
            exec_session = exec_time.normalize()
            if trade_start_ts is not None and exec_session < trade_start_ts:
                continue
            scheduled.setdefault(exec_time, []).append(
                _PendingOrder(
                    symbol=symbol,
                    payload=order.to_dict(),
                    priority=int(order["_priority"]),
                    expires_session=exec_session,
                )
            )

    carry: list[_PendingOrder] = []
    for ts in times:
        day_bars = bars_by_time[ts]
        session_date = ts.normalize()
        session_idx = session_index.get(session_date, 0)

        # 1) 卖出：先于买入，保证同日换仓的现金可用（与日线一致）
        for symbol in sorted(list(positions)):
            position = positions[symbol]
            if position.entry_session >= session_date:
                continue  # T+1
            bar = day_bars.get(symbol)
            if bar is None or session_date in halted_sessions.get(symbol, set()):
                # 停牌/无行情：当日不可成交，顺延到下一有行情的 bar（每交易日只告警一次）
                warn_once(
                    symbol,
                    session_date,
                    "halt",
                    f"{symbol} {session_date.date()}: 停牌/无行情，当日无法卖出，顺延",
                )
                continue
            rule = rules.get(symbol)
            previous_close = prev_close_map.get(symbol, {}).get(session_date)
            if previous_close is not None and is_limit_down(
                float(bar["open"]), previous_close, symbol, ts, rule
            ):
                warn_once(
                    symbol,
                    session_date,
                    "limit_down_defer",
                    f"{symbol} {session_date.date()}: 卖出遇跌停，顺延",
                )
                continue

            exit_price: float | None = None
            reason = position.reason
            if stop_loss_pct is not None:
                stop_price = position.entry_price * (1 - stop_loss_pct)
                if float(bar["low"]) <= stop_price:
                    # 跳空低开时按开盘价成交，否则按止损价
                    exit_price = min(float(bar["open"]), stop_price)
                    reason = "stop_loss"
            if exit_price is None and take_profit_pct is not None:
                target_price = position.entry_price * (1 + take_profit_pct)
                if float(bar["high"]) >= target_price:
                    exit_price = max(float(bar["open"]), target_price)
                    reason = "take_profit"
            if exit_price is None:
                if session_idx < position.exit_session_index:
                    continue
                exit_price = float(bar["open"])

            deal_price = cost_model.apply_sell_slippage(exit_price)
            if previous_close is not None:
                lower_limit, _ = price_limit_bounds(previous_close, symbol, ts, rule)
                deal_price = max(deal_price, lower_limit)
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
                    "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "side": "sell",
                    "price": round(deal_price, 6),
                    "qty": position.shares,
                    "amount": round(amount, 6),
                    "fee": round(fee, 6),
                    "pnl": round(pnl, 6),
                    "reason": reason,
                    "fill_phase": (
                        "call_auction"
                        if int(bar["bar_index"])
                        in session_open_indices.get(symbol, set())
                        else "continuous"
                    ),
                }
            )
            del positions[symbol]

        # 2) 买入：当日未成交的订单在后续 bar 继续尝试
        queue = carry + scheduled.get(ts, [])
        carry = []
        for order in sorted(queue, key=lambda item: item.priority):
            symbol = order.symbol
            if order.expires_session < session_date:
                warnings.append(
                    f"{symbol} {order.expires_session.date()}: 当日未成交"
                    "（停牌/涨停），买单作废"
                )
                continue
            if symbol in positions:
                warnings.append(f"{symbol} {ts}: 已持仓，忽略重叠买入信号")
                continue
            bar = day_bars.get(symbol)
            if bar is None or session_date in halted_sessions.get(symbol, set()):
                carry.append(order)  # 停牌/本 bar 无行情，继续等
                continue
            rule = rules.get(symbol)
            previous_close = prev_close_map.get(symbol, {}).get(session_date)
            if previous_close is not None and is_limit_up(
                float(bar["open"]), previous_close, symbol, ts, rule
            ):
                if cfg.defer_to_next_bar:
                    warn_once(
                        symbol,
                        session_date,
                        "limit_up_defer",
                        f"{symbol} {session_date.date()}: 买入遇涨停，顺延至当日后续 bar",
                    )
                    carry.append(order)
                else:
                    warnings.append(f"{symbol} {ts}: 买入遇涨停，未成交")
                continue

            upper_limit = None
            if previous_close is not None:
                _, upper_limit = price_limit_bounds(previous_close, symbol, ts, rule)
            deal_price = cost_model.apply_buy_slippage(float(bar["open"]))
            if upper_limit is not None:
                deal_price = min(deal_price, upper_limit)

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
            qty = size_entry_order(
                cash=cash,
                pretrade_equity=cash + held_value,
                held_value=held_value,
                requested_pct=order.payload.get("position_size_pct"),
                default_position_size_pct=default_position_size_pct,
                max_total_position_pct=max_total_position_pct,
                deal_price=deal_price,
                cost_model=cost_model,
                lot_size=cfg.lot_size,
            )
            if qty <= 0:
                warnings.append(f"{symbol} {ts}: 现金或仓位额度不足，未成交")
                continue
            amount = qty * deal_price
            fee = cost_model.buy_cost(amount)
            cash -= amount + fee
            if cash < -1e-8:
                raise RuntimeError("组合回测现金变为负数")
            cash = max(cash, 0.0)
            positions[symbol] = _MinutePosition(
                shares=qty,
                entry_price=deal_price,
                entry_fee=fee,
                entry_session=session_date,
                exit_session_index=session_idx + hold_days,
                reason=str(order.payload.get("reason") or "strategy_candidate"),
            )
            trades.append(
                {
                    "symbol": symbol,
                    "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "side": "buy",
                    "price": round(deal_price, 6),
                    "qty": qty,
                    "amount": round(amount, 6),
                    "fee": round(fee, 6),
                    "reason": str(order.payload.get("reason") or "strategy_candidate"),
                    "signal_datetime": (
                        pd.Timestamp(order.payload.get("signal_datetime")).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        if order.payload.get("signal_datetime") is not None
                        else pd.Timestamp(order.payload.get("trade_date")).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    ),
                    "fill_phase": (
                        "call_auction"
                        if int(bar["bar_index"])
                        in session_open_indices.get(symbol, set())
                        else "continuous"
                    ),
                }
            )

        # 3) 逐 bar 估值
        for symbol, bar in day_bars.items():
            last_prices[symbol] = float(bar["close"])
        holdings, position_value, equity = _equity_holdings(
            positions, day_bars, last_prices, cash
        )
        if trade_start_ts is None or session_date >= trade_start_ts:
            ts_text = ts.strftime("%Y-%m-%d %H:%M:%S")
            equity_curve.append({"datetime": ts_text, "equity": round(equity, 10)})
            if cfg.snapshot_every_bar or ts == session_last_time.get(session_date):
                snapshots.append(
                    {
                        "snapshot_time": ts_text,
                        "equity": round(equity, 10),
                        "cash": round(cash, 10),
                        "position_value": round(position_value, 10),
                        "holdings": holdings,
                    }
                )

    # 收盘后仍未成交的订单（当日最后一根 bar 之后没有 bar 可再尝试）
    for order in carry:
        warnings.append(
            f"{order.symbol} {order.expires_session.date()}: 当日未成交"
            "（停牌/涨停），买单作废"
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


def session_eod_curve(equity_curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """分钟净值曲线 → 每交易日收盘一个点。

    绩效指标（年化 / Sharpe / 最大回撤）按**日频**计算：``metrics.calc_summary`` 把每个
    点当交易日处理（sqrt(252)），直接喂分钟点会把分钟 bar 当交易日，Sharpe 被放大
    sqrt(bars_per_day) 倍。分钟曲线本身仍原样返回给调用方。
    """
    if not equity_curve:
        return []
    by_session: dict[str, dict[str, Any]] = {}
    for point in equity_curve:
        text = str(point.get("datetime") or "")
        session = text[:10]
        if not session:
            continue
        by_session[session] = point
    return [by_session[key] for key in sorted(by_session)]
