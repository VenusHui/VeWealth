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

    @staticmethod
    def _make_request(**overrides) -> SimpleNamespace:
        defaults = dict(
            name="test",
            strategy_id="ma_cross_v1",
            strategy_params={},
            mode="manual_symbols",
            symbols=["000001"],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            initial_cash=100000,
            cost_config=CostModel(),
            universe_type="all",
            pool_symbols=[],
            benchmark=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    # run_backtest validation tests
    def test_run_backtest_rejects_inverted_dates(self):
        request = self._make_request(
            start_date=date(2026, 2, 1), end_date=date(2026, 1, 1)
        )
        with self.assertRaises(ValueError) as ctx:
            self.service.run_backtest(request, MagicMock(), MagicMock())
        self.assertIn("start_date", str(ctx.exception))

    def test_run_backtest_rejects_empty_symbols_in_manual_mode(self):
        request = self._make_request(symbols=[])
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
            equity_curve=[{"datetime": "2026-01-01 00:00:00", "equity": 100000.0}],
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

        request = self._make_request(cost_config=mock_cost_config)

        result = self.service._run_manual_symbols_mode(request)
        self.assertIn("summary", result)
        self.assertIn("equity_curve", result)
        self.assertIn("trades", result)
        self.assertIn("symbols", result)
        self.assertEqual(result["symbols"], ["000001"])

    # run_backtest diagnostics persistence tests
    def test_run_backtest_persists_scan_diagnostics_in_summary(self):
        diagnostics = {
            "universe_size": 5000,
            "data_available_count": 4800,
            "data_empty_count": 200,
            "candidate_count": 50,
            "selected_count": 10,
            "event_count": 8,
            "policy_profile": "default",
            "effective_universe_filter": {"boards": ["main"], "exclude_st": True},
        }
        mode_result = {
            "summary": {
                "total_return": 0.1,
                "annual_return": 0.2,
                "max_drawdown": -0.05,
                "sharpe": 1.5,
                "win_rate": 0.6,
                "profit_loss_ratio": 1.5,
                "turnover": 1.0,
                "total_trades": 8,
            },
            "equity_curve": [],
            "trades": [],
            "warnings": [
                "strategy_select 扫描股票数: 5000，有效行情股票数: 4800，候选数: 50，执行事件数: 8"
            ],
            "positions_snapshot": [],
            "symbols": ["000001", "600519"],
            "diagnostics": diagnostics,
        }
        mock_cost_config = MagicMock()
        mock_cost_config.model_dump.return_value = {
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.001,
            "slippage_rate": 0.0005,
        }
        request = self._make_request(
            mode="strategy_select", cost_config=mock_cost_config
        )
        db = MagicMock()
        db.refresh.side_effect = lambda obj: obj

        with patch.object(
            BacktestService,
            "_run_strategy_select_mode",
            return_value=mode_result,
        ):
            self.service.run_backtest(request, MagicMock(), db)

        run = db.add.call_args[0][0]
        self.assertEqual(run.summary["diagnostics"], diagnostics)
        self.assertEqual(run.summary["mode"], "strategy_select")

    def test_run_backtest_manual_mode_marks_mode_without_diagnostics(self):
        mode_result = {
            "summary": {
                "total_return": 0.0,
                "annual_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "win_rate": 0.0,
                "profit_loss_ratio": 0.0,
                "turnover": 0.0,
                "total_trades": 0,
            },
            "equity_curve": [],
            "trades": [],
            "warnings": [],
            "positions_snapshot": [],
            "symbols": ["000001"],
        }
        mock_cost_config = MagicMock()
        mock_cost_config.model_dump.return_value = {
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.001,
            "slippage_rate": 0.0005,
        }
        request = self._make_request(
            mode="manual_symbols", cost_config=mock_cost_config
        )
        db = MagicMock()
        db.refresh.side_effect = lambda obj: obj

        with patch.object(
            BacktestService,
            "_run_manual_symbols_mode",
            return_value=mode_result,
        ):
            self.service.run_backtest(request, MagicMock(), db)

        run = db.add.call_args[0][0]
        self.assertEqual(run.summary["mode"], "manual_symbols")
        self.assertNotIn("diagnostics", run.summary)

    # get_scan_observability aggregation tests
    def test_get_scan_observability_aggregates_jobs_runs_and_universe(self):
        universe_stats = {
            "total_active": 5000,
            "st_active": 100,
            "non_st_active": 4900,
            "by_board": {"main": 3000, "gem": 1200, "star": 500, "bse": 300},
            "by_board_exclude_st": {
                "main": 2900,
                "gem": 1180,
                "star": 490,
                "bse": 280,
            },
            "defaults": {"boards": ["main"], "exclude_st": True},
        }

        job1 = SimpleNamespace(
            job_id="job1",
            request_payload={"name": "全市场扫描", "strategy_id": "ma_cross_v1"},
            status="running",
            stage="running",
            progress_pct=50.0,
            total_symbols=100,
            processed_symbols=50,
            eta_seconds=None,
            created_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 1),
        )
        scan_run = SimpleNamespace(
            id=101,
            name="扫描001",
            strategy_id="gmm_volume_v1",
            status="completed",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            summary={
                "total_return": 0.1,
                "diagnostics": {
                    "universe_size": 100,
                    "data_available_count": 95,
                    "data_empty_count": 5,
                },
            },
            warnings=["无可用日线数据股票数: 5/100"],
            created_at=datetime(2026, 1, 1),
        )
        manual_run = SimpleNamespace(
            id=100,
            name="手工回测",
            strategy_id="ma_cross_v1",
            status="completed",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            summary={"total_return": 0.05},
            warnings=[],
            created_at=datetime(2026, 1, 1),
        )

        job_query = MagicMock()
        job_chain = job_query.filter.return_value.order_by.return_value
        job_chain = job_chain.limit.return_value
        job_chain.all.return_value = [job1]
        job_count_query = MagicMock()
        job_count_query.filter.return_value.group_by.return_value.all.return_value = [
            ("running", 1),
            ("success", 2),
        ]
        run_query = MagicMock()
        run_chain = run_query.options.return_value.filter.return_value
        run_chain = run_chain.order_by.return_value.limit.return_value
        run_chain.all.return_value = [scan_run, manual_run]
        run_count_query = MagicMock()
        run_count_query.filter.return_value.scalar.return_value = 3

        db = MagicMock()
        db.query.side_effect = [
            job_query,
            job_count_query,
            run_query,
            run_count_query,
        ]

        with patch.object(
            BacktestService, "get_universe_stats", return_value=universe_stats
        ):
            result = self.service.get_scan_observability(
                MagicMock(id=7), db, recent_limit=50
            )

        self.assertEqual(result["universe"], universe_stats)
        self.assertIn("generated_at", result)

        self.assertEqual(len(result["active_jobs"]), 1)
        self.assertEqual(result["active_jobs"][0]["job_id"], "job1")
        self.assertEqual(result["active_jobs"][0]["strategy_id"], "ma_cross_v1")
        self.assertEqual(result["active_jobs"][0]["status"], "running")

        self.assertEqual(len(result["recent_scan_runs"]), 1)
        self.assertEqual(result["recent_scan_runs"][0]["run_id"], 101)
        self.assertEqual(
            result["recent_scan_runs"][0]["diagnostics"]["universe_size"], 100
        )

        self.assertEqual(
            result["counters"]["jobs"],
            {"pending": 0, "running": 1, "success": 2, "failed": 0, "cancelled": 0},
        )
        self.assertEqual(result["counters"]["runs"]["total"], 3)
        self.assertEqual(result["counters"]["runs"]["recent_scan_count"], 1)
    # _run_strategy_select_mode fault-tolerance tests
    @patch("app.services.backtest.service.stock_service")
    def test_strategy_select_skips_symbols_with_data_fetch_error(self, mock_stock_svc):
        """单只股票行情获取抛错不应中断整个 strategy_select 回测。"""
        mock_stock_svc.get_all_stock_symbols.return_value = ["000001", "000002"]
        mock_stock_svc.get_daily_data.side_effect = ValueError("行情源连接失败")

        request = self._make_request(
            mode="strategy_select",
            strategy_id="ma_cross_v1",
            symbols=[],
            pool_symbols=[],
            universe_type="all",
            strategy_params={
                "boards": ["main"],
                "exclude_st": True,
                "short_window": 5,
                "long_window": 20,
            },
        )

        result = self.service._run_strategy_select_mode(request)
        # 不抛异常；两只股票都被跳过并记录告警
        self.assertEqual(result["symbols"], ["000001", "000002"])
        self.assertEqual(result["diagnostics"]["data_empty_count"], 2)
        self.assertTrue(
            any("获取行情失败" in w for w in result["warnings"]),
            msg=f"expected data-fetch warning, got {result['warnings']}",
        )
        self.assertEqual(result["trades"], [])

    @patch("app.services.backtest.service.stock_service")
    def test_strategy_select_skips_symbol_whose_candidates_raise(self, mock_stock_svc):
        """策略 generate_candidates 抛错应跳过该股票而不是失败整个任务。"""
        mock_stock_svc.get_all_stock_symbols.return_value = ["000001"]

        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=8, freq="D"),
                "open": [10.0] * 8,
                "close": [10.0] * 8,
                "volume": [1000.0] * 8,
            }
        )
        mock_stock_svc.get_daily_data.return_value = (df, None, None)

        with patch("app.services.backtest.service.get_strategy") as mock_get_strategy:
            fake_strategy = MagicMock()
            fake_strategy.required_columns.return_value = {"datetime", "open", "close"}
            fake_strategy.generate_candidates.side_effect = ValueError("参数非法")
            mock_get_strategy.return_value = fake_strategy

            request = self._make_request(
                mode="strategy_select",
                strategy_id="ma_cross_v1",
                symbols=[],
                pool_symbols=[],
                universe_type="all",
                strategy_params={"boards": ["main"], "exclude_st": True},
            )

            result = self.service._run_strategy_select_mode(request)

        self.assertTrue(
            any("策略生成候选失败" in w for w in result["warnings"]),
            msg=f"expected candidate warning, got {result['warnings']}",
        )
        self.assertEqual(result["trades"], [])


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
