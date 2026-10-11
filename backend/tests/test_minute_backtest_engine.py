"""VEW-66 分钟级撮合引擎：集合竞价 / 涨跌停 / 停牌 / T+1 / 止损止盈。

引擎与日线 ``run_portfolio`` 同构（同一份 orders 契约、同一个 ``PortfolioRunResult``），
所以这里重点锁定**分钟粒度才有的**语义，以及它与日线契约保持一致的部分：

- 日线信号（``trade_date``，收盘才可知）落到**下一交易日开盘集合竞价**成交；
  分钟信号（``signal_datetime``）落到**下一根 bar**成交；
- 涨跌停以「前一交易日收盘价」为基准，当日后续 bar 继续尝试，全天未成交则作废；
- 停牌（无 bar，或整日零成交）当日不可成交，持仓顺延、买单作废，且必须告警；
- T+1 当日买入当日不可卖出；
- 止损/止盈按 bar 的 low/high 触发（分钟粒度新增的退出语义）；
- 净值曲线是分钟粒度，持仓快照默认按交易日收盘。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.services.backtest.costs import CostModel
from app.services.backtest.engine import SecurityRule
from app.services.backtest.minute.engine import (
    MinuteExecutionConfig,
    run_minute_portfolio,
    session_eod_curve,
)
from app.services.backtest.minute.session import session_bar_times

_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]
_FIELDS = {"open": 1, "high": 2, "low": 3, "close": 4, "volume": 5}


def _flat_day(day: str, price: float, volume: float = 1000.0) -> list[list]:
    """一整个交易日的平价 1 分钟 bar。"""
    return [
        [f"{day} {t.strftime('%H:%M')}:00", price, price, price, price, volume]
        for t in session_bar_times("1")
    ]


def _patch(rows: list[list], day: str, hhmm: str, **cols) -> None:
    """按收盘时刻改写某一根 bar 的字段。"""
    stamp = f"{day} {hhmm}:00"
    for row in rows:
        if row[0] == stamp:
            for key, value in cols.items():
                row[_FIELDS[key]] = value
            return
    raise AssertionError(f"未找到 bar {stamp}")


def _frame(rows: list[list]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=_COLUMNS)
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    return frame


def _orders(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _buys(result):
    return [t for t in result.trades if t["side"] == "buy"]


def _sells(result):
    return [t for t in result.trades if t["side"] == "sell"]


def test_daily_signal_fills_at_next_session_open_auction():
    rows = _flat_day("2026-01-05", 10.0) + _flat_day("2026-01-06", 10.0)
    _patch(rows, "2026-01-06", "09:31", open=10.2, high=10.2, low=10.2, close=10.2)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05", "reason": "sig"}]),
        100000.0,
        CostModel(),
    )
    buys = _buys(result)
    assert len(buys) == 1
    # 日线信号在当日收盘才可知 → 最早下一交易日第一根 bar 成交
    assert buys[0]["datetime"] == "2026-01-06 09:31:00"
    assert buys[0]["fill_phase"] == "call_auction"
    assert buys[0]["signal_datetime"] == "2026-01-05 00:00:00"
    # 成交价 = 开盘价 + 买滑点（未触涨停上限）
    assert buys[0]["price"] == pytest.approx(10.2 * 1.0005, rel=1e-9)
    amount = buys[0]["qty"] * buys[0]["price"]
    assert buys[0]["fee"] == pytest.approx(max(amount * 0.0003, 5.0), rel=1e-9)


def test_intraday_signal_fills_on_next_minute_bar():
    rows = _flat_day("2026-01-06", 10.0)
    _patch(rows, "2026-01-06", "10:31", open=10.5, high=10.5, low=10.5, close=10.5)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "signal_datetime": "2026-01-06 10:30:00"}]),
        100000.0,
        CostModel(),
    )
    buys = _buys(result)
    assert len(buys) == 1
    assert buys[0]["datetime"] == "2026-01-06 10:31:00"
    assert buys[0]["fill_phase"] == "continuous"
    assert buys[0]["price"] == pytest.approx(10.5 * 1.0005, rel=1e-9)


def test_limit_up_defers_fill_to_later_bar_same_day():
    rows = _flat_day("2026-01-05", 10.0) + _flat_day("2026-01-06", 10.0)
    # 01-06 开盘起连续涨停（10.0 * 1.1 = 11.00），10:00 才打开
    for minute in range(31, 60):
        _patch(
            rows,
            "2026-01-06",
            f"09:{minute:02d}",
            open=11.0,
            high=11.0,
            low=11.0,
            close=11.0,
        )
    _patch(rows, "2026-01-06", "10:00", open=10.8, high=10.8, low=10.8, close=10.8)

    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
    )
    buys = _buys(result)
    assert len(buys) == 1
    assert buys[0]["datetime"] == "2026-01-06 10:00:00"
    assert any("涨停" in w for w in result.warnings)


def test_limit_up_all_day_expires_order_with_warning():
    rows = _flat_day("2026-01-05", 10.0) + _flat_day("2026-01-06", 11.0)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
    )
    assert _buys(result) == []
    assert any("作废" in w for w in result.warnings)


def test_limit_up_defer_can_be_disabled():
    rows = _flat_day("2026-01-05", 10.0) + _flat_day("2026-01-06", 11.0)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
        config=MinuteExecutionConfig(defer_to_next_bar=False),
    )
    assert _buys(result) == []
    assert any("涨停" in w and "未成交" in w for w in result.warnings)


def test_limit_down_defers_sell_to_next_bar():
    rows = (
        _flat_day("2026-01-05", 10.0)
        + _flat_day("2026-01-06", 10.0)
        + _flat_day("2026-01-07", 10.0)
    )
    # 01-07 跌停（10.0 * 0.9 = 9.00）开盘，09:32 打开
    _patch(rows, "2026-01-07", "09:31", open=9.0, high=9.0, low=9.0, close=9.0)

    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
        config=MinuteExecutionConfig(hold_days=1),
    )
    sells = _sells(result)
    assert len(sells) == 1
    # hold_days=1 → 01-06 建仓，01-07 开盘到期；遇跌停顺延到 09:32
    assert sells[0]["datetime"] == "2026-01-07 09:32:00"
    assert sells[0]["price"] == pytest.approx(10.0 * 0.9995, rel=1e-9)
    assert any("跌停" in w for w in result.warnings)


def test_halted_symbol_defers_sell_and_warns_once_per_session():
    # 000001 缺 01-07 整日行情；000002 提供该交易日的会话，使停牌日仍被遍历到
    rows_a = (
        _flat_day("2026-01-05", 10.0)
        + _flat_day("2026-01-06", 10.0)
        + _flat_day("2026-01-08", 10.0)
    )
    rows_b = (
        _flat_day("2026-01-05", 20.0)
        + _flat_day("2026-01-06", 20.0)
        + _flat_day("2026-01-07", 20.0)
        + _flat_day("2026-01-08", 20.0)
    )
    result = run_minute_portfolio(
        {"000001": _frame(rows_a), "000002": _frame(rows_b)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
        config=MinuteExecutionConfig(hold_days=1),
    )
    sells = _sells(result)
    assert len(sells) == 1
    assert sells[0]["datetime"] == "2026-01-08 09:31:00"
    halt_warnings = [w for w in result.warnings if "停牌" in w]
    # 每交易日只告警一次，不是每根 bar 一次
    assert len(halt_warnings) == 1
    assert "2026-01-07" in halt_warnings[0]


def test_zero_volume_day_counts_as_halted():
    rows = (
        _flat_day("2026-01-05", 10.0)
        + _flat_day("2026-01-06", 10.0)
        + _flat_day("2026-01-07", 10.0, volume=0.0)
        + _flat_day("2026-01-08", 10.0)
    )
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
        config=MinuteExecutionConfig(hold_days=1),
    )
    sells = _sells(result)
    assert len(sells) == 1
    assert sells[0]["datetime"] == "2026-01-08 09:31:00"
    assert any("停牌" in w for w in result.warnings)


def test_t_plus_one_blocks_same_day_sell():
    rows = _flat_day("2026-01-06", 10.0) + _flat_day("2026-01-07", 10.0)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "signal_datetime": "2026-01-06 10:00:00"}]),
        100000.0,
        CostModel(),
        config=MinuteExecutionConfig(hold_days=1),
    )
    buys = _buys(result)
    assert len(buys) == 1
    assert buys[0]["datetime"] == "2026-01-06 10:01:00"
    sells = _sells(result)
    assert len(sells) == 1
    assert sells[0]["datetime"] == "2026-01-07 09:31:00"


def test_stop_loss_triggers_intraday_after_t_plus_one():
    rows = (
        _flat_day("2026-01-05", 10.0)
        + _flat_day("2026-01-06", 10.0)
        + _flat_day("2026-01-07", 10.0)
    )
    # 01-07 10:00 那根 bar 下探到 9.50，跌破 -3% 止损位
    _patch(rows, "2026-01-07", "10:00", open=9.8, high=9.8, low=9.5, close=9.6)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
        # hold_days 放大，确保到期卖出不会先于止损触发
        config=MinuteExecutionConfig(hold_days=10, stop_loss_pct=0.03),
    )
    buys = _buys(result)
    sells = _sells(result)
    assert len(buys) == 1 and len(sells) == 1
    entry_price = buys[0]["price"]
    expected = min(9.8, entry_price * 0.97) * 0.9995
    assert sells[0]["datetime"] == "2026-01-07 10:00:00"
    assert sells[0]["reason"] == "stop_loss"
    # 成交价按既有契约四舍五入到 6 位小数，故用 1e-6 容差比对
    assert sells[0]["price"] == pytest.approx(expected, abs=1e-6)


def test_take_profit_triggers_intraday():
    rows = (
        _flat_day("2026-01-05", 10.0)
        + _flat_day("2026-01-06", 10.0)
        + _flat_day("2026-01-07", 10.0)
    )
    _patch(rows, "2026-01-07", "11:00", open=10.4, high=10.8, low=10.4, close=10.7)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
        config=MinuteExecutionConfig(hold_days=10, take_profit_pct=0.05),
    )
    sells = _sells(result)
    assert len(sells) == 1
    entry_price = _buys(result)[0]["price"]
    assert sells[0]["datetime"] == "2026-01-07 11:00:00"
    assert sells[0]["reason"] == "take_profit"
    assert sells[0]["price"] == pytest.approx(
        max(10.4, entry_price * 1.05) * 0.9995, abs=1e-6
    )


def test_security_rule_makes_st_stock_limit_5_percent():
    rows = _flat_day("2026-01-05", 10.0) + _flat_day("2026-01-06", 10.5)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
        security_rules={"000001": SecurityRule(board="main", is_st=True)},
    )
    # ST 涨跌停 5%：10.0 * 1.05 = 10.50，全天一字涨停 → 买不进
    assert _buys(result) == []
    assert any("涨停" in w for w in result.warnings)


def test_equity_curve_is_minute_resolution_and_snapshots_are_eod():
    rows = _flat_day("2026-01-05", 10.0) + _flat_day("2026-01-06", 10.0)
    result = run_minute_portfolio({"000001": _frame(rows)}, None, 100000.0, CostModel())
    assert len(result.equity_curve) == 480
    assert result.equity_curve[0]["datetime"] == "2026-01-05 09:31:00"
    assert result.equity_curve[-1]["datetime"] == "2026-01-06 15:00:00"
    assert len(result.positions_snapshot) == 2
    assert result.positions_snapshot[0]["snapshot_time"] == "2026-01-05 15:00:00"


def test_snapshot_every_bar_option():
    rows = _flat_day("2026-01-05", 10.0)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        None,
        100000.0,
        CostModel(),
        config=MinuteExecutionConfig(snapshot_every_bar=True),
    )
    assert len(result.positions_snapshot) == len(result.equity_curve) == 240


def test_trade_start_gates_warmup_bars_and_orders():
    rows = _flat_day("2026-01-05", 10.0) + _flat_day("2026-01-06", 10.0)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        100000.0,
        CostModel(),
        trade_start="2026-01-06",
    )
    # warmup 交易日（01-05）不写净值，但它的信号在 01-06 开盘成交（首日交易不丢）
    assert result.equity_curve[0]["datetime"] == "2026-01-06 09:31:00"
    assert len(_buys(result)) == 1


def test_orders_without_signal_time_are_skipped_with_warning():
    rows = _flat_day("2026-01-06", 10.0)
    result = run_minute_portfolio(
        {"000001": _frame(rows)},
        _orders([{"symbol": "000001"}]),
        100000.0,
        CostModel(),
    )
    assert _buys(result) == []
    assert any("缺少 signal_datetime" in w for w in result.warnings)


def test_initial_cash_must_be_positive():
    rows = _flat_day("2026-01-06", 10.0)
    with pytest.raises(ValueError):
        run_minute_portfolio({"000001": _frame(rows)}, None, 0.0, CostModel())


def test_session_eod_curve_keeps_last_point_per_session():
    curve = [
        {"datetime": "2026-01-05 09:31:00", "equity": 100.0},
        {"datetime": "2026-01-05 15:00:00", "equity": 110.0},
        {"datetime": "2026-01-06 09:31:00", "equity": 120.0},
        {"datetime": "2026-01-06 15:00:00", "equity": 130.0},
    ]
    eod = session_eod_curve(curve)
    assert [point["equity"] for point in eod] == [110.0, 130.0]
    assert [point["datetime"][:10] for point in eod] == ["2026-01-05", "2026-01-06"]
    assert session_eod_curve([]) == []


def test_session_dates_are_ordered_and_deduped():
    from app.services.backtest.minute.session import session_dates

    rows = _flat_day("2026-01-06", 10.0) + _flat_day("2026-01-05", 10.0)
    dates = session_dates({"000001": _frame(rows)})
    assert dates == [date(2026, 1, 5), date(2026, 1, 6)]
