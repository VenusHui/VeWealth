from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from app.services.backtest.costs import CostModel
from app.services.backtest.engine import (
    SecurityRule,
    is_limit_down,
    is_limit_up,
    price_limit_rate,
    run_portfolio,
)
from app.services.backtest.metrics import calc_summary
from app.services.backtest.service import BacktestService


def _bars(symbol: str, opens: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.bdate_range("2026-01-05", periods=len(opens)),
            "open": opens,
            "close": closes,
            "volume": [1000.0] * len(opens),
            "symbol": [symbol] * len(opens),
        }
    )


class PortfolioBacktestEngineTests(unittest.TestCase):
    def test_signal_fills_at_t_plus_one_open_with_cost_slippage_lot_and_t1(self):
        frame = _bars("000001", [10.0, 10.0, 11.0, 12.0], [10.0, 10.5, 11.5, 12.0])
        orders = pd.DataFrame(
            [
                {
                    "trade_date": frame.iloc[0]["datetime"],
                    "symbol": "000001",
                    "position_size_pct": 1.0,
                    "reason": "fixture",
                }
            ]
        )
        result = run_portfolio(
            {"000001": frame},
            orders,
            10_000.0,
            CostModel(
                commission_rate=0.001,
                min_commission=0.0,
                stamp_tax_rate=0.001,
                slippage_rate=0.01,
            ),
            hold_days=1,
            default_position_size_pct=1.0,
        )

        self.assertEqual([trade["side"] for trade in result.trades], ["buy", "sell"])
        buy, sell = result.trades
        self.assertEqual(buy["signal_datetime"][:10], "2026-01-05")
        self.assertEqual(buy["datetime"][:10], "2026-01-06")
        self.assertEqual(sell["datetime"][:10], "2026-01-07")
        self.assertGreater(sell["datetime"], buy["datetime"])
        self.assertEqual(buy["qty"] % 100, 0)
        self.assertGreater(buy["fee"], 0)
        self.assertGreater(sell["fee"], 0)
        self.assertAlmostEqual(buy["price"], 10.1)
        self.assertAlmostEqual(sell["price"], 10.89)
        self.assertTrue(
            all(snapshot["cash"] >= 0 for snapshot in result.positions_snapshot)
        )

    def test_shared_cash_caps_total_entry_weight(self):
        frames = {
            symbol: _bars(symbol, [10.0, 10.0, 10.0], [10.0, 10.0, 10.0])
            for symbol in ("000001", "600519")
        }
        orders = pd.DataFrame(
            [
                {
                    "trade_date": "2026-01-05",
                    "symbol": symbol,
                    "position_size_pct": 0.6,
                }
                for symbol in frames
            ]
        )
        result = run_portfolio(
            frames,
            orders,
            100_000.0,
            CostModel(0.0, 0.0, 0.0, 0.0),
            max_total_position_pct=0.8,
            default_position_size_pct=0.6,
        )
        entry_snapshot = result.positions_snapshot[1]

        self.assertAlmostEqual(
            sum(h["weight"] for h in entry_snapshot["holdings"]), 0.8
        )
        self.assertGreaterEqual(entry_snapshot["cash"], 0.0)
        self.assertEqual(
            [trade["qty"] for trade in result.trades if trade["side"] == "buy"],
            [6000, 2000],
        )

    def test_daily_equity_matches_independent_cash_plus_mark_to_market(self):
        frame = _bars("000001", [10.0, 10.0, 10.0], [10.0, 11.0, 12.0])
        orders = pd.DataFrame(
            [{"trade_date": "2026-01-05", "symbol": "000001", "position_size_pct": 0.5}]
        )
        result = run_portfolio(
            {"000001": frame},
            orders,
            100_000.0,
            CostModel(0.0, 0.0, 0.0, 0.0),
            hold_days=5,
            default_position_size_pct=0.5,
        )

        self.assertEqual(len(result.equity_curve), 3)
        for point, snapshot in zip(result.equity_curve, result.positions_snapshot):
            independent = snapshot["cash"] + sum(
                holding["qty"] * holding["last_price"]
                for holding in snapshot["holdings"]
            )
            self.assertLessEqual(abs(point["equity"] - independent), 1e-6)

    def test_board_and_st_price_limit_rules(self):
        self.assertEqual(price_limit_rate("600000", date(2026, 1, 1)), 0.10)
        self.assertEqual(price_limit_rate("300001", date(2020, 8, 23)), 0.10)
        self.assertEqual(price_limit_rate("300001", date(2020, 8, 24)), 0.20)
        self.assertEqual(price_limit_rate("688001", date(2026, 1, 1)), 0.20)
        self.assertEqual(price_limit_rate("830001", date(2026, 1, 1)), 0.30)
        st_rule = SecurityRule(board="main", is_st=True)
        self.assertEqual(price_limit_rate("600000", date(2026, 1, 1), st_rule), 0.05)
        self.assertTrue(is_limit_up(10.5, 10.0, "600000", date(2026, 1, 1), st_rule))
        self.assertTrue(is_limit_down(9.5, 10.0, "600000", date(2026, 1, 1), st_rule))

    def test_limit_up_rejects_buy(self):
        frame = _bars("300001", [10.0, 12.0, 12.0], [10.0, 12.0, 12.0])
        orders = pd.DataFrame(
            [{"trade_date": "2026-01-05", "symbol": "300001", "position_size_pct": 1.0}]
        )
        result = run_portfolio(
            {"300001": frame},
            orders,
            10_000.0,
            CostModel(0.0, 0.0, 0.0, 0.0),
            default_position_size_pct=1.0,
        )
        self.assertEqual(result.trades, [])
        self.assertTrue(any("涨停" in warning for warning in result.warnings))

    def test_limit_down_delays_exit_to_next_tradable_open(self):
        frame = _bars(
            "000001",
            [10.0, 10.0, 9.0, 9.1],
            [10.0, 10.0, 9.0, 9.1],
        )
        orders = pd.DataFrame(
            [
                {
                    "trade_date": "2026-01-05",
                    "symbol": "000001",
                    "position_size_pct": 0.5,
                }
            ]
        )
        result = run_portfolio(
            {"000001": frame},
            orders,
            10_000.0,
            CostModel(0.0, 0.0, 0.0, 0.0),
            hold_days=1,
            default_position_size_pct=0.5,
        )
        sell = next(trade for trade in result.trades if trade["side"] == "sell")
        self.assertEqual(sell["datetime"][:10], "2026-01-08")
        self.assertTrue(any("跌停" in warning for warning in result.warnings))


