"""测试数据源取数链路三项修复（VEW-60）及其评审整改项。

覆盖：
- mootdx 客户端关闭 pytdx/tdxpy 的 setup 握手（``need_setup=False``），否则公开
  镜像上 ``get_security_bars`` 恒返回空、镜像被逐个误判不可用；
- 共享 mootdx client 的取数路径加锁串行化（并发调用会静默丢数据），探针同锁；
- 取数链消费 source_monitor 的 eastmoney ``down`` 状态做快速熔断，跳过注定失败
  的重试等待；``is_down`` 以「连续失败达阈值」为口径（评审 M2）；
- 日线裸 ``end_date`` 上界含结束日当根、带时间的上界保持精确（评审 M1）；
- 探针 / 分钟取数等取数锁有上限，不会把整轮探针或前端超时预算架空；探针等锁超时
  只顺延本轮、不改写源状态（评审 M3/M4）。
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

import pandas as pd

from app.core.source_health import (
    EVENT_DEFERRED,
    SourceHealthMonitor,
    STATUS_UNKNOWN,
    source_monitor,
)
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


def _bars_df(timestamps: list[str], close: float = 1258.62) -> pd.DataFrame:
    """构造固定时间戳的 K 线（日线时间戳为当日 15:00:00）。"""
    n = len(timestamps)
    return pd.DataFrame(
        {
            "datetime": timestamps,
            "open": [close] * n,
            "close": [close] * n,
            "high": [close] * n,
            "low": [close * 0.99] * n,
            "volume": [1.0] * n,
            "amount": [1.0] * n,
        }
    )


class StaticBarsClient:
    """每次 ``bars()`` 都返回同一份固定 K 线的假 client（日期边界回归用）。"""

    def __init__(self, df: pd.DataFrame):
        self._df = df
        self.calls = 0

    def bars(self, symbol, frequency, start, offset):
        self.calls += 1
        return self._df.copy()


class _SharedClientTestCase(unittest.TestCase):
    """把模块级 mootdx client 替换成假 client 的公共基类。"""

    def setUp(self):
        self._saved_client = ap._mootdx_client
        self._saved_failed_at = ap._mootdx_init_failed_at
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None

    def tearDown(self):
        ap._mootdx_client = self._saved_client
        ap._mootdx_init_failed_at = self._saved_failed_at


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


class HandshakeAwareTdxApi:
    """按真实时序模拟 tdxpy：响应错位是**连接**的属性，不是标志位的属性。

    - ``connect()`` 时若 ``need_setup`` 仍为真，就发出 setup 握手包，这条连接从此
      响应错位（``bars()`` 恒返回空）；
    - 重连时 ``need_setup`` 已被置 False，新连接不再发握手，取数恢复正常。

    这正是真实 tdxpy 的形状：``need_setup`` 只在 ``connect()`` 内部被读取，错位状态
    挂在已经建好的那条连接上。于是「只置标志位、不重连」会稳定判负 —— 本假 client
    因此能区分「修好了」和「没修」，而上面基于标志位的 ``FakeTdxApi`` 两种都绿。
    """

    def __init__(self):
        self.need_setup = True
        self.ip = None
        self.port = None
        self.closed = True
        self.connect_calls = 0
        self.disconnect_calls = 0
        self._desynced = False

    def connect(self, ip, port, time_out=None):
        self.connect_calls += 1
        self.ip, self.port = ip, int(port)
        self.closed = False
        # 握手只在 need_setup 为真时发出；发出过的那条连接从此响应错位。
        self._desynced = bool(self.need_setup)

    def disconnect(self):
        self.disconnect_calls += 1
        self.closed = True

    def bars(self, symbol, frequency, start, offset):
        if self._desynced:
            return pd.DataFrame()
        return _kline_df(min(offset, 3))


class HandshakeQuotes:
    """``Quotes.factory`` 的替身：构造即建连，与 ``StdQuotes.__init__`` 一致。"""

    def __init__(self, api: HandshakeAwareTdxApi):
        self._api = api

    def factory(self, **kwargs):
        server = kwargs.get("server")
        if server is not None:
            self._api.connect(server[0], server[1], kwargs.get("timeout"))
        return FakeQuotesClient(self._api)


class HandshakeReconnectTests(unittest.TestCase):
    """修复 1 的时序：置位后必须重连，否则错位的那条连接照旧取不到 K 线（评审 ②）。"""

    def test_reconnect_clears_desynced_session(self):
        api = HandshakeAwareTdxApi()
        got = ap._try_mootdx_server(HandshakeQuotes(api), ("1.2.3.4", 7709))
        self.assertIsNotNone(got, "重连后应从干净会话取到 K 线")
        self.assertFalse(api.need_setup)
        self.assertEqual(api.connect_calls, 2, "构造建连 1 次 + 关闭握手后重连 1 次")
        self.assertEqual(api.disconnect_calls, 1)

    def test_flag_only_without_reconnect_stays_rejected(self):
        """反向对照：只置标志位、不重连（修复前的形状）必须仍然判负。

        这条用例是上面那条的证伪对照：实现若退化成「只置位」，两条一起红，所以
        「修复」与 VEW-60 的失败模式在测试层面是可区分的。
        """
        api = HandshakeAwareTdxApi()
        quotes = HandshakeQuotes(api)

        def flag_only(client, server, timeout=None):
            client.client.need_setup = False

        with mock.patch.object(ap, "_disable_tdx_setup_handshake", flag_only):
            got = ap._try_mootdx_server(quotes, ("1.2.3.4", 7709))
        self.assertIsNone(got, "错位的连接不重连就取不到 K 线，必须判负")
        self.assertEqual(api.connect_calls, 1)

    def test_reconnect_honors_caller_timeout(self):
        """重连的建连超时由调用方给：探测期传探测超时，默认才是取数用的 5s（评审 ③）。"""
        for expected, kwargs in (
            (ap._MOOTDX_PROBE_TIMEOUT, {"timeout": ap._MOOTDX_PROBE_TIMEOUT}),
            (ap._MOOTDX_CONNECT_TIMEOUT, {}),
        ):
            with self.subTest(expected=expected):
                api = HandshakeAwareTdxApi()
                api.connect("1.1.1.1", 7709)  # 模拟 StdQuotes.__init__ 的构造期建连
                api.connect = mock.Mock(wraps=api.connect)
                ap._disable_tdx_setup_handshake(
                    FakeQuotesClient(api), ("1.2.3.4", 7709), **kwargs
                )
                self.assertEqual(api.connect.call_count, 1)
                self.assertEqual(api.connect.call_args.kwargs["time_out"], expected)
                # 重连用的是 api 自己记着的 ip/port，不是传进来的 server
                self.assertEqual(api.connect.call_args.args[:2], ("1.1.1.1", 7709))


class FetchSerializationTests(_SharedClientTestCase):
    """修复 2：共享 client 取数串行化。"""

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
    """修复 3 的查询原语；口径为「连续失败达阈值」（评审 M2）。"""

    def setUp(self):
        self.monitor = SourceHealthMonitor(fail_threshold=3)

    def test_unknown_is_not_down(self):
        self.assertFalse(self.monitor.is_down("eastmoney"))

    def test_single_failure_does_not_trip_the_breaker(self):
        # 单次失败可能只是该标的无数据 / 一次抖动，不应让取数链放弃整个源
        self.monitor.record_attempt("eastmoney", ok=False, error="boom")
        self.assertFalse(self.monitor.is_down("eastmoney"))
        self.monitor.record_attempt("eastmoney", ok=False, error="boom")
        self.assertFalse(self.monitor.is_down("eastmoney"))

    def test_consecutive_failures_reaching_threshold_trip_the_breaker(self):
        for _ in range(3):
            self.monitor.record_attempt("eastmoney", ok=False, error="boom")
        self.assertTrue(self.monitor.is_down("eastmoney"))

    def test_success_resets_the_failure_streak(self):
        for _ in range(3):
            self.monitor.record_attempt("eastmoney", ok=False, error="boom")
        self.assertTrue(self.monitor.is_down("eastmoney"))
        self.monitor.record_attempt("eastmoney", ok=True, duration_ms=5.0)
        self.assertFalse(self.monitor.is_down("eastmoney"))
        # 计数已清零：再失败两次仍不到阈值
        self.monitor.record_attempt("eastmoney", ok=False, error="boom")
        self.monitor.record_attempt("eastmoney", ok=False, error="boom")
        self.assertFalse(self.monitor.is_down("eastmoney"))


class EastmoneyBreakerTests(unittest.TestCase):
    """修复 3：eastmoney 为 down 时跳过重试，直接回退 Tushare。"""

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
        self._mark_eastmoney_down()
        result, em, sleep = self._run()
        self.assertEqual(em.call_count, 0, "已知 down 时不应再请求东财")
        sleep.assert_not_called()
        self.assertEqual(result.provenance.source, "tushare")
        self.assertIsNotNone(result.df)

    def test_breaker_stays_closed_below_failure_threshold(self):
        # 只失败一两次不熔断：仍按原逻辑重试东财
        self._mark_eastmoney_down(times=1)
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


class EndDateBoundTests(_SharedClientTestCase):
    """评审 M1：日线裸 ``end_date`` 上界必须含结束日当根。

    日线 bar 的时间戳是当日 15:00:00，原先 ``datetime <= Timestamp("2026-09-30")``
    （即当日 00:00）会把结束日整根截掉，既少一根 bar，也让 ``_coverage_gap`` 把
    「其实覆盖到了」误判成 ``gap=True``。
    """

    _DAILY = [
        "2026-09-28 15:00:00",
        "2026-09-29 15:00:00",
        "2026-09-30 15:00:00",
    ]

    def _fetch(self, start_date: str, end_date: str, period: str = "101"):
        client = StaticBarsClient(_bars_df(self._DAILY))
        ap._mootdx_client = client
        return ap.AStockDataProvider()._fetch_kline_mootdx(
            "600519",
            period=period,
            start_date=start_date,
            end_date=end_date,
            count=5,
        )

    def test_bare_end_date_includes_the_end_day_bar(self):
        df = self._fetch("", "2026-09-30")
        self.assertIsNotNone(df)
        self.assertEqual(list(df["datetime"])[-1], "2026-09-30 15:00:00")
        self.assertEqual(len(df), 3)

    def test_bare_end_date_still_excludes_later_bars(self):
        # 放宽只到当日结束，不会顺带把后面的 bar 放进来
        df = self._fetch("", "2026-09-29")
        self.assertIsNotNone(df)
        self.assertEqual(list(df["datetime"])[-1], "2026-09-29 15:00:00")
        self.assertEqual(len(df), 2)

    def test_time_bearing_end_date_stays_exact(self):
        # 分钟链路传 "YYYY-MM-DD HH:MM:SS"：上界带时间分量时必须保持精确比较，
        # 不能被 normalize 放宽到当日结束（否则 10:05 与 15:00 会漏进来）
        client = StaticBarsClient(
            _bars_df(
                [
                    "2026-09-30 09:35:00",
                    "2026-09-30 10:05:00",
                    "2026-09-30 15:00:00",
                ]
            )
        )
        ap._mootdx_client = client
        df = ap.AStockDataProvider()._fetch_kline_mootdx(
            "600519",
            period="5",
            start_date="2026-09-30 09:30:00",
            end_date="2026-09-30 10:00:00",
            count=5,
        )
        self.assertIsNotNone(df)
        self.assertEqual(list(df["datetime"]), ["2026-09-30 09:35:00"])

    def test_full_coverage_no_longer_reports_a_gap(self):
        # 验收项 1 的回归：请求区间与实际数据完全重合时，gap 必须为 False。
        # 修复前结束日被截掉（actual_end=2026-09-29 < req_end），gap 恒为 True。
        ap._mootdx_client = StaticBarsClient(_bars_df(self._DAILY))
        result = ap.AStockDataProvider().fetch_daily_data_with_meta(
            "600519", "20260928", "20260930"
        )
        self.assertEqual(result.provenance.source, "mootdx")
        self.assertEqual(result.provenance.actual_start, "2026-09-28")
        self.assertEqual(result.provenance.actual_end, "2026-09-30")
        self.assertFalse(result.provenance.gap)
        self.assertEqual(result.df["close"].iloc[-1], 1258.62)


class FetchLockBudgetTests(_SharedClientTestCase):
    """评审 M3/M4：等取数锁有上限，不架空调用方自己的墙钟预算。"""

    def setUp(self):
        super().setUp()
        source_monitor.reset()

    def tearDown(self):
        source_monitor.reset()
        super().tearDown()

    def test_probe_defers_on_lock_timeout_without_touching_source_status(self):
        """等锁超时只是「本轮没测到」，不得改写成 skipped（VEW-60 复审）。"""
        client = StaticBarsClient(_bars_df(["2026-09-30 15:00:00"]))
        client.bars = mock.Mock(return_value=_bars_df(["2026-09-30 15:00:00"]))
        ap._mootdx_client = client

        # 上一轮探针成功 → 已知状态为 up
        source_monitor.record_attempt("mootdx", ok=True, duration_ms=12.0)
        before = source_monitor.metrics()["sources"]["mootdx"]

        start = time.monotonic()
        with mock.patch.object(ap, "_MOOTDX_PROBE_LOCK_TIMEOUT", 0.2):
            with ap._mootdx_fetch_lock:
                result = probe_mootdx()
        elapsed = time.monotonic() - start

        self.assertEqual(result.status, STATUS_UNKNOWN)
        self.assertEqual(result.duration_ms, 0)
        client.bars.assert_not_called()
        # 超时即返回，而不是无限期等锁
        self.assertLess(elapsed, 3.0)

        after = source_monitor.metrics()["sources"]["mootdx"]
        self.assertEqual(after["status"], "up", "顺延不得改写源状态")
        self.assertEqual(after["total_requests"], before["total_requests"])
        self.assertEqual(after["total_skipped"], before["total_skipped"])
        # 仍然留痕：事件流里能看到本轮顺延，且不是 skipped 事件
        events = source_monitor.events_recent(5)
        self.assertTrue(
            any(
                e["event_type"] == EVENT_DEFERRED and e["source"] == "mootdx"
                for e in events
            )
        )
        self.assertFalse(any(e["event_type"] == "skipped" for e in events))

    def test_deferred_probe_keeps_source_in_overall_status(self):
        """唯一异常源顺延后，总体健康度不得从 unhealthy 翻成 ok。"""
        ap._mootdx_client = StaticBarsClient(_bars_df(["2026-09-30 15:00:00"]))

        for _ in range(3):
            source_monitor.record_attempt("mootdx", ok=False, error="mirror down")
        self.assertEqual(source_monitor.overall_status(), "unhealthy")

        with mock.patch.object(ap, "_MOOTDX_PROBE_LOCK_TIMEOUT", 0.2):
            with ap._mootdx_fetch_lock:
                result = probe_mootdx()

        self.assertEqual(result.status, STATUS_UNKNOWN)
        self.assertEqual(
            source_monitor.metrics()["sources"]["mootdx"]["status"], "down"
        )
        self.assertEqual(source_monitor.overall_status(), "unhealthy")

    def test_minute_fetch_gives_up_when_lock_exceeds_budget(self):
        client = StaticBarsClient(_bars_df(["2026-09-30 15:00:00"]))
        ap._mootdx_client = client

        start = time.monotonic()
        with ap._mootdx_fetch_lock:
            df = ap.AStockDataProvider()._fetch_kline_mootdx(
                "600519",
                period="5",
                start_date="",
                end_date="",
                count=5,
                deadline=time.monotonic() + 0.3,
            )
        elapsed = time.monotonic() - start

        self.assertIsNone(df)
        self.assertEqual(client.calls, 0, "未拿到锁就不应发起取数")
        self.assertLess(elapsed, 3.0, "等锁应受 deadline 约束，而不是无限期阻塞")

    def test_daily_fetch_without_deadline_waits_for_the_lock(self):
        # 日线 / CYQ 路径没有墙钟预算：保持无限期等待（VEW-54 的既有取舍）
        client = StaticBarsClient(_bars_df(["2026-09-30 15:00:00"]))
        ap._mootdx_client = client
        provider = ap.AStockDataProvider()

        out: list = []

        def worker():
            out.append(
                provider._fetch_kline_mootdx(
                    "600519", period="101", start_date="", end_date="", count=5
                )
            )

        with ap._mootdx_fetch_lock:
            t = threading.Thread(target=worker)
            t.start()
            time.sleep(0.2)
            self.assertEqual(client.calls, 0)
        t.join(timeout=10)
        self.assertEqual(len(out), 1)
        self.assertIsNotNone(out[0])
        self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
