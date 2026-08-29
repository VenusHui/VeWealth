"""测试回测执行引擎的容错逻辑"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from app.services.backtest.costs import CostModel
from app.services.backtest.engine import run_for_symbol


class _BuyFirstStrategy:
    """固定信号的假策略：首行触发买入，其余无信号。"""

    def generate_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        out = df.copy()
        out["buy_signal"] = False
        out["sell_signal"] = False
        out.at[0, "buy_signal"] = True
        return out


class BacktestEngineFaultToleranceTests(unittest.TestCase):
    def test_zero_open_price_on_buy_execution_is_skipped(self):
        """次日开盘价为 0 时买入应被跳过并告警，而不是除零崩溃。"""
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=3, freq="D"),
                "open": [10.0, 0.0, 11.0],
                "close": [10.0, 10.0, 11.0],
                "high": [10.5, 10.5, 11.5],
                "low": [9.5, 9.5, 10.5],
                "volume": [1000.0, 1000.0, 1000.0],
            }
        )

        with patch(
            "app.services.backtest.engine.get_strategy",
            return_value=_BuyFirstStrategy(),
        ):
            result = run_for_symbol(
                symbol="000001",
                df=df,
                strategy_id="fake",
                strategy_params={},
                init_cash=100000.0,
                cost_model=CostModel(),
            )

        self.assertEqual(result.trades, [])
        self.assertTrue(
            any("开盘价异常" in w for w in result.warnings),
            msg=f"expected zero-open warning, got {result.warnings}",
        )
        # 未成交，持仓与资金不变
        self.assertEqual(result.final_position, 0)
        self.assertEqual(result.last_equity, 100000.0)

    def test_buy_signal_with_insufficient_cash_warns(self):
        """开盘价过高导致现金不足时应跳过并告警。"""
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=3, freq="D"),
                "open": [1000.0, 1000.0, 1000.0],
                "close": [1000.0, 1000.0, 1000.0],
                "high": [1000.5, 1000.5, 1000.5],
                "low": [999.5, 999.5, 999.5],
                "volume": [1000.0, 1000.0, 1000.0],
            }
        )

        with patch(
            "app.services.backtest.engine.get_strategy",
            return_value=_BuyFirstStrategy(),
        ):
            result = run_for_symbol(
                symbol="000001",
                df=df,
                strategy_id="fake",
                strategy_params={},
                init_cash=100000.0,
                cost_model=CostModel(),
            )

        self.assertEqual(result.trades, [])
        self.assertTrue(
            any("现金不足" in w for w in result.warnings),
            msg=f"expected insufficient-cash warning, got {result.warnings}",
        )
        self.assertEqual(result.final_position, 0)

    def test_normal_buy_execution_still_works(self):
        """容错逻辑不应破坏正常的买入路径。"""
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=3, freq="D"),
                "open": [10.0, 10.0, 11.0],
                "close": [10.0, 10.0, 11.0],
                "high": [10.5, 10.5, 11.5],
                "low": [9.5, 9.5, 10.5],
                "volume": [1000.0, 1000.0, 1000.0],
            }
        )

        with patch(
            "app.services.backtest.engine.get_strategy",
            return_value=_BuyFirstStrategy(),
        ):
            result = run_for_symbol(
                symbol="000001",
                df=df,
                strategy_id="fake",
                strategy_params={},
                init_cash=100000.0,
                cost_model=CostModel(),
            )

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0]["side"], "buy")
        self.assertGreater(result.final_position, 0)
        self.assertFalse(any("开盘价异常" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
