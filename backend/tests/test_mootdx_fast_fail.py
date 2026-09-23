"""测试 mootdx 分钟链路快速失败（VEW-54）。

镜像池全挂时 _init_mootdx_client 会串行探测 curated + 扫描候选 + 配置默认，每个
镜像含建连 + 2 次探针取数（各自受 _MOOTDX_CONNECT_TIMEOUT 兜底），最坏可把请求
挂起数十秒、超过前端 15s 超时。本用例验证三类快速失败：

- 镜像探测受墙钟预算约束：预算耗尽即放弃后续镜像，不再逐个探测；
- 并发请求在扫描进行中直接快速返回 None，而不是排队阻塞在锁上等完整扫描；
- mootdx 取数 / 分钟整体请求受预算约束，超时返回空 K 线且不摘除健康缓存客户端。
"""

from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from app.providers import astock_provider as ap


class FakeQuotes:
    """模拟 mootdx factory 返回的客户端。"""

    def bars(self, *args, **kwargs):
        return []


class SymbolAwareClient:
    """Return configured DataFrames per symbol and record every raw request."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def bars(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self.responses.get(kwargs["symbol"], pd.DataFrame())


class FakeClock:
    """可控单调时钟，用于确定性地触发预算耗尽，而不真正 sleep。"""

    def __init__(self, now=0.0):
        self._now = now

    def __call__(self):
        return self._now

    def set(self, now):
        self._now = now


def _kline_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": ["2026-09-23 14:00:00"],
            "open": [1.0],
            "close": [1.5],
            "high": [1.6],
            "low": [0.9],
            "volume": [100],
            "amount": [1000.0],
        }
    )


class MootdxFastFailTests(unittest.TestCase):
    def setUp(self):
        # 重置模块级状态，避免跨用例泄漏
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None
        ap._mootdx_discovered_server = None
        ap._mootdx_last_scan_at = None
        ap._mootdx_scan_cursor = 0
        ap._mootdx_scan_in_progress = False

    def tearDown(self):
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None
        ap._mootdx_discovered_server = None
        ap._mootdx_last_scan_at = None
        ap._mootdx_scan_cursor = 0
        ap._mootdx_scan_in_progress = False

    # ------------------------------------------------------------------
    # 镜像探测预算
    # ------------------------------------------------------------------

    def test_scan_respects_budget_and_stops_probing(self):
        """预算耗尽后停止探测后续镜像：3 个慢镜像后不再尝试第 4 个。"""
        clock = FakeClock()
        # deadline = 3 单位时间；每个镜像探测消耗 1 单位 -> 只试 3 个镜像
        with mock.patch.object(ap.time, "monotonic", side_effect=clock):
            with mock.patch.object(
                ap, "_mootdx_scan_due", return_value=False
            ), mock.patch.object(ap, "_try_mootdx_server") as try_srv:

                def slow_probe(Quotes, server, deadline=None):
                    clock.set(clock._now + 1.0)
                    return None

                try_srv.side_effect = slow_probe
                got = ap._init_mootdx_client(deadline=3.0)
        self.assertIsNone(got)
        # 只探测了预算允许的 3 个镜像，而不是把 curated 列表全试一遍
        self.assertEqual(try_srv.call_count, 3)

    def test_scan_with_expired_deadline_probes_nothing(self):
        """deadline 已过期时不做任何探测，直接返回 None。"""
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(ap, "_try_mootdx_server") as try_srv:
            try_srv.side_effect = AssertionError("不应被调用")
            self.assertIsNone(ap._init_mootdx_client(deadline=-1.0))
            try_srv.assert_not_called()

    def test_scan_budget_default_does_not_break_fast_mirrors(self):
        """默认预算下，快速可用镜像仍被正常采用（行为不回归）。"""
        clock = FakeClock()
        client = FakeQuotes()
        with mock.patch.object(ap.time, "monotonic", side_effect=clock):
            with mock.patch.object(
                ap,
                "_try_mootdx_server",
                return_value=client,
            ) as try_srv:
                got = ap._init_mootdx_client()
        self.assertIs(got, client)
        self.assertEqual(try_srv.call_count, 1)

    # ------------------------------------------------------------------
    # 并发快速失败（扫描期间不阻塞锁）
    # ------------------------------------------------------------------

    def test_concurrent_caller_fast_fails_during_scan(self):
        """已有请求在扫描镜像时，并发调用直接返回 None 而不是排队等待。"""
        ap._mootdx_scan_in_progress = True
        with mock.patch.object(ap, "_init_mootdx_client") as init:
            self.assertIsNone(ap._get_mootdx_client())
            init.assert_not_called()

    def test_get_clears_scan_flag_and_caches_on_success(self):
        """扫描结束后标志复位，成功结果被缓存，后续调用不再扫描。"""
        client = FakeQuotes()
        with mock.patch.object(ap, "_init_mootdx_client", return_value=client):
            got = ap._get_mootdx_client()
        self.assertIs(got, client)
        self.assertIs(ap._mootdx_client, client)
        self.assertFalse(ap._mootdx_scan_in_progress)
        # 第二次调用直接命中缓存，不重新扫描
        with mock.patch.object(ap, "_init_mootdx_client") as init:
            self.assertIs(ap._get_mootdx_client(), client)
            init.assert_not_called()

    def test_get_clears_scan_flag_on_failure(self):
        """扫描失败后标志复位并进入冷却期（不永久卡住后续请求）。"""
        with mock.patch.object(ap, "_init_mootdx_client", return_value=None):
            self.assertIsNone(ap._get_mootdx_client())
        self.assertFalse(ap._mootdx_scan_in_progress)
        self.assertIsNotNone(ap._mootdx_init_failed_at)

    # ------------------------------------------------------------------
    # 取数预算
    # ------------------------------------------------------------------

    def test_fetch_respects_budget_without_invalidating_client(self):
        """预算耗尽时快速返回空，且不摘除健康的缓存客户端。"""
        client = SymbolAwareClient({"600519": _kline_df()})
        ap._mootdx_client = client
        provider = object.__new__(ap.AStockDataProvider)
        with mock.patch.object(ap.time, "monotonic", return_value=999.0):
            result = provider._fetch_kline_mootdx("600519", "5", "", "", deadline=998.0)
        self.assertIsNone(result)
        # 预算内未发出任何 socket 请求
        self.assertEqual(client.calls, [])
        # 健康客户端保留
        self.assertIs(ap._mootdx_client, client)

    def test_fetch_returns_data_within_budget(self):
        """预算充足时正常返回数据，行为不回归。"""
        client = SymbolAwareClient({"600519": _kline_df()})
        ap._mootdx_client = client
        provider = object.__new__(ap.AStockDataProvider)
        with mock.patch.object(ap.time, "monotonic", return_value=999.0):
            result = provider._fetch_kline_mootdx(
                "600519", "5", "", "", deadline=1000.0
            )
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertIs(ap._mootdx_client, client)

    # ------------------------------------------------------------------
    # 分钟整体预算
    # ------------------------------------------------------------------

    def test_minute_data_expired_deadline_returns_none_fast(self):
        """分钟请求入口已超预算时快速返回空，不触碰任何数据源。"""
        provider = object.__new__(ap.AStockDataProvider)
        with mock.patch.object(ap.time, "monotonic", return_value=999.0):
            with mock.patch.object(provider, "_fetch_kline_mootdx") as mootdx:
                result = provider.fetch_minute_data(
                    "600519", "", "", period="5", deadline=998.0
                )
        self.assertIsNone(result)
        mootdx.assert_not_called()

    def test_minute_data_passes_shared_deadline_to_mootdx(self):
        """分钟整体预算作为同一 deadline 传给 mootdx 取数，覆盖整条链路。"""
        provider = object.__new__(ap.AStockDataProvider)
        clock = FakeClock(now=100.0)
        with mock.patch.object(ap.time, "monotonic", side_effect=clock):
            with mock.patch.object(
                provider, "_fetch_kline_mootdx", return_value=_kline_df()
            ) as mootdx:
                result = provider.fetch_minute_data("600519", "", "", period="5")
        self.assertIsNotNone(result)
        self.assertEqual(
            mootdx.call_args.kwargs["deadline"],
            100.0 + ap._MOOTDX_MINUTE_BUDGET,
        )

    def test_minute_data_fallback_honors_deadline(self):
        """mootdx 返回空且预算在回退前耗尽时，放弃东财回退直接返回空。"""
        provider = object.__new__(ap.AStockDataProvider)
        clock = FakeClock()

        def empty_then_expire(*args, **kwargs):
            clock.set(999.0)  # mootdx 阶段耗尽预算
            return pd.DataFrame()

        with mock.patch.object(
            ap.time, "monotonic", side_effect=clock
        ), mock.patch.object(ap, "eastmoney_kline") as em, mock.patch.object(
            provider, "_fetch_kline_mootdx", side_effect=empty_then_expire
        ):
            result = provider.fetch_minute_data(
                "600519", "", "", period="5", deadline=12.0
            )
        self.assertIsNone(result)
        # 预算耗尽后不再发起东财回退
        em.assert_not_called()


if __name__ == "__main__":
    unittest.main()
