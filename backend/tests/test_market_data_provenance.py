"""测试行情 provenance 与 mootdx 长区间分页（VEW-26）。

覆盖：
- 请求日期归一化、覆盖缺口判定；
- fetch_daily_data_with_meta 的 source/actual range/gap/last_bar；
- 全源失败时 failure_reason；
- count>800 时 mootdx 分页取全，不再返回被截断的 500 根。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import unittest

import pandas as pd

from app.providers.astock_provider import (
    AStockDataProvider,
    _norm_request_date,
    _coverage_gap,
)


def _df_for_dates(dates, extra: dict | None = None):
    """构造带 datetime 列的日线 DataFrame。"""
    rows = {
        "datetime": pd.to_datetime(dates).strftime("%Y-%m-%d %H:%M:%S"),
        "open": 10.0,
        "close": 10.0,
        "high": 10.0,
        "low": 9.0,
        "volume": 1.0,
        "amount": 1.0,
    }
    if extra:
        rows.update(extra)
    return pd.DataFrame(rows)


def _fake_mootdx_client(all_dates):
    """返回一个按 start/offset 切片返回 bars 的伪客户端。"""
    client = MagicMock()

    def bars(symbol, frequency, start, offset):
        n = int(offset)
        sl = all_dates[int(start) : int(start) + n]
        return _df_for_dates(sl)

    client.bars.side_effect = bars
    return client


class NormRequestDateTests(unittest.TestCase):
    def test_normalizes_yyyymmdd_and_yyyy_mm_dd(self):
        self.assertEqual(_norm_request_date("20260101"), "2026-01-01")
        self.assertEqual(_norm_request_date("2026-01-02"), "2026-01-02")
        self.assertIsNone(_norm_request_date(""))
        self.assertIsNone(_norm_request_date(None))


class CoverageGapTests(unittest.TestCase):
    def test_no_gap_when_covered(self):
        self.assertFalse(
            _coverage_gap("2026-01-01", "2026-01-10", "2026-01-01", "2026-01-10")
        )

    def test_gap_when_start_lagging(self):
        self.assertTrue(
            _coverage_gap("2026-01-01", "2026-01-10", "2026-01-05", "2026-01-10")
        )

    def test_gap_when_end_short(self):
        self.assertTrue(
            _coverage_gap("2026-01-01", "2026-01-10", "2026-01-01", "2026-01-05")
        )

    def test_gap_when_missing_actual(self):
        self.assertTrue(_coverage_gap("2026-01-01", "2026-01-10", None, None))


class FetchDailyDataWithMetaTests(unittest.TestCase):
    def test_mootdx_source_and_range(self):
        df = _df_for_dates(pd.date_range("2026-01-01", "2026-01-10"))
        with patch.object(AStockDataProvider, "_fetch_kline_mootdx", return_value=df):
            provider = AStockDataProvider()
            result = provider.fetch_daily_data_with_meta(
                "000001", "20260101", "20260110", adjust="qfq"
            )
        self.assertIsNotNone(result.df)
        self.assertEqual(result.provenance.source, "mootdx")
        self.assertEqual(result.provenance.adjustment, "qfq")
        self.assertEqual(result.provenance.requested_start, "2026-01-01")
        self.assertEqual(result.provenance.requested_end, "2026-01-10")
        self.assertEqual(result.provenance.actual_start, "2026-01-01")
        self.assertEqual(result.provenance.actual_end, "2026-01-10")
        self.assertEqual(result.provenance.bar_count, 10)
        self.assertEqual(result.provenance.last_bar, "2026-01-10")
        self.assertFalse(result.provenance.gap)

    def test_gap_detected_on_partial_range(self):
        df = _df_for_dates(pd.date_range("2026-01-05", "2026-01-10"))
        with patch.object(AStockDataProvider, "_fetch_kline_mootdx", return_value=df):
            provider = AStockDataProvider()
            result = provider.fetch_daily_data_with_meta(
                "000001", "20260101", "20260110"
            )
        self.assertTrue(result.provenance.gap)
        self.assertEqual(result.provenance.actual_start, "2026-01-05")

    def test_failure_reason_when_all_sources_fail(self):
        with patch.object(
            AStockDataProvider, "_fetch_kline_mootdx", return_value=None
        ), patch.object(
            AStockDataProvider, "_fetch_daily_tushare", return_value=None
        ), patch(
            "app.providers.astock_provider.eastmoney_kline", return_value=None
        ):
            provider = AStockDataProvider()
            result = provider.fetch_daily_data_with_meta(
                "000001", "20260101", "20260110", max_retries=0
            )
        self.assertIsNone(result.df)
        self.assertIsNone(result.provenance.source)
        self.assertIn("无数据", result.provenance.failure_reason)


class MootdxPaginationTests(unittest.TestCase):
    def test_paginates_when_count_exceeds_single_page(self):
        all_dates = pd.date_range(end="2026-01-30", periods=1600, freq="D")
        client = _fake_mootdx_client(all_dates)
        with patch("app.providers.astock_provider._mootdx_client", client):
            provider = AStockDataProvider()
            df = provider._fetch_kline_mootdx("000001", "101", "", "", count=1500)
        self.assertIsNotNone(df)
        # 单页最多 800，count=1500 必须翻页
        self.assertGreaterEqual(client.bars.call_count, 2)
        self.assertGreaterEqual(len(df), 1500)
        self.assertEqual(len(df["datetime"].unique()), len(df))


if __name__ == "__main__":
    unittest.main()
