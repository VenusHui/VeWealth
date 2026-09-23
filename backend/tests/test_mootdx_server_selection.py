"""测试 mootdx 服务端镜像选择逻辑（VEW-36 / VEW-55）。

_manage_mootdx_server 的关键行为是「连着通不算数，必须真的能取到 K 线」：
- 镜像若忽略 TCP 握手但返回空 K 线，应被跳过、尝试下一个；
- 第一个能取到有效 K 线的镜像被采用；
- _init_mootdx_client 在 curated 全部失败后执行有界公开镜像扫描（VEW-55），
  再回退到配置默认，最后才返回 None；
- settings.MOOTDX_SERVERS 可覆盖内置 curated 列表，无需改代码换镜像（VEW-55）。
"""

from __future__ import annotations

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

    def tearDown(self):
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None
        ap._mootdx_discovered_server = None
        ap._mootdx_last_scan_at = None
        ap._mootdx_scan_cursor = 0
        ap._mootdx_scan_in_progress = False

    def test_init_returns_first_mirror_with_data(self):
        """跳过无数据的镜像，采用第一个能取到 K 线的镜像。"""
        client = FakeClient(_nonempty_df())
        first = ap._curated_mootdx_servers()[0]
        with mock.patch.object(ap, "_try_mootdx_server") as try_srv:
            try_srv.side_effect = lambda Quotes, server, deadline=None: (
                client if server and server != first else None
            )
            got = ap._init_mootdx_client()
            self.assertIs(got, client)
            # 首个镜像被尝试过并因无数据被跳过
            self.assertEqual(try_srv.call_args_list[0][0][1], first)

    def test_settings_override_replaces_curated_list(self):
        """MOOTDX_SERVERS 配置优先于内置 curated 列表。"""
        client = FakeClient(_nonempty_df())
        with mock.patch.object(
            ap.settings, "MOOTDX_SERVERS", "8.8.8.8:7709,9.9.9.9"
        ), mock.patch.object(ap, "_try_mootdx_server") as try_srv:
            try_srv.side_effect = lambda Quotes, server, deadline=None: (
                client if server == ("9.9.9.9", 7709) else None
            )
            got = ap._init_mootdx_client()
            self.assertIs(got, client)
            servers = [c[0][1] for c in try_srv.call_args_list]
            self.assertEqual(servers[0], ("8.8.8.8", 7709))
            self.assertEqual(servers[1], ("9.9.9.9", 7709))
            # 只试配置镜像，不碰内置列表
            self.assertEqual(len(servers), 2)

    def test_init_falls_back_to_config_default(self):
        """curated 全部无数据时回退到配置默认（server=None）。"""
        default_client = FakeClient(_nonempty_df())
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(ap, "_try_mootdx_server") as try_srv:
            try_srv.side_effect = lambda Quotes, server, deadline=None: (
                default_client if server is None else None
            )
            got = ap._init_mootdx_client()
            self.assertIs(got, default_client)
            # curated 列表全部尝试过，最后落到配置默认
            self.assertEqual(try_srv.call_count, len(ap._MOOTDX_SERVERS) + 1)
            self.assertEqual(try_srv.call_args_list[-1][0][1], None)

    def test_init_returns_none_when_all_mirrors_fail(self):
        """全部镜像均失败时返回 None（扫描被禁用）。"""
        with mock.patch.object(ap.settings, "MOOTDX_SCAN_LIMIT", 0), mock.patch.object(
            ap, "_try_mootdx_server", return_value=None
        ):
            self.assertIsNone(ap._init_mootdx_client())

    def test_scan_discovers_working_mirror_after_curated_fail(self):
        """curated 全挂时，有界扫描发现一个可用镜像并缓存。"""
        scan_server = ("203.0.113.10", 7709)
        client = FakeClient(_nonempty_df())
        curated = set(ap._curated_mootdx_servers())
        with mock.patch.object(
            ap, "_mootdx_scan_candidates", return_value=[scan_server]
        ), mock.patch.object(
            ap, "_mootdx_scan_due", return_value=True
        ), mock.patch.object(
            ap, "_try_mootdx_server"
        ) as try_srv:

            def side_effect(Quotes, server, deadline=None):
                if server == scan_server:
                    return client
                return None

            try_srv.side_effect = side_effect
            got = ap._init_mootdx_client()
            self.assertIs(got, client)
            self.assertEqual(ap._mootdx_discovered_server, scan_server)
            self.assertIsNotNone(ap._mootdx_last_scan_at)

    def test_discovered_server_reused_without_rescan(self):
        """扫描发现的镜像在后续 init 中被优先复用，且未到冷却期不再扫描。"""
        discovered = ("203.0.113.20", 7709)
        ap._mootdx_discovered_server = discovered
        ap._mootdx_last_scan_at = 1e9  # 冷却期内
        client = FakeClient(_nonempty_df())

        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(ap, "_try_mootdx_server") as try_srv:
            try_srv.side_effect = lambda Quotes, server, deadline=None: (
                client if server == discovered else None
            )
            got = ap._init_mootdx_client()
            self.assertIs(got, client)
            tried = [c[0][1] for c in try_srv.call_args_list]
            self.assertIn(discovered, tried)
            # 冷却期内不触发扫描候选
            with mock.patch.object(ap, "_mootdx_scan_candidates") as scan:
                got2 = ap._init_mootdx_client()
                self.assertIs(got2, client)
                scan.assert_not_called()

    def test_scan_respects_cooldown(self):
        """扫描在冷却期内不重复执行（避免死镜像池被反复全量扫描）。"""
        ap._mootdx_last_scan_at = 1e9
        with mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(ap, "_mootdx_scan_candidates") as scan, mock.patch.object(
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


if __name__ == "__main__":
    unittest.main()
