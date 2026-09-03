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
from app.services.backtest.strategies.ma_cross_v1 import MACrossV1Strategy


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
        gem_st_rule = SecurityRule(board="gem", is_st=True)
        self.assertEqual(
            price_limit_rate("300001", date(2020, 8, 23), gem_st_rule), 0.05
        )
        self.assertEqual(
            price_limit_rate("300001", date(2020, 8, 24), gem_st_rule), 0.20
        )
        self.assertEqual(
            price_limit_rate("688001", date(2026, 1, 1), SecurityRule("star", True)),
            0.20,
        )
        self.assertEqual(
            price_limit_rate("830001", date(2026, 1, 1), SecurityRule("bse", True)),
            0.30,
        )
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

    def test_slippage_price_is_clamped_to_daily_price_limits(self):
        buy_frame = _bars("600000", [10.0, 10.99, 10.99], [10.0, 10.0, 10.0])
        order = pd.DataFrame(
            [
                {
                    "trade_date": "2026-01-05",
                    "symbol": "600000",
                    "position_size_pct": 0.5,
                }
            ]
        )
        buy_result = run_portfolio(
            {"600000": buy_frame},
            order,
            100_000.0,
            CostModel(0.0, 0.0, 0.0, 0.01),
            hold_days=5,
            default_position_size_pct=0.5,
        )
        buy = next(trade for trade in buy_result.trades if trade["side"] == "buy")
        self.assertEqual(buy["price"], 11.0)

        sell_frame = _bars("600000", [10.0, 10.0, 9.01], [10.0, 10.0, 9.01])
        sell_result = run_portfolio(
            {"600000": sell_frame},
            order,
            100_000.0,
            CostModel(0.0, 0.0, 0.0, 0.01),
            hold_days=1,
            default_position_size_pct=0.5,
        )
        sell = next(trade for trade in sell_result.trades if trade["side"] == "sell")
        self.assertEqual(sell["price"], 9.0)

    def test_intraday_input_uses_first_open_and_last_close(self):
        frame = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2026-01-05 09:30:00",
                        "2026-01-05 15:00:00",
                        "2026-01-06 09:30:00",
                        "2026-01-06 15:00:00",
                    ]
                ),
                "open": [10.0, 10.5, 10.2, 10.8],
                "close": [10.5, 10.0, 10.6, 11.0],
                "symbol": ["000001"] * 4,
            }
        )
        result = run_portfolio(
            {"000001": frame},
            pd.DataFrame(
                [
                    {
                        "trade_date": "2026-01-05",
                        "symbol": "000001",
                        "position_size_pct": 0.5,
                    }
                ]
            ),
            100_000.0,
            CostModel(0.0, 0.0, 0.0, 0.0),
            hold_days=5,
            default_position_size_pct=0.5,
        )
        buy = next(trade for trade in result.trades if trade["side"] == "buy")
        self.assertEqual(buy["price"], 10.2)
        self.assertEqual(
            result.positions_snapshot[-1]["holdings"][0]["last_price"], 11.0
        )


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

    def test_ma_schema_exposes_unified_portfolio_defaults(self):
        params = {item["key"]: item for item in MACrossV1Strategy.param_schema()}
        self.assertEqual(params["position_size_pct"]["default"], 0.1)
        self.assertEqual(params["hold_days"]["default"], 5)


