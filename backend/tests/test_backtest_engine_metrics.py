"""VEW-30 回测绩效指标与净值回归测试。

此前 `metrics.calc_summary` 的 Sharpe / 最大回撤 / 胜率 / 盈亏比 / 换手率均无专测，
年化"按真实日历天数"也只在 engine 层间接覆盖。这里用固定输入做确定性校验：
- 总收益、最大回撤；
- 胜率、盈亏比、换手率、总交易数；
- 年化按真实日历天数而非事件数（同两点的曲线，日期跨度不同，年化不同）；
- 空曲线返回全零；同输入重复计算得到一致结果（确定性）。

净值 vs 基准的独立校验（≤1e-6）已在
`tests/test_portfolio_backtest_engine.py::test_daily_equity_matches_independent_cash_plus_mark_to_market`
覆盖，此处不重复。
"""

from __future__ import annotations

from datetime import datetime

from app.services.backtest.metrics import calc_summary


def _point(value, date_str):
    return {"datetime": f"{date_str}T00:00:00Z", "equity": value}


def _curve(entries):
    return [_point(v, d) for d, v in entries]


def test_total_return_and_max_drawdown():
    equity = _curve(
        [
            ("2026-01-01", 100000),
            ("2026-01-02", 110000),
            ("2026-01-03", 99000),
            ("2026-01-04", 116000),
        ]
    )
    summary = calc_summary(equity, [], 100000)
    # 总收益 = (116000-100000)/100000
    assert summary["total_return"] == 0.16
    # 最大回撤：峰值 110000 → 99_000，跌幅 0.1
    assert summary["max_drawdown"] == 0.1


def test_win_rate_profit_loss_ratio_and_turnover():
    trades = [
        {"side": "sell", "pnl": 500, "amount": 10000},
        {"side": "sell", "pnl": -200, "amount": 8000},
        {"side": "sell", "pnl": 300, "amount": 12000},
    ]
    equity = _curve([("2026-01-01", 100000), ("2026-01-02", 101000)])
    summary = calc_summary(equity, trades, 100000)
    # 胜率 2/3；平均盈利 400 / 平均亏损 200 = 2.0
    assert summary["win_rate"] == round(2 / 3, 6)
    assert summary["profit_loss_ratio"] == 2.0
    # 换手率 = 累计成交额 / 初始资金 = 30000 / 100000
    assert summary["turnover"] == 0.3
    assert summary["total_trades"] == 3


def test_annual_return_uses_calendar_days_not_event_count():
    # 两条曲线都只含 2 个点（1 个事件），但日期跨度不同 → 年化应不同
    equity_1y = _curve([("2026-01-01", 100000), ("2027-01-01", 116000)])
    equity_2y = _curve([("2026-01-01", 100000), ("2028-01-01", 116000)])
    s1y = calc_summary(equity_1y, [], 100000)["annual_return"]
    s2y = calc_summary(equity_2y, [], 100000)["annual_return"]
    # 1 年跨度年化更接近总收益（0.16），2 年跨度年化更低
    assert s1y > 0
    assert s2y > 0
    assert s1y > s2y
    assert s1y < 0.5
    assert s2y < s1y


def test_empty_curve_returns_zeros():
    summary = calc_summary([], [], 100000)
    for key in (
        "total_return",
        "annual_return",
        "max_drawdown",
        "sharpe",
        "win_rate",
        "profit_loss_ratio",
        "turnover",
        "total_trades",
    ):
        assert summary[key] == 0


def test_calc_summary_is_deterministic():
    equity = _curve(
        [
            ("2026-01-01", 100000),
            ("2026-01-02", 110000),
            ("2026-01-03", 99000),
            ("2026-01-04", 116000),
        ]
    )
    trades = [{"side": "sell", "pnl": 400, "amount": 25000}]
    a = calc_summary(equity, trades, 100000)
    b = calc_summary(equity, trades, 100000)
    assert a == b


def test_sharpe_sign_is_consistent_with_returns():
    # 单调上涨曲线 → 日收益为正 → sharpe > 0
    up = _curve(
        [
            ("2026-01-01", 100000),
            ("2026-01-02", 101000),
            ("2026-01-03", 102000),
            ("2026-01-04", 103000),
        ]
    )
    assert calc_summary(up, [], 100000)["sharpe"] > 0

    # 单点曲线无日收益 → sharpe 为 0
    single = _curve([("2026-01-01", 100000)])
    assert calc_summary(single, [], 100000)["sharpe"] == 0
