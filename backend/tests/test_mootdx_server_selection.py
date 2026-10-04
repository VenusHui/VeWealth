"""测试 mootdx 服务端镜像选择逻辑（VEW-36 / VEW-55 / VEW-62）。

_manage_mootdx_server 的关键行为是「连着通不算数，必须真的能取到 K 线」：
- 镜像若忽略 TCP 握手但返回空 K 线，应被跳过、尝试下一个；
- 第一个能取到有效 K 线的镜像被采用；
- _init_mootdx_client 在 curated 全部失败后执行有界公开镜像扫描（VEW-55），
  再回退到配置默认，最后才返回 None；
- settings.MOOTDX_SERVERS 可覆盖内置 curated 列表，无需改代码换镜像（VEW-55）；
  settings.MOOTDX_EXTRA_SERVERS 则是在保留内置列表的前提下追加（VEW-62）；
- 所有候选先过一次并发 TCP 可达性预筛，剪掉黑洞候选后再由 _probe_mirrors_concurrently
  并发竞速做完整的 K 线校验（VEW-62）—— 预筛与竞速本身的行为分别见
  MootdxReachabilityFilterTests 与 MootdxConcurrentProbeTests。
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

import pandas as pd

from app.providers import astock_provider as ap


class FakeClient:
    """模拟一个 mootdx 客户端，bars() 行为可控。

    ``bars`` 为 DataFrame 时所有周期都返回它；为 callable 时按 frequency 参数返回。
    """

    def __init__(self, bars):
        self._bars = bars

    def bars(self, *args, **kwargs):
        if callable(self._bars):
            return self._bars(kwargs.get("frequency"))
        return self._bars


class FakeQuotes:
    """模拟 mootdx Quotes，factory 返回可控 client。"""

    def __init__(self, client):
        self._client = client
        self.factory = mock.Mock(return_value=client)


def _nonempty_df() -> pd.DataFrame:
    return pd.DataFrame({"open": [1.0, 2.0, 3.0], "close": [1.0, 2.0, 3.0]})


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


def _daily_only_df(frequency: int | None) -> pd.DataFrame:
    """仅日线（frequency=4）返回数据，5 分钟（frequency=0）返回空。"""
    return _nonempty_df() if frequency == 4 else _empty_df()


class MootdxServerSelectionTests(unittest.TestCase):
    def setUp(self):
        # 重置模块级状态，避免跨用例泄漏
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None
        ap._mootdx_discovered_server = None
        ap._mootdx_last_scan_at = None
        ap._mootdx_scan_cursor = 0
        ap._mootdx_scan_in_progress = False
        # TCP 可达性预筛默认放行（VEW-62）：本文件关注候选顺序与选择逻辑，预筛
        # 自身的行为（剪枝、顺序、并发）由 MootdxReachabilityFilterTests 覆盖。
        # 不放行的话，用例里的样例 IP 会被真实 TCP 连接逐个判死，什么都测不到。
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

    def test_init_adopts_mirror_returned_by_race(self):
        """竞速挑出的可用镜像被采用，可达候选整池一次交给竞速。"""
        client = FakeClient(_nonempty_df())
        curated = ap._curated_mootdx_servers()
        second = curated[1]
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=(second, client)
        ) as race:
            got = ap._init_mootdx_client()
        self.assertIs(got, client)
        # 候选一次性全交给竞速（并发），而不是逐个串行探测
        self.assertEqual(race.call_count, 1)
        self.assertEqual(race.call_args[0][1], curated)
        self.assertEqual(ap._mootdx_discovered_server, second)

    def test_settings_override_replaces_curated_list(self):
        """MOOTDX_SERVERS 配置优先于内置 curated 列表。"""
        client = FakeClient(_nonempty_df())
        with mock.patch.object(
            ap.settings, "MOOTDX_SERVERS", "8.8.8.8:7709,9.9.9.9"
        ), mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=(("9.9.9.9", 7709), client)
        ) as race:
            got = ap._init_mootdx_client()
        self.assertIs(got, client)
        # 只竞速配置镜像，不碰内置列表
        self.assertEqual(race.call_args[0][1], [("8.8.8.8", 7709), ("9.9.9.9", 7709)])

    def test_init_falls_back_to_config_default(self):
        """curated 全部无数据时回退到配置默认（server=None）。"""
        default_client = FakeClient(_nonempty_df())
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=None
        ), mock.patch.object(
            ap, "_try_mootdx_server", return_value=default_client
        ) as try_srv:
            got = ap._init_mootdx_client()
        self.assertIs(got, default_client)
        # 竞速整池失败后，落到配置默认这一条兜底路径
        try_srv.assert_called_once()
        self.assertIsNone(try_srv.call_args[0][1])

    def test_init_returns_none_when_all_mirrors_fail(self):
        """全部镜像均失败时返回 None（扫描被禁用）。"""
        with mock.patch.object(ap.settings, "MOOTDX_SCAN_LIMIT", 0), mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=None
        ), mock.patch.object(ap, "_try_mootdx_server", return_value=None):
            self.assertIsNone(ap._init_mootdx_client())

    def test_scan_discovers_working_mirror_after_curated_fail(self):
        """curated 全挂时，有界扫描发现一个可用镜像并缓存。"""
        scan_server = ("203.0.113.10", 7709)
        client = FakeClient(_nonempty_df())
        with mock.patch.object(
            ap, "_mootdx_scan_candidates", return_value=[scan_server]
        ), mock.patch.object(
            ap, "_mootdx_scan_due", return_value=True
        ), mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=(scan_server, client)
        ) as race:
            got = ap._init_mootdx_client()
        self.assertIs(got, client)
        self.assertEqual(ap._mootdx_discovered_server, scan_server)
        self.assertIsNotNone(ap._mootdx_last_scan_at)
        # 扫描候选与 curated 同批竞速，不是第二轮串行
        self.assertIn(scan_server, race.call_args[0][1])

    def test_discovered_server_reused_without_rescan(self):
        """扫描发现的镜像在后续 init 中被优先复用，且未到冷却期不再扫描。"""
        discovered = ("203.0.113.20", 7709)
        ap._mootdx_discovered_server = discovered
        ap._mootdx_last_scan_at = 1e9  # 冷却期内
        client = FakeClient(_nonempty_df())

        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=(discovered, client)
        ) as race:
            got = ap._init_mootdx_client()
        self.assertIs(got, client)
        self.assertIn(discovered, race.call_args[0][1])
        # 冷却期内不触发扫描候选
        with mock.patch.object(
            ap, "_mootdx_scan_candidates"
        ) as scan, mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=(discovered, client)
        ):
            got2 = ap._init_mootdx_client()
            self.assertIs(got2, client)
            scan.assert_not_called()

    def test_scan_respects_cooldown(self):
        """扫描在冷却期内不重复执行（避免死镜像池被反复全量扫描）。"""
        ap._mootdx_last_scan_at = 1e9
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(ap, "_mootdx_scan_candidates") as scan, mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=None
        ), mock.patch.object(
            ap, "_try_mootdx_server", return_value=None
        ):
            self.assertIsNone(ap._init_mootdx_client())
            scan.assert_not_called()

    def test_try_server_returns_client_on_real_data(self):
        """能取到非空 K 线的 client 被返回。"""
        client = FakeClient(_nonempty_df())
        quotes = FakeQuotes(client)
        got = ap._try_mootdx_server(quotes, ("1.1.1.1", 7709))
        self.assertIs(got, client)
        quotes.factory.assert_called_with(
            market="std",
            server=("1.1.1.1", 7709),
            timeout=ap._MOOTDX_CONNECT_TIMEOUT,
        )

    def test_try_server_skips_empty_klines(self):
        """握手成功但返回空 K 线的镜像被丢弃（返回 None）。"""
        client = FakeClient(_empty_df())
        quotes = FakeQuotes(client)
        self.assertIsNone(ap._try_mootdx_server(quotes, ("1.1.1.1", 7709)))

    def test_try_server_rejects_mirror_without_minutes(self):
        """只返回日线、不返回 5 分钟线的镜像被拒绝（深度图默认取 5 分钟）。"""
        client = FakeClient(_daily_only_df)
        quotes = FakeQuotes(client)
        self.assertIsNone(ap._try_mootdx_server(quotes, ("1.1.1.1", 7709)))

    def test_try_server_builds_default_when_no_server(self):
        """server=None 时退化为不带 server 参数的默认工厂调用。"""
        client = FakeClient(_nonempty_df())
        quotes = FakeQuotes(client)
        got = ap._try_mootdx_server(quotes, None)
        self.assertIs(got, client)
        quotes.factory.assert_called_once_with(market="std")

    # ------------------------------------------------------------------
    # 镜像池扩充路径（VEW-62）
    # ------------------------------------------------------------------

    def test_extra_servers_extend_curated_instead_of_replacing(self):
        """MOOTDX_EXTRA_SERVERS 追加在内置列表之前，curated 仍保留为回退。"""
        with mock.patch.object(ap.settings, "MOOTDX_SERVERS", ""), mock.patch.object(
            ap.settings, "MOOTDX_EXTRA_SERVERS", "8.8.8.8:7709,9.9.9.9"
        ):
            servers = ap._curated_mootdx_servers()
        self.assertEqual(servers[:2], [("8.8.8.8", 7709), ("9.9.9.9", 7709)])
        # 内置列表被完整保留在后面，而不是被覆盖
        self.assertEqual(servers[2:], list(ap._MOOTDX_SERVERS))

    def test_extra_servers_ignored_when_override_is_set(self):
        """MOOTDX_SERVERS 非空时完整覆盖，MOOTDX_EXTRA_SERVERS 不参与。"""
        with mock.patch.object(
            ap.settings, "MOOTDX_SERVERS", "7.7.7.7:7709"
        ), mock.patch.object(ap.settings, "MOOTDX_EXTRA_SERVERS", "8.8.8.8:7709"):
            self.assertEqual(ap._curated_mootdx_servers(), [("7.7.7.7", 7709)])

    def test_extra_servers_skips_invalid_port_without_dropping_list(self):
        """一条脏端口项只跳过自身，不丢掉整张列表。"""
        with mock.patch.object(
            ap.settings, "MOOTDX_EXTRA_SERVERS", "8.8.8.8:7709,bad:port,9.9.9.9"
        ):
            servers = ap._parse_mootdx_server_list(
                ap.settings.MOOTDX_EXTRA_SERVERS, source="MOOTDX_EXTRA_SERVERS"
            )
        self.assertEqual(servers, [("8.8.8.8", 7709), ("9.9.9.9", 7709)])

    # ------------------------------------------------------------------
    # TCP 可达性预筛（VEW-62）
    # ------------------------------------------------------------------

    def test_init_skips_unreachable_candidates_without_probing(self):
        """预筛剪掉的候选不再进入完整的 K 线校验。"""
        client = FakeClient(_nonempty_df())
        first, second = ap._curated_mootdx_servers()[:2]
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(
            ap, "_filter_reachable_servers", return_value=[second]
        ) as reach, mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=(second, client)
        ) as race:
            got = ap._init_mootdx_client()
        self.assertIs(got, client)
        # 不可达的 first 被剪掉，只竞速了 second
        self.assertEqual(race.call_args[0][1], [second])
        self.assertEqual(reach.call_count, 1)
        self.assertIn(first, reach.call_args[0][0])

    def test_init_scan_window_shares_one_reachability_pass(self):
        """curated 与扫描窗口在同一次预筛里一起过，不再分两轮串行探测。"""
        scan_server = ("203.0.113.30", 7709)
        client = FakeClient(_nonempty_df())
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=True
        ), mock.patch.object(
            ap, "_mootdx_scan_candidates", return_value=[scan_server]
        ), mock.patch.object(
            ap, "_filter_reachable_servers", return_value=[scan_server]
        ) as reach, mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=(scan_server, client)
        ):
            got = ap._init_mootdx_client()
        self.assertIs(got, client)
        self.assertEqual(reach.call_count, 1)
        # 同一次预筛的候选里既有 curated 也有扫描窗口
        filtered = reach.call_args[0][0]
        self.assertIn(scan_server, filtered)
        self.assertIn(ap._curated_mootdx_servers()[0], filtered)
        self.assertEqual(ap._mootdx_discovered_server, scan_server)
        self.assertIsNotNone(ap._mootdx_last_scan_at)

    def test_init_reaches_config_default_when_nothing_reachable(self):
        """整池都不可达时不空转竞速，直接走配置默认兜底。"""
        default_client = FakeClient(_nonempty_df())
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(
            ap, "_filter_reachable_servers", return_value=[]
        ), mock.patch.object(
            ap, "_probe_mirrors_concurrently", return_value=None
        ) as race, mock.patch.object(
            ap, "_try_mootdx_server", return_value=default_client
        ) as try_srv:
            got = ap._init_mootdx_client()
        self.assertIs(got, default_client)
        race.assert_not_called()
        try_srv.assert_called_once()
        self.assertIsNone(try_srv.call_args[0][1])

    def test_scan_candidates_window_rotation(self):
        """有界扫描游标推进：多轮扫描覆盖完整列表并回绕。"""
        ap._mootdx_scan_cursor = 0
        hosts = [
            ("srv1", "10.0.0.1", 7709),
            ("srv2", "10.0.0.2", 7709),
            ("srv3", "10.0.0.3", 7709),
            ("srv4", "10.0.0.4", 7709),
        ]
        with mock.patch.object(ap.settings, "MOOTDX_SCAN_LIMIT", 3):
            w1 = ap._mootdx_scan_candidates(hosts)
            w2 = ap._mootdx_scan_candidates(hosts)
            w3 = ap._mootdx_scan_candidates(hosts)
        self.assertEqual(
            w1, [("10.0.0.1", 7709), ("10.0.0.2", 7709), ("10.0.0.3", 7709)]
        )
        # 游标推进 3 个后窗口右移
        self.assertEqual(
            w2, [("10.0.0.4", 7709), ("10.0.0.1", 7709), ("10.0.0.2", 7709)]
        )
        # 再推一轮回绕到开头
        self.assertEqual(
            w3, [("10.0.0.3", 7709), ("10.0.0.4", 7709), ("10.0.0.1", 7709)]
        )

    def test_scan_candidates_dedupes_shared_ip(self):
        """镜像名不同但 ip 相同的项去重。"""
        ap._mootdx_scan_cursor = 0
        hosts = [
            ("srv1", "10.0.0.1", 7709),
            ("srv1b", "10.0.0.1", 7709),
            ("srv2", "10.0.0.2", 7709),
        ]
        with mock.patch.object(ap.settings, "MOOTDX_SCAN_LIMIT", 10):
            window = ap._mootdx_scan_candidates(hosts)
        self.assertEqual(window, [("10.0.0.1", 7709), ("10.0.0.2", 7709)])

    def test_scan_candidates_disabled_when_limit_zero(self):
        """MOOTDX_SCAN_LIMIT=0 时扫描返回空列表。"""
        with mock.patch.object(ap.settings, "MOOTDX_SCAN_LIMIT", 0):
            self.assertEqual(
                ap._mootdx_scan_candidates([("srv1", "10.0.0.1", 7709)]), []
            )


class MootdxReachabilityFilterTests(unittest.TestCase):
    """TCP 可达性预筛本身的行为（VEW-62）。

    独立于 MootdxServerSelectionTests —— 那个类的 setUp 会把预筛整体打桩放行，
    以便专注候选顺序；这里测的正是被打桩掉的那个函数。
    """

    def test_keeps_input_order_and_prunes_unreachable(self):
        """按入参顺序返回可达候选，不可达的被剪掉。"""
        servers = [("10.0.0.1", 7709), ("10.0.0.2", 7709), ("10.0.0.3", 7709)]
        with mock.patch.object(
            ap, "_tcp_reachable", side_effect=lambda s, t: s[0] != "10.0.0.2"
        ) as probe:
            got = ap._filter_reachable_servers(servers, timeout=0.1)
        self.assertEqual(got, [("10.0.0.1", 7709), ("10.0.0.3", 7709)])
        self.assertEqual(probe.call_count, 3)

    def test_empty_input_probes_nothing(self):
        """空候选不做任何探测。"""
        with mock.patch.object(ap, "_tcp_reachable") as probe:
            self.assertEqual(ap._filter_reachable_servers([], timeout=0.1), [])
            probe.assert_not_called()

    def test_single_candidate_uses_short_path(self):
        """只有一个候选时不建线程池，直接探测。"""
        with mock.patch.object(
            ap, "_tcp_reachable", return_value=True
        ) as probe, mock.patch.object(ap, "ThreadPoolExecutor") as pool:
            got = ap._filter_reachable_servers([("10.0.0.1", 7709)], timeout=0.1)
        self.assertEqual(got, [("10.0.0.1", 7709)])
        probe.assert_called_once()
        pool.assert_not_called()

    def test_all_unreachable_returns_empty(self):
        """整池不可达时返回空列表（调用方随即快速放弃，不再逐个探测）。"""
        servers = [("10.0.0.1", 7709), ("10.0.0.2", 7709)]
        with mock.patch.object(ap, "_tcp_reachable", return_value=False):
            self.assertEqual(ap._filter_reachable_servers(servers, timeout=0.1), [])

    def test_worker_exception_is_treated_as_unreachable(self):
        """单个候选探测抛异常不影响其余候选的判定。"""
        servers = [("10.0.0.1", 7709), ("10.0.0.2", 7709)]

        def probe(server, timeout):
            if server[0] == "10.0.0.1":
                raise RuntimeError("boom")
            return True

        with mock.patch.object(ap, "_tcp_reachable", side_effect=probe):
            self.assertEqual(
                ap._filter_reachable_servers(servers, timeout=0.1),
                [("10.0.0.2", 7709)],
            )

    def test_tcp_reachable_false_on_connect_error(self):
        """TCP 建连异常被判为不可达，不向上抛。"""
        with mock.patch.object(
            ap.socket, "create_connection", side_effect=OSError("refused")
        ):
            self.assertFalse(ap._tcp_reachable(("10.0.0.9", 7709), 0.1))

    def test_tcp_reachable_true_on_success(self):
        """建连成功即可达。"""
        with mock.patch.object(ap.socket, "create_connection") as conn:
            self.assertTrue(ap._tcp_reachable(("10.0.0.9", 7709), 0.1))
        conn.assert_called_once_with(("10.0.0.9", 7709), timeout=0.1)


class MootdxConcurrentProbeTests(unittest.TestCase):
    """并发竞速探测本身的行为（VEW-62）。

    独立于 MootdxServerSelectionTests —— 那里把竞速整体打桩以专注候选选择，这里测的
    正是被打桩掉的 ``_probe_mirrors_concurrently``：并发语义、胜出者、落选者回收。
    """

    def test_returns_first_working_candidate(self):
        """竞速返回 (server, client)，其余候选返回 None 不影响结果。"""
        winner = ("10.0.0.2", 7709)
        client = object()

        def probe(Quotes, server, deadline, build_lock):
            return (server, client) if server == winner else None

        with mock.patch.object(ap, "_probe_candidate", side_effect=probe):
            got = ap._probe_mirrors_concurrently(
                mock.Mock(), [("10.0.0.1", 7709), winner], deadline=None
            )
        self.assertEqual(got, (winner, client))

    def test_empty_candidates_return_none(self):
        """没有候选时不建线程池，直接返回 None。"""
        with mock.patch.object(ap, "ThreadPoolExecutor") as pool:
            self.assertIsNone(ap._probe_mirrors_concurrently(mock.Mock(), []))
        pool.assert_not_called()

    def test_single_candidate_uses_short_path(self):
        """只有一个候选时不建线程池，直接探测。"""
        client = object()
        with mock.patch.object(
            ap, "_probe_candidate", return_value=(("10.0.0.1", 7709), client)
        ) as probe, mock.patch.object(ap, "ThreadPoolExecutor") as pool:
            got = ap._probe_mirrors_concurrently(mock.Mock(), [("10.0.0.1", 7709)])
        self.assertEqual(got, (("10.0.0.1", 7709), client))
        probe.assert_called_once()
        pool.assert_not_called()

    def test_all_candidates_failing_returns_none(self):
        """整池候选都探测失败时返回 None。"""
        with mock.patch.object(ap, "_probe_candidate", return_value=None):
            self.assertIsNone(
                ap._probe_mirrors_concurrently(
                    mock.Mock(), [("10.0.0.1", 7709), ("10.0.0.2", 7709)]
                )
            )

    def test_worker_exception_does_not_abort_the_race(self):
        """单个候选探测抛异常时其余候选照常参与竞速。"""
        client = object()

        def probe(Quotes, server, deadline, build_lock):
            if server[0] == "10.0.0.1":
                raise RuntimeError("boom")
            return (server, client)

        with mock.patch.object(ap, "_probe_candidate", side_effect=probe):
            got = ap._probe_mirrors_concurrently(
                mock.Mock(), [("10.0.0.1", 7709), ("10.0.0.2", 7709)]
            )
        self.assertEqual(got, (("10.0.0.2", 7709), client))

    def test_candidate_builds_are_serialized(self):
        """建 client 必须串行：mootdx 的 config 是模块级单例，并发构造会串到别的镜像。"""
        state = {"active": 0, "peak": 0}
        guard = threading.Lock()

        def build(Quotes, server):
            with guard:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.01)
            with guard:
                state["active"] -= 1
            return FakeClient(_empty_df())

        servers = [("10.0.0.%d" % i, 7709) for i in range(1, 5)]
        with mock.patch.object(ap, "_build_mootdx_client", side_effect=build):
            ap._probe_mirrors_concurrently(mock.Mock(), servers)
        self.assertEqual(state["peak"], 1)

    def test_probe_candidate_returns_server_and_client_on_success(self):
        """_probe_candidate 成功时连同 server 一起返回，供调用方记录选中镜像。"""
        client = FakeClient(_nonempty_df())
        got = ap._probe_candidate(
            FakeQuotes(client), ("1.1.1.1", 7709), None, threading.Lock()
        )
        self.assertEqual(got, (("1.1.1.1", 7709), client))

    def test_probe_candidate_closes_client_when_mirror_rejected(self):
        """_probe_candidate 判定镜像不可用时关掉自己的 client，不留悬挂连接。"""
        client = FakeClient(_empty_df())
        with mock.patch.object(ap, "_close_mootdx_client") as close:
            got = ap._probe_candidate(
                FakeQuotes(client), ("1.1.1.1", 7709), None, threading.Lock()
            )
        self.assertIsNone(got)
        close.assert_called_once_with(client)

    def test_discard_losers_closes_completed_and_pending_successes(self):
        """落选者若也探测成功，其 client 必须被回收，否则连接一直挂到进程退出。"""
        loser_client = object()
        winner = mock.Mock()

        done_loser = mock.Mock()
        done_loser.done.return_value = True
        done_loser.result.return_value = (("10.0.0.9", 7709), loser_client)

        pending_loser = mock.Mock()
        pending_loser.done.return_value = False
        pending_loser.result.return_value = (("10.0.0.8", 7709), loser_client)

        with mock.patch.object(ap, "_close_mootdx_client") as close:
            ap._discard_losers([winner, done_loser, pending_loser], winner)
            # 已完成的落选者当场关掉；未完成的挂回调，不阻塞当前请求
            close.assert_called_once_with(loser_client)
            pending_loser.add_done_callback.assert_called_once()
            pending_loser.add_done_callback.call_args[0][0](pending_loser)
            self.assertEqual(close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