class WarmupTradeStartJointTests(unittest.TestCase):
    """VEW-24×VEW-23 联合回归：warmup 取数 + trade_start 执行日切片落到统一引擎。"""

    def test_fetch_daily_with_warmup_pulls_back_start_for_250_day_strategy(self):
        """250 日策略（GMM min_history_bars=250）取数必须回拉起始日，保证足量 bar。

        回归点：若直接按 request.start_date 取数，GMM250 / MA240 在短区间会因
        "无历史→0 信号" 而失效 —— 这是 VEW-24 要修的根因。
        """
        service = BacktestService()
        captured = {}

        def fake_get_daily_data(symbol, start_date, end_date, count=None):
            captured["start_date"] = start_date
            captured["count"] = count
            n = 320
            return (
                pd.DataFrame(
                    {
                        "datetime": pd.bdate_range(end_date, periods=n, freq="D"),
                        "open": [10.0] * n,
                        "close": [10.0] * n,
                    }
                ),
                None,
                None,
            )

        with patch(
            "app.services.backtest.service.stock_service.get_daily_data",
            side_effect=fake_get_daily_data,
        ):
            df, effective_start = service._fetch_daily_with_warmup(
                "000001", "2026-01-05", "2026-01-09", "gmm_volume_v1"
            )

        # GMM min_history_bars=250，回拉 int((250+30)*1.6)=448 自然日
        self.assertLess(effective_start, "2026-01-05")
        self.assertEqual(captured["start_date"], effective_start)
        self.assertEqual(captured["count"], 500)
        self.assertGreaterEqual(len(df), 250)

    def test_run_portfolio_trade_start_skips_pre_start_orders_keeps_first_day(self):
        """trade_start 只对执行日 >= trade_start 的订单成交，warmup bar 不污染净值。

        信号落在 warmup bar（01-05 → 执行 01-06 < start）应被跳过；
        信号落在 start 前一日（01-06 → 执行 01-07 == start）应被保留（首日交易不丢弃）。
        """
        opens = [10.0, 10.0, 10.0, 10.0, 10.0]
        frame = _bars("000001", opens, opens)  # 2026-01-05..01-09
        orders = pd.DataFrame(
            [
                {
                    "trade_date": frame.iloc[0]["datetime"],
                    "symbol": "000001",
                    "position_size_pct": 1.0,
                    "reason": "warmup",
                },  # 信号 01-05 → 执行 01-06 < start → 跳过
                {
                    "trade_date": frame.iloc[1]["datetime"],
                    "symbol": "000001",
                    "position_size_pct": 1.0,
                    "reason": "first_day",
                },  # 信号 01-06 → 执行 01-07 == start → 保留
            ]
        )
        result = run_portfolio(
            {"000001": frame},
            orders,
            100_000.0,
            CostModel(
                commission_rate=0.001,
                min_commission=0.0,
                stamp_tax_rate=0.001,
                slippage_rate=0.01,
            ),
            hold_days=1,
            default_position_size_pct=1.0,
            trade_start="2026-01-07",
        )

        buys = [t for t in result.trades if t["side"] == "buy"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]["datetime"][:10], "2026-01-07")
        self.assertTrue(all(t["datetime"][:10] >= "2026-01-07" for t in result.trades))
        # 净值曲线与订单门保持一致：首点即 trade_start，不把 warmup 平段写进曲线
        curve_dates = [p["datetime"][:10] for p in result.equity_curve]
        self.assertEqual(curve_dates[0], "2026-01-07")
        self.assertNotIn("2026-01-05", curve_dates)

    def test_trade_start_gates_equity_curve_and_annual_return_span(self):
        """净值曲线不带 warmup 前缀，年化跨度用 start→end 而非 warmup→end。

        回归点：此前 equity_curve/snapshots 对每个 bar 日期无条件写入，warmup 回拉
        出的平段被并入曲线 → first_date 前移、annual_return 被系统性低估、Sharpe 被稀释。
        """
        n = 15
        opens = [10.0 + 0.1 * i for i in range(n)]
        frame = _bars("000001", opens, opens)  # 2026-01-05..2026-01-26
        trade_start = "2026-01-13"
        orders = pd.DataFrame(
            [
                {
                    "trade_date": frame.iloc[5]["datetime"],  # 2026-01-12
                    "symbol": "000001",
                    "position_size_pct": 1.0,
                    "reason": "first_day",
                }  # 信号 01-12 → 执行 01-13 == start → 保留
            ]
        )
        result = run_portfolio(
            {"000001": frame},
            orders,
            100_000.0,
            CostModel(
                commission_rate=0.001,
                min_commission=0.0,
                stamp_tax_rate=0.001,
                slippage_rate=0.01,
            ),
            hold_days=5,
            default_position_size_pct=1.0,
            trade_start=trade_start,
        )

        curve_dates = [p["datetime"][:10] for p in result.equity_curve]
        self.assertEqual(curve_dates[0], trade_start)
        self.assertNotIn("2026-01-05", curve_dates)
        self.assertTrue(all(d >= trade_start for d in curve_dates))

        gated = calc_summary(result.equity_curve, result.trades, 100_000.0)
        # 人为把 warmup 平段塞回曲线首部，模拟门控前的行为：first_date 前移 → 年化被压低
        polluted_points = [
            {"datetime": d.strftime("%Y-%m-%d 00:00:00"), "equity": 100_000.0}
            for d in frame.loc[
                frame["datetime"] < pd.Timestamp(trade_start), "datetime"
            ]
        ]
        polluted = calc_summary(
            polluted_points + result.equity_curve, result.trades, 100_000.0
        )
        self.assertGreater(gated["annual_return"], polluted["annual_return"])


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
