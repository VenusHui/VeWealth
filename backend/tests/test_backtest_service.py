"""测试回测服务核心逻辑"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

import pandas as pd

from app.services.backtest.service import BacktestService
from app.services.backtest.costs import CostModel
from app.services.backtest.engine import SymbolRunResult


class BacktestServiceUnitTests(unittest.TestCase):
    def setUp(self):
        self.service = BacktestService()

    # _normalize_symbol_code tests
    def test_normalize_symbol_code_extracts_six_digits(self):
        self.assertEqual(self.service._normalize_symbol_code("000001.XSHE"), "000001")
        self.assertEqual(self.service._normalize_symbol_code("600519.XSHG"), "600519")
        self.assertEqual(self.service._normalize_symbol_code("000001"), "000001")

    def test_normalize_symbol_code_empty(self):
        self.assertEqual(self.service._normalize_symbol_code(""), "")
        self.assertEqual(self.service._normalize_symbol_code(None), "")

    def test_normalize_symbol_code_short(self):
        self.assertEqual(self.service._normalize_symbol_code("12345"), "")

    # _extract_trade_date tests
    def test_extract_trade_date_valid(self):
        self.assertEqual(
            self.service._extract_trade_date("2026-01-15 00:00:00"), "2026-01-15"
        )
        self.assertEqual(self.service._extract_trade_date("2026-01-15"), "2026-01-15")

    def test_extract_trade_date_empty(self):
        self.assertIsNone(self.service._extract_trade_date(""))
        self.assertIsNone(self.service._extract_trade_date(None))

    def test_extract_trade_date_short(self):
        self.assertIsNone(self.service._extract_trade_date("2026-01"))

    # _parse_trade_datetime tests
    def test_parse_trade_datetime_full(self):
        result = self.service._parse_trade_datetime("2026-01-15 09:30:00")
        self.assertIsInstance(result, datetime)
        self.assertEqual(result.year, 2026)

    def test_parse_trade_datetime_date_only(self):
        result = self.service._parse_trade_datetime("2026-01-15")
        self.assertIsInstance(result, datetime)

    def test_parse_trade_datetime_iso(self):
        result = self.service._parse_trade_datetime("2026-01-15T09:30:00")
        self.assertIsInstance(result, datetime)

    def test_parse_trade_datetime_already_datetime(self):
        dt = datetime(2026, 1, 15, 9, 30)
        result = self.service._parse_trade_datetime(dt)
        self.assertIs(result, dt)

    def test_parse_trade_datetime_none(self):
        self.assertIsNone(self.service._parse_trade_datetime(None))

    # _build_round_trips tests
    def test_build_round_trips_empty(self):
        self.assertEqual(self.service._build_round_trips([]), [])

    def test_build_round_trips_single_pair(self):
        trades = [
            {
                "symbol": "000001",
                "datetime": "2026-01-05 09:30:00",
                "side": "buy",
                "price": 10.0,
                "qty": 100,
                "amount": 1000.0,
                "reason": "测试买入",
            },
            {
                "symbol": "000001",
                "datetime": "2026-01-10 09:30:00",
                "side": "sell",
                "price": 11.0,
                "qty": 100,
                "amount": 1100.0,
                "reason": "测试卖出",
            },
        ]
        rounds = self.service._build_round_trips(trades)
        self.assertEqual(len(rounds), 1)
        r = rounds[0]
        self.assertEqual(r["symbol"], "000001")
        self.assertEqual(r["open_price"], 10.0)
        self.assertEqual(r["close_price"], 11.0)
        self.assertEqual(r["qty"], 100)
        self.assertAlmostEqual(r["pnl_amount"], 100.0, places=1)
        self.assertGreater(r["pnl_ratio"], 0)

    def test_build_round_trips_multiple_lots(self):
        trades = [
            {
                "symbol": "000001",
                "datetime": "2026-01-05 09:30:00",
                "side": "buy",
                "price": 10.0,
                "qty": 200,
                "amount": 2000.0,
                "reason": "买入",
            },
            {
                "symbol": "000001",
                "datetime": "2026-01-08 09:30:00",
                "side": "sell",
                "price": 11.0,
                "qty": 100,
                "amount": 1100.0,
                "reason": "卖出一半",
            },
            {
                "symbol": "000001",
                "datetime": "2026-01-10 09:30:00",
                "side": "sell",
                "price": 12.0,
                "qty": 100,
                "amount": 1200.0,
                "reason": "卖出剩余",
            },
        ]
        rounds = self.service._build_round_trips(trades)
        self.assertEqual(len(rounds), 2)
        self.assertAlmostEqual(rounds[0]["pnl_amount"], 100.0, places=1)
        self.assertAlmostEqual(rounds[1]["pnl_amount"], 200.0, places=1)

    def test_build_round_trips_ignores_non_buy_sell(self):
        trades = [
            {
                "symbol": "000001",
                "datetime": "2026-01-05 09:30:00",
                "side": "hold",
                "price": 10.0,
                "qty": 100,
                "amount": 1000.0,
                "reason": "持有",
            },
        ]
        rounds = self.service._build_round_trips(trades)
        self.assertEqual(len(rounds), 0)

    def test_build_round_trips_skip_no_symbol(self):
        trades = [
            {
                "symbol": "",
                "datetime": "2026-01-05 09:30:00",
                "side": "buy",
                "price": 10.0,
                "qty": 100,
                "amount": 1000.0,
                "reason": "无标的",
            },
        ]
        rounds = self.service._build_round_trips(trades)
        self.assertEqual(len(rounds), 0)

    # _merge_symbol_curves tests
    def test_merge_symbol_curves_empty(self):
        self.assertEqual(self.service._merge_symbol_curves({}), [])

    def test_merge_symbol_curves_single(self):
        curves = {
            "000001": [
                {"datetime": "2026-01-05 00:00:00", "equity": 100000.0},
                {"datetime": "2026-01-06 00:00:00", "equity": 101000.0},
            ]
        }
        result = self.service._merge_symbol_curves(curves)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["equity"], 100000.0)

    def test_merge_symbol_curves_two_symbols(self):
        curves = {
            "000001": [
                {"datetime": "2026-01-05 00:00:00", "equity": 100000.0},
                {"datetime": "2026-01-06 00:00:00", "equity": 101000.0},
            ],
            "600519": [
                {"datetime": "2026-01-05 00:00:00", "equity": 50000.0},
                {"datetime": "2026-01-06 00:00:00", "equity": 51000.0},
            ],
        }
        result = self.service._merge_symbol_curves(curves)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["equity"], 150000.0)
        self.assertAlmostEqual(result[1]["equity"], 152000.0)

    # _merge_position_snapshots tests
    def test_merge_position_snapshots_empty(self):
        self.assertEqual(self.service._merge_position_snapshots({}), [])

    def test_merge_position_snapshots_basic(self):
        curves = {
            "000001": [
                {
                    "datetime": "2026-01-05 00:00:00",
                    "shares": 100,
                    "close": 10.0,
                    "market_value": 1000.0,
                    "cash": 99000.0,
                    "equity": 100000.0,
                }
            ]
        }
        result = self.service._merge_position_snapshots(curves)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["equity"], 100000.0)
        self.assertEqual(len(result[0]["holdings"]), 1)

    # _normalize_curve_to_base_one tests
    def test_normalize_curve_to_base_one(self):
        values = {"2026-01-01": 100.0, "2026-01-02": 110.0}
        base_dates = ["2026-01-01", "2026-01-02"]
        result = self.service._normalize_curve_to_base_one(values, base_dates)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["value_norm"], 1.0)
        self.assertAlmostEqual(result[1]["value_norm"], 1.1)

    def test_normalize_curve_to_base_one_with_gaps(self):
        values = {"2026-01-01": 100.0}
        base_dates = ["2026-01-01", "2026-01-02"]
        result = self.service._normalize_curve_to_base_one(values, base_dates)
        self.assertEqual(len(result), 1)

    # run_backtest validation tests
    def test_run_backtest_rejects_inverted_dates(self):
        request = SimpleNamespace(
            name="test",
            strategy_id="ma_cross_v1",
            strategy_params={},
            mode="manual_symbols",
            symbols=["000001"],
            start_date=date(2026, 2, 1),
            end_date=date(2026, 1, 1),
            initial_cash=100000,
            cost_config=CostModel(),
            universe_type="all",
            pool_symbols=[],
            benchmark=None,
        )
        with self.assertRaises(ValueError) as ctx:
            self.service.run_backtest(request, MagicMock(), MagicMock())
        self.assertIn("start_date", str(ctx.exception))

    def test_run_backtest_rejects_empty_symbols_in_manual_mode(self):
        request = SimpleNamespace(
            name="test",
            strategy_id="ma_cross_v1",
            strategy_params={},
            mode="manual_symbols",
            symbols=[],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            initial_cash=100000,
            cost_config=CostModel(),
            universe_type="all",
            pool_symbols=[],
            benchmark=None,
        )
        with self.assertRaises(ValueError) as ctx:
            self.service._run_manual_symbols_mode(request)
        self.assertIn("symbols 不能为空", str(ctx.exception))

    # _run_manual_symbols_mode tests
    @patch("app.services.backtest.service.stock_service")
    @patch("app.services.backtest.service.run_for_symbol")
    def test_run_manual_symbols_mode_success(self, mock_run, mock_stock_svc):
        mock_stock_svc.get_daily_data.return_value = (
            pd.DataFrame(
                {
                    "datetime": pd.date_range("2026-01-01", periods=5, freq="D"),
                    "open": [10.0] * 5,
                    "close": [10.0] * 5,
                }
            ),
            None,
            None,
        )

        mock_run.return_value = SymbolRunResult(
            symbol="000001",
            equity_curve=[
                {"datetime": "2026-01-01 00:00:00", "equity": 100000.0}
            ],
            position_curve=[
                {
                    "datetime": "2026-01-01 00:00:00",
                    "shares": 0,
                    "close": 10.0,
                    "market_value": 0.0,
                    "cash": 100000.0,
                    "equity": 100000.0,
                }
            ],
            trades=[],
            warnings=[],
            last_equity=100000.0,
            final_position=0,
        )

        # Create a mock cost_config with model_dump() to simulate a Pydantic model
        mock_cost_config = MagicMock()
        mock_cost_config.model_dump.return_value = {
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.001,
            "slippage_rate": 0.0005,
        }

        request = SimpleNamespace(
            name="test",
            strategy_id="ma_cross_v1",
            strategy_params={},
            mode="manual_symbols",
            symbols=["000001"],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            initial_cash=100000,
            cost_config=mock_cost_config,
        )

        result = self.service._run_manual_symbols_mode(request)
        self.assertIn("summary", result)
        self.assertIn("equity_curve", result)
        self.assertIn("trades", result)
        self.assertIn("symbols", result)
        self.assertEqual(result["symbols"], ["000001"])


class CostModelTests(unittest.TestCase):
    def test_default_cost_model(self):
        model = CostModel()
        self.assertAlmostEqual(model.commission_rate, 0.0003)
        self.assertAlmostEqual(model.min_commission, 5.0)
        self.assertAlmostEqual(model.stamp_tax_rate, 0.001)
        self.assertAlmostEqual(model.slippage_rate, 0.0005)

    def test_buy_cost(self):
        model = CostModel(commission_rate=0.0003, min_commission=5.0)
        cost = model.buy_cost(10000.0)
        expected = max(10000 * 0.0003, 5.0)
        self.assertAlmostEqual(cost, expected)

    def test_sell_cost_with_stamp_tax(self):
        model = CostModel(
            commission_rate=0.0003, min_commission=5.0, stamp_tax_rate=0.001
        )
        cost = model.sell_cost(10000.0)
        expected_commission = max(10000 * 0.0003, 5.0)
        expected_stamp = 10000 * 0.001
        self.assertAlmostEqual(cost, expected_commission + expected_stamp)

    def test_apply_buy_slippage(self):
        model = CostModel(slippage_rate=0.0005)
        price = model.apply_buy_slippage(10.0)
        self.assertAlmostEqual(price, 10.0 * (1 + 0.0005))

    def test_apply_sell_slippage(self):
        model = CostModel(slippage_rate=0.0005)
        price = model.apply_sell_slippage(10.0)
        self.assertAlmostEqual(price, 10.0 * (1 - 0.0005))


if __name__ == "__main__":
    unittest.main()
