"""VEW-30 性能回归测试（CI 质量门禁）。

用于守住计算核心不出现病态放大（如退化到 O(n^2) / 无界循环）。采用**宽松**的
墙钟上限，正常路径远低于上限（实测 300 symbols 引擎 ~0.7s），因此不会在 CI 上
抖动误报；一旦发生量级放大就会顶破上限标红。

同时做正确性断言，确保性能优化不破坏语义（收益率/交易数/现金非负）。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd
import pytest

from app.services.backtest.costs import CostModel
from app.services.backtest.engine import run_portfolio
from app.services.backtest.metrics import calc_summary

ENGINE_BOUND_SECONDS = 5.0
METRICS_BOUND_SECONDS = 2.0
N_SYMBOLS = 300
N_BARS = 40


def _market(n_symbols, n_bars):
    dates = pd.bdate_range("2025-01-06", periods=n_bars)
    return {
        f"{i:06d}": pd.DataFrame(
            {
                "datetime": dates,
                "open": [10.0 + i * 0.01] * n_bars,
                "close": [10.1 + i * 0.01] * n_bars,
                "volume": [1000.0] * n_bars,
                "symbol": [f"{i:06d}"] * n_bars,
            }
        )
        for i in range(n_symbols)
    }


@pytest.mark.perf
def test_engine_scales_in_bounded_time_with_correct_results():
    market = _market(N_SYMBOLS, N_BARS)
    orders = pd.DataFrame(
        [
            {
                "trade_date": market[s]["datetime"].iloc[0],
                "symbol": s,
                "position_size_pct": 0.1,
                "reason": "perf-fixture",
            }
            for s in market
        ]
    )
    start = time.perf_counter()
    result = run_portfolio(
        market,
        orders,
        1_000_000.0,
        CostModel(
            commission_rate=0.001,
            min_commission=0.0,
            stamp_tax_rate=0.001,
            slippage_rate=0.01,
        ),
        hold_days=5,
        default_position_size_pct=0.1,
    )
    elapsed = time.perf_counter() - start
    assert elapsed < ENGINE_BOUND_SECONDS, f"engine too slow: {elapsed:.3f}s"
    # 正确性：确实产生成交且净值曲线各点非负
    assert len(result.trades) > 0
    assert len(result.equity_curve) > 0
    assert all(point["equity"] >= 0 for point in result.equity_curve)


@pytest.mark.perf
def test_calc_summary_bounded_for_long_curve():
    # 5000 个净值点的长曲线，指标计算应线性、无放大
    base = datetime(2025, 1, 1)
    curve = [
        {
            "datetime": (base + timedelta(seconds=i)).isoformat() + "Z",
            "equity": 100000 + i,
        }
        for i in range(5000)
    ]
    start = time.perf_counter()
    summary = calc_summary(curve, [], 100000)
    elapsed = time.perf_counter() - start
    assert elapsed < METRICS_BOUND_SECONDS, f"metrics too slow: {elapsed:.3f}s"
    assert summary["total_trades"] == 0
    assert summary["total_return"] > 0
