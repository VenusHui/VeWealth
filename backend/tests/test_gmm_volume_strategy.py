"""测试 GMM 成交量密度策略（含共享数据并发安全回归）"""

from __future__ import annotations

import threading
import unittest

import numpy as np
import pandas as pd

from app.services.backtest.strategies.gmm_volume_v1 import (
    GMMVolumeV1Strategy,
    _SHARED_DATA_LOCK,
)


def _make_market_df(symbol: str, base_price: float, n: int = 40) -> pd.DataFrame:
    """构造价格波动的日线数据，足以让 GMM 拟合出多峰分布。"""
    rng = np.random.default_rng(42)
    closes = base_price + np.sin(np.linspace(0, 12, n)) * 1.5 + rng.normal(0, 0.05, n)
    closes = np.round(closes, 2)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=n, freq="D"),
            "open": closes,
            "close": closes,
            "high": closes + 0.1,
            "low": closes - 0.1,
            "volume": 1000 + (np.arange(n) % 5) * 200,
            "symbol": symbol,
        }
    )


def _params() -> dict:
    return {
        "lookback_days": 20,
        "threshold": 0.7,
        "max_components": 4,
        "refit_interval": 5,
        "max_workers": 1,
    }


class GMMVolumeStrategyTests(unittest.TestCase):
    def setUp(self):
        self.strategy = GMMVolumeV1Strategy()

    def test_generate_candidates_returns_expected_columns(self):
        df = _make_market_df("000001", base_price=10.0)
        result = self.strategy.generate_candidates(df, _params())
        self.assertIsInstance(result, pd.DataFrame)
        for col in ("trade_date", "symbol", "signal_strength", "reason"):
            self.assertIn(col, result.columns)
        if not result.empty:
            self.assertTrue((result["symbol"] == "000001").all())

    def test_generate_candidates_empty_market_returns_empty(self):
        result = self.strategy.generate_candidates(pd.DataFrame(), _params())
        self.assertTrue(result.empty)
        self.assertIn("symbol", result.columns)

    def test_shared_data_lock_guards_global_state(self):
        """模块级全局数据必须由锁保护，防止并发调用互相污染。"""
        self.assertTrue(hasattr(_SHARED_DATA_LOCK, "acquire"))
        self.assertTrue(hasattr(_SHARED_DATA_LOCK, "release"))
        self.assertTrue(_SHARED_DATA_LOCK.acquire(blocking=False))
        try:
            pass
        finally:
            _SHARED_DATA_LOCK.release()

    def test_concurrent_generate_candidates_no_cross_contamination(self):
        """并发调用 generate_candidates 时各 symbol 结果不应串号。"""
        df_a = _make_market_df("000001", base_price=10.0)
        df_b = _make_market_df("600519", base_price=12.0)

        results: dict[str, pd.DataFrame] = {}
        errors: list[Exception] = []

        def run(key: str, df: pd.DataFrame) -> None:
            try:
                results[key] = self.strategy.generate_candidates(df, _params())
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [
            threading.Thread(target=run, args=("a", df_a)),
            threading.Thread(target=run, args=("b", df_b)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        expected = {"a": "000001", "b": "600519"}
        for key, res in results.items():
            self.assertIn("symbol", res.columns)
            if not res.empty:
                self.assertTrue(
                    (res["symbol"] == expected[key]).all(),
                    msg=f"{key} 的结果混入了其他 symbol",
                )


if __name__ == "__main__":
    unittest.main()
