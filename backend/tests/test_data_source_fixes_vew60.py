"""测试数据源取数链路三项修复（VEW-60）。

覆盖：
- mootdx 客户端关闭 pytdx/tdxpy 的 setup 握手（``need_setup=False``），否则公开
  镜像上 ``get_security_bars`` 恒返回空、镜像被逐个误判不可用；
- 共享 mootdx client 的取数路径加锁串行化（并发调用会静默丢数据），探针同锁；
- 取数链消费 source_monitor 的 eastmoney ``down`` 状态做快速熔断，跳过注定失败
  的重试等待。
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

import pandas as pd

from app.core.source_health import SourceHealthMonitor, source_monitor
from app.providers import astock_provider as ap
from app.providers.probes import probe_mootdx


class FakeTdxApi:
    """模拟 tdxpy 的 TdxHq_API：默认 need_setup=True，握手未关时取数返回空。"""

    def __init__(self, honor_setup: bool = True):
        self.need_setup = True
        self._honor_setup = honor_setup
        self.bars_calls = 0

    def bars(self, symbol, frequency, start, offset):
        self.bars_calls += 1
        if self._honor_setup and self.need_setup:
            # 握手响应错位后，真实镜像表现为「恒返回空」。
            return pd.DataFrame()
        return _kline_df(min(offset, 3))


class FakeQuotesClient:
    """模拟 mootdx ``Quotes``：``.client`` 是底层 TdxHq_API。"""

    def __init__(self, api: FakeTdxApi):
        self.client = api

    def bars(self, *args, **kwargs):
        return self.client.bars(
            kwargs.get("symbol"),
            kwargs.get("frequency"),
            kwargs.get("start", 0),
            kwargs.get("offset", 3),
        )


class FakeQuotes:
    """模拟 mootdx ``Quotes`` 模块，``factory`` 返回固定客户端。"""

    def __init__(self, client):
        self.factory = mock.Mock(return_value=client)


def _kline_df(n: int = 3) -> pd.DataFrame:
    dates = pd.date_range(end="2026-09-30", periods=n, freq="D")
    return pd.DataFrame(
        {
            "datetime": dates.strftime("%Y-%m-%d %H:%M:%S"),
            "open": [10.0] * n,
            "close": [10.0] * n,
            "high": [10.0] * n,
            "low": [9.0] * n,
            "volume": [1.0] * n,
            "amount": [1.0] * n,
        }
    )


class ConcurrencyDetectingClient:
    """记录 bars() 的最大并发进入数：>1 即说明共享 client 未串行化。"""

    def __init__(self, page: int = 5, delay: float = 0.01):
        self._page = page
        self._delay = delay
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0
        self.calls = 0

    def bars(self, symbol, frequency, start, offset):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            self.calls += 1
        try:
            time.sleep(self._delay)
            dates = pd.date_range(start="2026-09-01", periods=self._page, freq="D")
            return pd.DataFrame(
                {
                    "datetime": dates.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": [10.0] * self._page,
                    "close": [10.0] * self._page,
                    "high": [10.0] * self._page,
                    "low": [9.0] * self._page,
                    "volume": [1.0] * self._page,
                    "amount": [1.0] * self._page,
                }
            )
        finally:
            with self._lock:
                self._active -= 1


class SetupHandshakeTests(unittest.TestCase):
    """修复 1：关闭 setup 握手。"""

    def test_disables_need_setup(self):
        client = FakeQuotesClient(FakeTdxApi())
        ap._disable_tdx_setup_handshake(client, ("1.2.3.4", 7709))
        self.assertFalse(client.client.need_setup)

    def test_tolerates_missing_client_attribute(self):
        # 不抛异常即可（mootdx/tdxpy 版本差异下 client 结构可能不同）
        ap._disable_tdx_setup_handshake(object(), None)
        ap._disable_tdx_setup_handshake(mock.Mock(spec=[]), None)

    def test_tolerates_readonly_attribute(self):
        class Readonly:
            @property
            def need_setup(self):
                return True

        holder = mock.Mock()
        holder.client = Readonly()
        ap._disable_tdx_setup_handshake(holder, None)  # 不抛异常

    def test_mirror_rejected_before_fix_is_accepted_after(self):
        """镜像只在握手关闭后才返回 K 线；修复后应被采用而不是判为不可用。"""
        api = FakeTdxApi(honor_setup=True)
        client = FakeQuotesClient(api)
        got = ap._try_mootdx_server(FakeQuotes(client), ("1.2.3.4", 7709))
        self.assertIs(got, client)
        self.assertFalse(api.need_setup)
        self.assertGreater(api.bars_calls, 0)


class FetchSerializationTests(unittest.TestCase):
    """修复 2：共享 client 取数串行化。"""

    def setUp(self):
        self._saved_client = ap._mootdx_client
        self._saved_failed_at = ap._mootdx_init_failed_at
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None

    def tearDown(self):
        ap._mootdx_client = self._saved_client
        ap._mootdx_init_failed_at = self._saved_failed_at

    def test_concurrent_fetches_never_overlap(self):
        client = ConcurrencyDetectingClient()
        ap._mootdx_client = client
        provider = ap.AStockDataProvider()

        results: list[bool] = []
        lock = threading.Lock()

        def worker(code: str):
            df = provider._fetch_kline_mootdx(
                code, period="101", start_date="", end_date="", count=5
            )
            with lock:
                results.append(df is not None and not df.empty)

        threads = [
            threading.Thread(target=worker, args=(f"{i:06d}",)) for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(results), 8)
        self.assertTrue(all(results), f"部分标的取数为空: {results}")
        self.assertEqual(
            client.max_active, 1, "共享 client 的 bars() 发生了并发调用（会静默丢数据）"
        )

    def test_probe_shares_the_fetch_lock(self):
        """探针走同一 client，也必须持锁，否则会误判镜像故障并摘除客户端。"""
        client = ConcurrencyDetectingClient(delay=0.0)
        client.bars = mock.Mock(return_value=_kline_df(3))
        ap._mootdx_client = client

        done = threading.Event()

        def run_probe():
            probe_mootdx()
            done.set()

        with ap._mootdx_fetch_lock:
            t = threading.Thread(target=run_probe)
            t.start()
            # 锁被主线程持有期间，探针不应发起 bars() 调用
            time.sleep(0.2)
            self.assertEqual(client.bars.call_count, 0)

        self.assertTrue(done.wait(timeout=10))
        t.join(timeout=10)
        self.assertEqual(client.bars.call_count, 1)


class IsDownTests(unittest.TestCase):
    """修复 3 的查询原语。"""

    def setUp(self):
        self.monitor = SourceHealthMonitor()

    def test_unknown_is_not_down(self):
        self.assertFalse(self.monitor.is_down("eastmoney"))

    def test_failure_marks_down_and_success_recovers(self):
        self.monitor.record_attempt("eastmoney", ok=False, error="boom")
        self.assertTrue(self.monitor.is_down("eastmoney"))
        self.monitor.record_attempt("eastmoney", ok=True, duration_ms=5.0)
        self.assertFalse(self.monitor.is_down("eastmoney"))


class EastmoneyBreakerTests(unittest.TestCase):
    """修复 3：eastmoney 为 down 时跳过重试，直接回退 Tushare。"""

    def setUp(self):
        source_monitor.reset()
        self.provider = ap.AStockDataProvider()

    def tearDown(self):
        source_monitor.reset()

    def _run(self):
        with mock.patch.object(
            ap.AStockDataProvider, "_fetch_kline_mootdx", return_value=None
        ), mock.patch.object(
            ap.AStockDataProvider,
            "_fetch_daily_tushare",
            return_value=_kline_df(3),
        ), mock.patch(
            "app.providers.astock_provider.eastmoney_kline"
        ) as em, mock.patch(
            "app.providers.astock_provider.time.sleep"
        ) as sleep:
            result = self.provider.fetch_daily_data_with_meta(
                "000001", "20260901", "20260930"
            )
        return result, em, sleep

    def test_breaker_open_skips_eastmoney_and_retry_sleep(self):
        source_monitor.record_attempt("eastmoney", ok=False, error="RemoteDisconnected")
        result, em, sleep = self._run()
        self.assertEqual(em.call_count, 0, "已知 down 时不应再请求东财")
        sleep.assert_not_called()
        self.assertEqual(result.provenance.source, "tushare")
        self.assertIsNotNone(result.df)

    def test_breaker_closed_still_uses_eastmoney(self):
        source_monitor.record_attempt("eastmoney", ok=True, duration_ms=10.0)
        with mock.patch.object(
            ap.AStockDataProvider, "_fetch_kline_mootdx", return_value=None
        ), mock.patch.object(
            ap.AStockDataProvider, "_fetch_daily_tushare", return_value=None
        ), mock.patch(
            "app.providers.astock_provider.eastmoney_kline",
            return_value=_kline_df(3),
        ) as em:
            result = self.provider.fetch_daily_data_with_meta(
                "000001", "20260901", "20260930"
            )
        self.assertEqual(em.call_count, 1)
        self.assertEqual(result.provenance.source, "eastmoney")


if __name__ == "__main__":
    unittest.main()