class UnifiedModeTests(unittest.TestCase):
    class _FixtureStrategy:
        @staticmethod
        def required_columns():
            return {"datetime", "open", "close"}

        @staticmethod
        def default_policy_profile():
            return "vsd_v1_default"

        @staticmethod
        def generate_candidates(frame, _params):
            return pd.DataFrame(
                [
                    {
                        "trade_date": frame.iloc[0]["datetime"],
                        "symbol": frame.iloc[0]["symbol"],
                        "signal_strength": 1.0,
                        "reason": "fixture",
                    }
                ]
            )

    @staticmethod
    def _request(mode: str) -> SimpleNamespace:
        params = {
            "top_k_per_day": 2,
            "position_size_pct": 0.4,
            "hold_days": 1,
        }
        return SimpleNamespace(
            strategy_id="fixture",
            strategy_params=params,
            mode=mode,
            symbols=["000001", "600519"],
            pool_symbols=["000001", "600519"],
            universe_type="custom",
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 9),
            initial_cash=100_000.0,
            cost_config=SimpleNamespace(
                model_dump=lambda: {
                    "commission_rate": 0.0003,
                    "min_commission": 5.0,
                    "stamp_tax_rate": 0.001,
                    "slippage_rate": 0.0005,
                }
            ),
        )

    def test_modes_with_same_pool_and_parameters_have_identical_trades(self):
        frames = {
            "000001": _bars("000001", [10.0, 10.1, 10.2], [10.0, 10.1, 10.2]),
            "600519": _bars("600519", [20.0, 20.1, 20.2], [20.0, 20.1, 20.2]),
        }

        def get_daily_data(symbol, **_kwargs):
            return frames[symbol].copy(), None, None

        service = BacktestService()
        with patch(
            "app.services.backtest.service.get_strategy",
            return_value=self._FixtureStrategy(),
        ), patch(
            "app.services.backtest.service.stock_service.get_daily_data",
            side_effect=get_daily_data,
        ):
            manual = service._run_manual_symbols_mode(self._request("manual_symbols"))
            selected = service._run_strategy_select_mode(
                self._request("strategy_select")
            )

        self.assertEqual(manual["trades"], selected["trades"])
        self.assertEqual(manual["equity_curve"], selected["equity_curve"])


class DateAwareMetricsTests(unittest.TestCase):
    def test_annual_return_uses_elapsed_calendar_dates_not_event_count(self):
        curve = [
            {"datetime": "2020-01-01 00:00:00", "equity": 100.0},
            {"datetime": "2021-01-01 00:00:00", "equity": 110.0},
        ]
        result = calc_summary(curve, [], 100.0)
        expected = 1.1 ** (365.2425 / 366) - 1
        self.assertAlmostEqual(result["annual_return"], expected, places=6)


if __name__ == "__main__":
    unittest.main()
