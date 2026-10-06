"""腾讯 ifzq 分钟 K 线接入（VEW-63）测试。

覆盖：
- 原始 bar → 标准化 DataFrame：列名、时间解析、**成交量「手」→「股」换算**（与
  mootdx 口径一致，VEW-61 交叉校验的唯一差异项）、排序与去重；
- ``start_time`` 游标翻页、``start_offset`` 跳过最新 N 根、请求区间过滤；
- 翻页终止条件：不足一页即到底、游标未前进不得死循环、超预算提前返回；
- 分钟取数链：mootdx → 东财 → 腾讯 的回退顺序；东财 ``down`` 时整段跳过（验收项：
  东财 push2his 被封锁时仍能通过腾讯完成）；
- 复权口径如实标注（腾讯为不复权，与 mootdx 同）。
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

import pandas as pd

from app.core.source_health import source_monitor
from app.providers import astock_data as ad
from app.providers import astock_provider as ap


def _minute_times(n: int, start: str = "2026-09-25 09:30") -> list[str]:
    """生成 n 个连续的 ``YYYYMMDDHHMM`` 时间串（升序）。"""
    return [
        t.strftime("%Y%m%d%H%M")
        for t in pd.date_range(start=start, periods=n, freq="min")
    ]


def _raw_bars(times: list[str], volume: float = 100.0) -> list[list]:
    """构造腾讯原始 bar：``[time, open, close, high, low, volume(手), {}, ...]``。"""
    return [
        [t, "10.00", "10.50", "10.60", "9.90", f"{volume:.2f}", {}, "0.10"]
        for t in times
    ]


def _tencent_source(all_bars: list[list]):
    """模拟真实接口：返回游标之前（不含游标那根）的最近 count 根。

    与 VEW-61 实测的翻页语义一致 —— ``start_time`` 指定的是**上一页最旧一根**，
    新一页的最右一根严格早于游标。
    """

    def fake(code, period, start_time="", count=800, timeout=12, _record=True):
        if not start_time:
            end = len(all_bars)
        else:
            end = next(
                (i for i, b in enumerate(all_bars) if str(b[0]) == str(start_time)),
                None,
            )
            if end is None:
                return []
        return all_bars[max(0, end - count) : end]

    return fake


class TencentSymbolTests(unittest.TestCase):
    """代码 → 腾讯 sh/sz/bj 前缀。"""

    def test_prefixes_by_market(self):
        self.assertEqual(ad._tencent_symbol("000001"), "sz000001")
        self.assertEqual(ad._tencent_symbol("600519"), "sh600519")
        self.assertEqual(ad._tencent_symbol("300750"), "sz300750")
        self.assertEqual(ad._tencent_symbol("830799"), "bj830799")

    def test_already_prefixed_is_preserved(self):
        self.assertEqual(ad._tencent_symbol("sz000001"), "sz000001")
        self.assertEqual(ad._tencent_symbol("SH600519"), "sh600519")


class TencentNormalizeTests(unittest.TestCase):
    """原始 bar → 标准化 DataFrame。"""

    def test_columns_and_volume_unit_conversion(self):
        # 验收核心：腾讯成交量单位是「手」，对外统一按 mootdx 的「股」输出（×100）。
        df = ad.tencent_minute_frame(_raw_bars(["202609301450"], volume=16079.0))
        self.assertIsNotNone(df)
        self.assertEqual(
            list(df.columns), ["datetime", "open", "close", "high", "low", "volume"]
        )
        self.assertEqual(df["volume"].iloc[0], 1607900.0)
        self.assertEqual(str(df["datetime"].iloc[0]), "2026-09-30 14:50:00")

    def test_sorted_and_deduplicated(self):
        # 分页交界会重叠：同一根 bar 出现两次时保留后一份，并按时间升序输出
        bars = _raw_bars(["202609301500", "202609301450", "202609301450"])
        bars[0][2] = "11.00"  # 最后一根改个价，确认 keep="last" 生效
        df = ad.tencent_minute_frame(bars)
        self.assertEqual(
            list(df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")),
            [
                "2026-09-30 14:50:00",
                "2026-09-30 15:00:00",
            ],
        )
        self.assertEqual(df["close"].iloc[-1], 11.0)

    def test_malformed_rows_are_skipped(self):
        bars = [
            ["202609301450", "10.00", "10.50", "10.60", "9.90", "100.00", {}, "0.1"],
            ["not-a-time", "10.00", "10.50", "10.60", "9.90", "100.00"],
            ["202609301455", "10.00", "bad", "10.60", "9.90", "100.00"],
            ["202609301500"],  # 列数不足
        ]
        df = ad.tencent_minute_frame(bars)
        self.assertEqual(len(df), 1)
        self.assertEqual(str(df["datetime"].iloc[0]), "2026-09-30 14:50:00")

    def test_missing_volume_keeps_the_bar(self):
        # 单根缺量不该丢掉整根 K 线：OHLC 对形态 / 回测仍然可用
        df = ad.tencent_minute_frame(
            [["202609301450", "10.00", "10.50", "10.60", "9.90", ""]]
        )
        self.assertEqual(len(df), 1)
        self.assertEqual(df["volume"].iloc[0], 0.0)

    def test_empty_input_returns_none(self):
        self.assertIsNone(ad.tencent_minute_frame([]))
        self.assertIsNone(ad.tencent_minute_frame(None))


class TencentBoundedTimeoutTests(unittest.TestCase):
    """腾讯回退的单次超时按剩余预算夹紧（与东财回退同口径）。"""

    def test_default_when_no_deadline(self):
        self.assertEqual(
            ap._bounded_tencent_timeout(None), ap._TENCENT_FALLBACK_TIMEOUT
        )

    def test_clamped_to_remaining_budget(self):
        # 剩余 3s < 默认 8s：夹紧到剩余预算，单次请求装得进整条链路的墙钟预算
        deadline = time.monotonic() + 3.0
        got = ap._bounded_tencent_timeout(deadline)
        self.assertLess(got, ap._TENCENT_FALLBACK_TIMEOUT)
        self.assertGreater(got, 2.5)

    def test_floors_at_minimum_when_budget_is_gone(self):
        # 剩余预算再少也保底一次请求的时间，否则回退等于直接放弃
        self.assertEqual(
            ap._bounded_tencent_timeout(time.monotonic() - 5),
            ap._TENCENT_FALLBACK_MIN_TIMEOUT,
        )


class TencentPaginationTests(unittest.TestCase):
    """``_fetch_kline_tencent`` 的翻页、跳过与区间过滤。"""

    def setUp(self):
        self.provider = ap.AStockDataProvider()

    def _fetch(self, all_bars, **kwargs):
        with mock.patch.object(
            ap, "tencent_minute_bars", side_effect=_tencent_source(all_bars)
        ):
            return self.provider._fetch_kline_tencent(
                kwargs.pop("code", "000001"),
                period=kwargs.pop("period", "5"),
                start_date=kwargs.pop("start_date", ""),
                end_date=kwargs.pop("end_date", ""),
                **kwargs,
            )

    def test_single_page_within_the_800_cap(self):
        bars = _raw_bars(_minute_times(50))
        df = self._fetch(bars, count=50)
        self.assertEqual(len(df), 50)
        self.assertEqual(
            str(df["datetime"].iloc[-1]), str(pd.Timestamp("2026-09-25 10:19"))
        )

    def test_paginates_beyond_the_single_page_cap(self):
        # 单页上限 800：要 900 根必须翻第二页（VEW-61 实测 count>800 静默回落）
        bars = _raw_bars(_minute_times(1000))
        df = self._fetch(bars, count=900)
        self.assertEqual(len(df), 900)
        # 900 根且从最新往前取 → 最旧一根是倒数第 900 根
        expected_oldest = pd.Timestamp(_minute_times(1000)[-900])
        self.assertEqual(pd.Timestamp(df["datetime"].iloc[0]), expected_oldest)

    def test_start_offset_skips_the_newest_bars(self):
        # 前端滚动加载：跳过最新 10 根后再取 5 根
        bars = _raw_bars(_minute_times(100))
        df = self._fetch(bars, count=5, start_offset=10)
        expected = [pd.Timestamp(t) for t in _minute_times(100)[-15:-10]]
        self.assertEqual(list(pd.to_datetime(df["datetime"])), expected)

    def test_filters_to_the_requested_range(self):
        bars = _raw_bars(_minute_times(60, start="2026-09-30 09:30"))
        df = self._fetch(
            bars,
            count=60,
            start_date="2026-09-30 09:40:00",
            end_date="2026-09-30 09:50:00",
        )
        self.assertEqual(len(df), 11)
        self.assertEqual(str(df["datetime"].iloc[0]), "2026-09-30 09:40:00")
        self.assertEqual(str(df["datetime"].iloc[-1]), "2026-09-30 09:50:00")

    def test_stops_when_a_short_page_means_depth_exhausted(self):
        # 要 500 根但历史只有 120 根：取满即停，不空转
        bars = _raw_bars(_minute_times(120))
        calls = []

        def fake(code, period, start_time="", count=800, timeout=12, _record=True):
            calls.append((start_time, count))
            return _tencent_source(bars)(
                code, period, start_time, count, timeout, _record
            )

        with mock.patch.object(ap, "tencent_minute_bars", side_effect=fake):
            df = self.provider._fetch_kline_tencent(
                "000001", period="5", start_date="", end_date="", count=500
            )
        self.assertEqual(len(df), 120)
        self.assertEqual(len(calls), 1, "不足一页即到底，不该再翻")

    def test_non_advancing_cursor_does_not_loop_forever(self):
        # 接口若忽略 start_time（返回同一页），必须停下来而不是死循环
        page = _raw_bars(_minute_times(800))
        calls = []

        def fake(code, period, start_time="", count=800, timeout=12, _record=True):
            calls.append(start_time)
            return page

        with mock.patch.object(ap, "tencent_minute_bars", side_effect=fake):
            df = self.provider._fetch_kline_tencent(
                "000001", period="5", start_date="", end_date="", count=2000
            )
        self.assertIsNotNone(df)
        self.assertEqual(len(calls), 2, "游标未前进应立即终止翻页")

    def test_returns_none_when_over_budget(self):
        with mock.patch.object(ap, "tencent_minute_bars") as bars:
            df = self.provider._fetch_kline_tencent(
                "000001",
                period="5",
                start_date="",
                end_date="",
                count=10,
                deadline=time.monotonic() - 1,
            )
        self.assertIsNone(df)
        bars.assert_not_called()

    def test_unsupported_period_returns_none(self):
        # 日线（101）不属腾讯分钟接口，直接放弃而不是发一个无意义的请求
        with mock.patch.object(ap, "tencent_minute_bars") as bars:
            df = self.provider._fetch_kline_tencent(
                "000001", period="101", start_date="", end_date="", count=10
            )
        self.assertIsNone(df)
        bars.assert_not_called()

    def test_marks_raw_unadjusted(self):
        # 腾讯 mkline 是不复权原始行情：必须如实标注，调用方请求 qfq 时据此降级
        df = self._fetch(_raw_bars(_minute_times(10)), count=10)
        self.assertEqual(df.attrs["adjust_served"], "")
        self.assertFalse(df.attrs["adjust_degraded"])

    def test_returns_none_when_range_excludes_everything(self):
        bars = _raw_bars(_minute_times(10, start="2026-09-30 09:30"))
        df = self._fetch(
            bars,
            count=10,
            start_date="2026-01-01 09:30:00",
            end_date="2026-01-02 15:00:00",
        )
        self.assertIsNone(df)


class MinuteFallbackChainTests(unittest.TestCase):
    """分钟取数链回退顺序与验收项：东财被封锁时由腾讯顶上。"""

    def setUp(self):
        source_monitor.reset()
        source_monitor.configure(fail_threshold=3)
        self.provider = ap.AStockDataProvider()

    def tearDown(self):
        source_monitor.reset()

    @staticmethod
    def _mark_eastmoney_down(times: int = 3) -> None:
        for _ in range(times):
            source_monitor.record_attempt(
                "eastmoney", ok=False, error="RemoteDisconnected"
            )

    def _run_chain(self, bars, period: str = "1", eastmoney=None):
        """跑完整分钟链：mootdx 置空 → 东财按 eastmoney 替身 → 腾讯按 bars 替身。

        Returns:
            ``(df, eastmoney_mock, tencent_mock)``
        """
        with mock.patch.object(
            ap.AStockDataProvider, "_fetch_kline_mootdx", return_value=None
        ), mock.patch.object(
            ap, "eastmoney_trends2", return_value=eastmoney
        ) as em, mock.patch.object(
            ap, "eastmoney_kline", return_value=eastmoney
        ) as em_k, mock.patch.object(
            ap, "tencent_minute_bars", side_effect=_tencent_source(bars)
        ) as tc, mock.patch.object(
            ap.time, "sleep"
        ):
            df = self.provider.fetch_minute_data(
                "000001",
                start_datetime="2026-09-30 09:00:00",
                end_datetime="2026-09-30 16:00:00",
                period=period,
                adjust="",
            )
        return df, (em if period == "1" else em_k), tc

    def test_falls_back_to_tencent_when_eastmoney_returns_nothing(self):
        bars = _raw_bars(_minute_times(30, start="2026-09-30 09:30"))
        df, _em, tc = self._run_chain(bars)
        self.assertIsNotNone(df)
        self.assertTrue(tc.called, "东财无数据时应回退腾讯")
        self.assertEqual(len(df), 30)
        # 成交量已是「股」（原始 100 手 → 10000 股）
        self.assertEqual(df["volume"].iloc[0], 10000.0)

    def test_acceptance_eastmoney_blocked_still_served_by_tencent(self):
        """验收项：东财 push2his 被封锁（source_monitor=down）时仍能取到分钟数据。"""
        self._mark_eastmoney_down()
        bars = _raw_bars(_minute_times(30, start="2026-09-30 09:30"))
        df, em, tc = self._run_chain(bars)

        self.assertIsNotNone(df)
        em.assert_not_called()
        self.assertTrue(tc.called, "东财熔断后必须直接回退腾讯")
        self.assertEqual(len(df), 30)

    def test_eastmoney_still_preferred_while_healthy(self):
        # 熔断未触发时保持既有顺序：东财能返回就不打腾讯，避免改变现有行为
        em_df = pd.DataFrame(
            {
                "datetime": ["2026-09-30 09:30:00"],
                "open": [10.0],
                "close": [10.5],
                "high": [10.6],
                "low": [9.9],
                "volume": [100.0],
            }
        )
        source_monitor.record_attempt("eastmoney", ok=True, duration_ms=10.0)
        df, em, tc = self._run_chain([], eastmoney=em_df)
        self.assertEqual(em.call_count, 1)
        tc.assert_not_called()
        self.assertEqual(df["close"].iloc[0], 10.5)

    def test_tencent_only_used_for_supported_periods(self):
        # period=101 不在腾讯分钟接口内：链路最终返回空，而不是报错
        df, _em, tc = self._run_chain([], period="101")
        self.assertIsNone(df)
        tc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
