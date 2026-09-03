from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from app.services.backtest.service import BacktestService
from app.services.backtest.strategies.ma_cross_v1 import MACrossV1Strategy
from app.services.backtest.strategies.volume_shrink_drop_v1 import (
    VolumeShrinkDropV1Strategy,
)
from app.services.backtest.validators.strategy_validator import validate_strategy_class


class StrategyV2MigrationTests(unittest.TestCase):
    def test_ma_cross_generate_signals_behavior_kept(self):
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=6, freq="D"),
                "close": [10.0, 9.0, 8.0, 9.0, 10.0, 11.0],
                "open": [10.0, 9.0, 8.0, 9.0, 10.0, 11.0],
            }
        )

        strategy = MACrossV1Strategy()
        signal_df = strategy.generate_signals(df, {"short_window": 2, "long_window": 3})

        self.assertIn("buy_signal", signal_df.columns)
        self.assertIn("sell_signal", signal_df.columns)
        self.assertTrue(signal_df["buy_signal"].astype(bool).any())

    def test_volume_shrink_generate_signals_produces_buy_signal(self):
        # 连续缩量下跌（价格跌>=1%、量缩>=10%）命中后应产生买入信号，不再是 no-op。
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=7, freq="D"),
                "close": [10.0, 9.8, 9.6, 9.4, 9.2, 9.0, 8.8],
                "open": [10.0, 9.8, 9.6, 9.4, 9.2, 9.0, 8.8],
                "volume": [2000, 1700, 1450, 1230, 1040, 880, 750],
            }
        )

        strategy = VolumeShrinkDropV1Strategy()
        signal_df = strategy.generate_signals(
            df,
            {
                "min_price_drop_pct": -1.0,
                "min_volume_shrink_pct": 10.0,
                "consecutive_days": 3,
                "hold_days": 3,
            },
        )

        self.assertIn("buy_signal", signal_df.columns)
        self.assertIn("sell_signal", signal_df.columns)
        self.assertTrue(signal_df["buy_signal"].astype(bool).any())

    def test_both_strategies_are_usable_after_v2_migration(self):
        for strategy_cls in (MACrossV1Strategy, VolumeShrinkDropV1Strategy):
            validation = validate_strategy_class(strategy_cls)
            self.assertTrue(
                validation.usable,
                msg=f"{strategy_cls.strategy_id} should be usable: {validation.unusable_reasons}",
            )

    @patch(
        "app.services.backtest.service.stock_service.get_all_stock_symbols",
        return_value=[],
    )
    @patch("app.services.backtest.service.get_strategy")
    def test_strategy_select_enforces_require_usable(
        self,
        mock_get_strategy,
        _mock_get_symbols,
    ):
        mock_get_strategy.return_value = object()
        service = BacktestService()
        request = SimpleNamespace(
            name="test",
            strategy_id="volume_shrink_drop_v1",
            strategy_params={},
            mode="strategy_select",
            universe_type="all",
            symbols=[],
            pool_symbols=[],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            initial_cash=100000,
        )

        with self.assertRaises(ValueError):
            service._run_strategy_select_mode(request)

        mock_get_strategy.assert_called_once_with(
            "volume_shrink_drop_v1", require_usable=True
        )


if __name__ == "__main__":
    unittest.main()
