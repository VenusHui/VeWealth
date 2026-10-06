"""测试 mootdx 分钟链路快速失败（VEW-54 / VEW-62）。

镜像池全挂时 _init_mootdx_client 会并发竞速可达候选 + 兜底试一次配置默认，每个
镜像含建连 + 2 次探针取数（探测期受 _MOOTDX_PROBE_TIMEOUT 与单次重试兜底），最坏
仍可把请求挂起数十秒、超过前端 15s 超时。本用例验证四类快速失败：

- 镜像探测受墙钟预算约束：预算耗尽即不再发起探测（含竞速与配置默认兜底）；
- 探测期把 tdxpy 的重试退避收敛成「只重试一次」并在采用后恢复原值（VEW-62）；
- 并发请求在扫描进行中直接快速返回 None，而不是排队阻塞在锁上等完整扫描；
- mootdx 取数 / 分钟整体请求受预算约束，超时返回空 K 线且不摘除健康缓存客户端。
"""

from __future__ import annotations

import threading
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


def _kline_df(n: int = 1, start: int = 0) -> pd.DataFrame:
    base = pd.Timestamp("2026-09-23 09:00:00")
    return pd.DataFrame(
        {
            "datetime": [
                (base + pd.Timedelta(seconds=i + start)).strftime("%Y-%m-%d %H:%M:%S")
                for i in range(n)
            ],
            "open": [1.0] * n,
            "close": [1.5] * n,
            "high": [1.6] * n,
            "low": [0.9] * n,
            "volume": [100] * n,
            "amount": [1000.0] * n,
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
        # TCP 可达性预筛默认放行（VEW-62）：本文件关注预算与快速失败，样例 IP 在
        # 真实预筛下会被逐个判死（还会引入真实网络等待），故在此放行。
        patcher = mock.patch.object(
            ap,
            "_filter_reachable_servers",
            side_effect=lambda servers, *args, **kwargs: list(servers),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_scan_skips_probing_when_budget_exhausted(self):
        """预算耗尽后连竞速都不发起，直接返回 None。"""
        clock = FakeClock()
        with mock.patch.object(ap.time, "monotonic", side_effect=clock):
            with mock.patch.object(
                ap, "_mootdx_scan_due", return_value=False
            ), mock.patch.object(
                ap, "_probe_mirrors_concurrently"
            ) as race, mock.patch.object(
                ap, "_try_mootdx_server"
            ) as try_srv:
                self.assertIsNone(ap._init_mootdx_client(deadline=0.0))
        race.assert_not_called()
        try_srv.assert_not_called()

    def test_scan_with_expired_deadline_probes_nothing(self):
        """deadline 已过期时不做任何探测，直接返回 None。"""
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(
            ap, "_probe_mirrors_concurrently"
        ) as race, mock.patch.object(
            ap, "_try_mootdx_server"
        ) as try_srv:
            try_srv.side_effect = AssertionError("不应被调用")
            self.assertIsNone(ap._init_mootdx_client(deadline=-1.0))
            try_srv.assert_not_called()
            race.assert_not_called()

    def test_scan_budget_default_does_not_break_fast_mirrors(self):
        """默认预算下，竞速挑出的可用镜像仍被正常采用（行为不回归）。"""
        clock = FakeClock()
        client = FakeQuotes()
        with mock.patch.object(ap.time, "monotonic", side_effect=clock):
            with mock.patch.object(
                ap, "_mootdx_scan_due", return_value=False
            ), mock.patch.object(
                ap,
                "_probe_mirrors_concurrently",
                return_value=(("1.1.1.1", 7709), client),
            ) as race:
                got = ap._init_mootdx_client()
        self.assertIs(got, client)
        self.assertEqual(race.call_count, 1)
        # 竞速拿到的是整个剩余预算（默认 6s），而不是无限等待
        self.assertEqual(race.call_args.kwargs["deadline"], ap._MOOTDX_SCAN_BUDGET)

    def test_scan_capped_by_budget_even_with_wider_deadline(self):
        """整体 deadline 更宽时, 扫描自身仍受 _MOOTDX_SCAN_BUDGET 兜底（评审 F3）。"""
        clock = FakeClock()
        with mock.patch.object(ap.time, "monotonic", side_effect=clock):
            with mock.patch.object(
                ap, "_mootdx_scan_due", return_value=False
            ), mock.patch.object(
                ap, "_probe_mirrors_concurrently", return_value=None
            ) as race, mock.patch.object(
                ap, "_try_mootdx_server", return_value=None
            ):
                # 调用方给出 1000s 宽 deadline，但扫描阶段仍按 6s 预算收口
                self.assertIsNone(ap._get_mootdx_client(deadline=1000.0))
        self.assertEqual(race.call_args.kwargs["deadline"], ap._MOOTDX_SCAN_BUDGET)
        self.assertFalse(ap._mootdx_scan_in_progress)
        self.assertIsNotNone(ap._mootdx_init_failed_at)

    # ------------------------------------------------------------------
    # 探测模式（VEW-62）
    # ------------------------------------------------------------------

    def test_probe_bounds_retry_strategy_and_restores_it_on_success(self):
        """探测期把 tdxpy 的退避换成「只重试一次」，采用后恢复原策略。

        tdxpy 默认策略 [0.1,0.5,1,2] 会在不回包的镜像上重连重试 4 次，单个候选实测
        52.3s；但完全不重试又会误杀可用镜像（115.238.90.165 约半数情况丢首个请求），
        所以探测期保留 auto_retry、只把退避收敛成一次。取数阶段（同一 client 长连
        复用）必须恢复原策略，保留 tdxpy 的自愈行为。
        """
        original_strategy = object()

        class RetryAwareApi:
            def __init__(self):
                self.auto_retry = True
                self.retry_strategy = original_strategy
                self.seen = []

        class RetryAwareClient:
            def __init__(self, df):
                self.client = RetryAwareApi()
                self._df = df

            def bars(self, *args, **kwargs):
                self.client.seen.append(
                    (self.client.auto_retry, self.client.retry_strategy)
                )
                return self._df

        client = RetryAwareClient(_kline_df())
        quotes = mock.Mock()
        quotes.factory = mock.Mock(return_value=client)
        got = ap._try_mootdx_server(quotes, ("1.1.1.1", 7709))
        self.assertIs(got, client)
        # 两次探针取数都在「关掉库内重试 + 空退避表」的探测模式下进行（VEW-70）：
        # 一次 bars() 必须就是一次连接尝试，重连重试由探测层显式做并计入预算
        self.assertEqual(
            client.client.seen,
            [(False, ap._ProbeRetryStrategy), (False, ap._ProbeRetryStrategy)],
        )
        # 探测通过后恢复原策略，长连取数客户端保持自愈行为
        self.assertIs(client.client.retry_strategy, original_strategy)
        self.assertTrue(client.client.auto_retry)

    def test_probe_shortens_socket_timeout_and_restores_it(self):
        """探测期把 socket 超时收到 _MOOTDX_PROBE_TIMEOUT，采用后恢复原值。"""

        class Sock:
            def __init__(self):
                self.timeout = 5
                self.seen = []

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.seen.append(value)
                self.timeout = value

        class Client:
            def __init__(self):
                self.client = mock.Mock(client=Sock())

            def bars(self, *args, **kwargs):
                return _kline_df()

        client = Client()
        quotes = mock.Mock()
        quotes.factory = mock.Mock(return_value=client)
        self.assertIs(ap._try_mootdx_server(quotes, ("1.1.1.1", 7709)), client)
        # 先收紧到探测超时，采用后恢复原来的 5s
        self.assertEqual(client.client.client.seen, [ap._MOOTDX_PROBE_TIMEOUT, 5])

    def test_probe_closes_client_when_mirror_rejected(self):
        """镜像被拒（返回空 K 线）时关掉探测用的 client，不留悬挂连接。"""

        class Client:
            def __init__(self):
                self.closed = False

            def bars(self, *args, **kwargs):
                return pd.DataFrame()

            def close(self):
                self.closed = True

        client = Client()
        quotes = mock.Mock()
        quotes.factory = mock.Mock(return_value=client)
        self.assertIsNone(ap._try_mootdx_server(quotes, ("1.1.1.1", 7709)))
        self.assertTrue(client.closed)

    def test_probe_tolerates_client_without_tuning_hooks(self):
        """测试替身 / 旧版 tdxpy 没有 client / retry_strategy 时不影响探测。"""

        class BareClient:
            def bars(self, *args, **kwargs):
                return _kline_df()

        client = BareClient()
        quotes = mock.Mock()
        quotes.factory = mock.Mock(return_value=client)
        self.assertIs(ap._try_mootdx_server(quotes, ("1.1.1.1", 7709)), client)

    def test_probe_restores_fetch_timeout_when_socket_reports_none(self):
        """socket 报 None（无超时）时也要恢复成取数超时，不能静默停在探测超时上。

        原来的恢复条件是 ``saved.get("timeout") is not None``，而探测期建连用的是
        探测超时，照抄现值 + 用 None 当哨兵会让被采用的镜像永久停在 2.5s（评审 ⑦）。
        """

        class Sock:
            def __init__(self):
                self.timeout = None
                self.seen = []

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.seen.append(value)
                self.timeout = value

        class Client:
            def __init__(self):
                self.client = mock.Mock(client=Sock())

            def bars(self, *args, **kwargs):
                return _kline_df()

        client = Client()
        quotes = mock.Mock()
        quotes.factory = mock.Mock(return_value=client)
        self.assertIs(ap._try_mootdx_server(quotes, ("1.1.1.1", 7709)), client)
        self.assertEqual(
            client.client.client.seen,
            [ap._MOOTDX_PROBE_TIMEOUT, ap._MOOTDX_CONNECT_TIMEOUT],
        )

    def test_probe_build_timeout_is_bounded_and_clamped(self):
        """竞速里串行的建连也受探测超时约束，并按剩余预算夹紧、保底（评审 ③）。"""
        client = FakeQuotes()
        clock = FakeClock(now=100.0)
        with mock.patch.object(
            ap.time, "monotonic", side_effect=clock
        ), mock.patch.object(
            ap, "_build_mootdx_client", return_value=client
        ) as build, mock.patch.object(
            ap, "_mirror_serves_bars", return_value=True
        ):
            ap._probe_candidate(mock.Mock(), ("1.1.1.1", 7709), None, threading.Lock())
            self.assertEqual(
                build.call_args.kwargs["timeout"],
                ap._MOOTDX_PROBE_TIMEOUT,
                "无 deadline 时用探测超时，而不是取数用的 5s",
            )
            ap._probe_candidate(mock.Mock(), ("1.1.1.1", 7709), 101.0, threading.Lock())
            self.assertEqual(build.call_args.kwargs["timeout"], 1.0)
            ap._probe_candidate(
                mock.Mock(), ("1.1.1.1", 7709), 100.05, threading.Lock()
            )
            self.assertEqual(
                build.call_args.kwargs["timeout"], ap._MOOTDX_PROBE_MIN_TIMEOUT
            )

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

    def test_fetch_without_deadline_paginates_unbounded(self):
        """deadline=None（日线/CYQ 路径）时不做取数预算截断：慢页也完整分页（评审 F1）。"""
        clock = FakeClock()

        class PagingClient:
            """每页返回 800 行并推进时钟，模拟慢镜像页。"""

            def __init__(self):
                self.calls = 0
                self._start = 0

            def bars(self, *args, **kwargs):
                self.calls += 1
                clock.set(clock._now + 4.0)  # 每页 4s，3 页累计 12s
                df = _kline_df(800, start=self._start)
                self._start += 800  # 各页 datetime 不重叠，去重后仍 2400 行
                return df

        client = PagingClient()
        ap._mootdx_client = client
        provider = object.__new__(ap.AStockDataProvider)
        with mock.patch.object(ap.time, "monotonic", side_effect=clock):
            # count=2400 -> 每页 800，需 3 页；累计 12s 超过原有 8s 取数预算也不截断
            result = provider._fetch_kline_mootdx("600519", "5", "", "", count=2400)
        self.assertEqual(client.calls, 3)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2400)
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

    def test_minute_fallback_http_timeout_clamped_to_remaining_budget(self):
        """回退的单次 HTTP 超时按剩余预算夹紧，而不是恒为 15s（评审 ①）。

        不夹紧时最坏是「8s 扫描 + 15s 首次回退 ≈ 23s」，12s 预算与前端 15s 超时都
        拦不住 —— 这正是 VEW-54 要防的形状。
        """
        provider = object.__new__(ap.AStockDataProvider)
        clock = FakeClock(now=100.0)
        with mock.patch.object(
            ap.time, "monotonic", side_effect=clock
        ), mock.patch.object(ap.time, "sleep"), mock.patch.object(
            ap, "eastmoney_kline", return_value=None
        ) as em, mock.patch.object(
            ap, "tencent_minute_bars", return_value=[]
        ), mock.patch.object(
            provider, "_fetch_kline_mootdx", return_value=None
        ):
            result = provider.fetch_minute_data(
                "600519", "", "", period="5", deadline=106.0
            )
        self.assertIsNone(result)
        self.assertEqual(em.call_args.kwargs["timeout"], 6.0)

    def test_minute_fallback_http_timeout_capped_at_default(self):
        """预算充裕时不放大超时：仍以默认 15s 为上限。"""
        provider = object.__new__(ap.AStockDataProvider)
        clock = FakeClock(now=100.0)
        with mock.patch.object(
            ap.time, "monotonic", side_effect=clock
        ), mock.patch.object(ap.time, "sleep"), mock.patch.object(
            ap, "eastmoney_kline", return_value=None
        ) as em, mock.patch.object(
            ap, "tencent_minute_bars", return_value=[]
        ), mock.patch.object(
            provider, "_fetch_kline_mootdx", return_value=None
        ):
            result = provider.fetch_minute_data(
                "600519", "", "", period="5", deadline=1000.0
            )
        self.assertIsNone(result)
        self.assertEqual(em.call_args.kwargs["timeout"], ap._EASTMONEY_FALLBACK_TIMEOUT)

    def test_minute_fallback_hanging_http_stays_within_budget(self):
        """回退卡满自己的超时也不越预算：夹紧后一次就用完，退避不再发生（评审 ①）。"""
        provider = object.__new__(ap.AStockDataProvider)
        clock = FakeClock(now=100.0)

        def hanging(**kwargs):
            # 模拟东财把这次请求挂满它拿到的超时
            clock.set(clock._now + kwargs["timeout"])
            return None

        with mock.patch.object(
            ap.time, "monotonic", side_effect=clock
        ), mock.patch.object(ap.time, "sleep") as sleeper, mock.patch.object(
            ap, "eastmoney_kline", side_effect=hanging
        ), mock.patch.object(
            ap, "tencent_minute_bars", return_value=[]
        ), mock.patch.object(
            provider, "_fetch_kline_mootdx", return_value=None
        ):
            result = provider.fetch_minute_data(
                "600519", "", "", period="5", deadline=112.0
            )
        self.assertIsNone(result)
        # 首次回退被夹到 12s，正好用完预算；退避会把总耗时顶出预算，故不再 sleep
        self.assertEqual(clock._now, 112.0)
        sleeper.assert_not_called()

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
