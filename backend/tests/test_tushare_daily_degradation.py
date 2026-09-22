"""Tushare 日线 qfq/hfq 降级测试（VEW-55）。

覆盖：
- _fetch_daily_tushare 先取非复权，再用缓存的 adj_factor 本地复权；
- adj_factor 不可用时降级为非复权并在 df.attrs 打降级标记；
- fetch_daily_data_with_meta 把实际口径与降级标记写入 provenance。
"""

from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from app.providers.astock_provider import AStockDataProvider


def _raw_tushare_df():
    """模拟 ts.pro_bar(adj=None) 的返回（trade_date 为 YYYYMMDD 字符串）。"""
    return pd.DataFrame(
        {
            "trade_date": ["20260105", "20260106", "20260107"],
            "open": [10.1, 10.2, 10.3],
            "close": [10.0, 10.4, 10.7],
            "high": [10.5, 10.6, 10.9],
            "low": [9.9, 10.0, 10.2],
            "vol": [1000, 1200, 1100],
            "amount": [1e6, 1.2e6, 1.1e6],
        }
    )


class TushareDailyDegradationTests(unittest.TestCase):
    def setUp(self):
        self.provider = AStockDataProvider()
        # 测试环境无 .env 文件，TUSHARE_TOKEN 为空会导致备源直接跳过
        self.env_patch = mock.patch.multiple(
            "app.providers.astock_provider.settings",
            TUSHARE_ENABLED=True,
            TUSHARE_TOKEN="test-token",
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def _run_fetch(self, *args, **kwargs):
        return self.provider._fetch_daily_tushare(*args, **kwargs)

    def test_qfq_applied_when_adj_factor_available(self):
        """adj_factor 可用时本地复权并标记实际口径。"""
        raw = _raw_tushare_df()
        with mock.patch(
            "app.providers.astock_provider.ts"
        ) as mock_ts, mock.patch.object(
            self.provider, "_apply_tushare_adjust"
        ) as apply_adj:
            mock_ts.pro_bar.return_value = raw
            adjusted = raw.copy()
            adjusted["close"] = adjusted["close"] * 0.5
            apply_adj.return_value = adjusted
            df = self.provider._fetch_daily_tushare(
                "600519", "20260101", "20260110", "qfq", max_retries=0
            )
        self.assertIsNotNone(df)
        # 请求走非复权取数 + 本地复权
        self.assertEqual(mock_ts.pro_bar.call_args.kwargs["adj"], None)
        self.assertEqual(apply_adj.call_args[0][2], "qfq")
        self.assertEqual(df.attrs["adjust_served"], "qfq")
        self.assertFalse(df.attrs["adjust_degraded"])
        # 复权后的 close 被应用
        self.assertAlmostEqual(df["close"].iloc[0], 5.0)

    def test_degraded_to_raw_when_adj_factor_unavailable(self):
        """adj_factor 不可用时降级为非复权并打标记。"""
        raw = _raw_tushare_df()
        with mock.patch(
            "app.providers.astock_provider.ts"
        ) as mock_ts, mock.patch.object(
            self.provider, "_apply_tushare_adjust", return_value=None
        ):
            mock_ts.pro_bar.return_value = raw
            df = self.provider._fetch_daily_tushare(
                "600519", "20260101", "20260110", "qfq", max_retries=0
            )
        self.assertIsNotNone(df)
        self.assertEqual(df.attrs["adjust_served"], "")
        self.assertTrue(df.attrs["adjust_degraded"])
        # 数据保持非复权原值
        self.assertAlmostEqual(df["close"].iloc[0], 10.0)

    def test_none_adjust_fetches_raw_only(self):
        """不复权请求直接返回原始数据，不打降级标记。"""
        raw = _raw_tushare_df()
        with mock.patch(
            "app.providers.astock_provider.ts"
        ) as mock_ts, mock.patch.object(
            self.provider, "_apply_tushare_adjust"
        ) as apply_adj:
            mock_ts.pro_bar.return_value = raw
            df = self.provider._fetch_daily_tushare(
                "600519", "20260101", "20260110", "", max_retries=0
            )
        self.assertIsNotNone(df)
        apply_adj.assert_not_called()
        self.assertEqual(df.attrs["adjust_served"], "")
        self.assertFalse(df.attrs["adjust_degraded"])

    def test_provenance_records_degraded_flag(self):
        """fetch_daily_data_with_meta 从 df.attrs 读实际口径并写入 provenance。"""
        raw = _raw_tushare_df()
        raw.attrs["adjust_served"] = ""
        raw.attrs["adjust_degraded"] = True
        with mock.patch.object(
            self.provider, "_fetch_kline_mootdx", return_value=None
        ), mock.patch(
            "app.providers.astock_provider.eastmoney_kline", return_value=None
        ), mock.patch.object(
            self.provider, "_fetch_daily_tushare", return_value=raw
        ):
            result = self.provider.fetch_daily_data_with_meta(
                "600519", "20260101", "20260110", adjust="qfq", max_retries=0
            )
        self.assertIsNotNone(result.df)
        self.assertEqual(result.provenance.source, "tushare")
        self.assertEqual(result.provenance.adjustment, "")
        self.assertTrue(result.provenance.degraded)


if __name__ == "__main__":
    unittest.main()
