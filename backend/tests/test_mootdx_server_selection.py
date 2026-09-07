"""测试 mootdx 服务端镜像选择逻辑（VEW-36）。

_manage_mootdx_server 的关键行为是「连着通不算数，必须真的能取到 K 线」：
- 镜像若忽略 TCP 握手但返回空 K 线，应被跳过、尝试下一个；
- 第一个能取到有效 K 线的镜像被采用；
- _init_mootdx_client 在全部镜像失败后回退到配置默认，再失败才返回 None。
"""

from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from app.providers import astock_provider as ap


class FakeClient:
    """模拟一个 mootdx 客户端，bars() 行为可控。"""

    def __init__(self, bars_df):
        self._bars_df = bars_df

    def bars(self, *args, **kwargs):
        return self._bars_df


class FakeQuotes:
    """模拟 mootdx Quotes，factory 返回可控 client。"""

    def __init__(self, client):
        self._client = client
        self.factory = mock.Mock(return_value=client)


def _nonempty_df() -> pd.DataFrame:
    return pd.DataFrame({"open": [1.0, 2.0, 3.0], "close": [1.0, 2.0, 3.0]})


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


class MootdxServerSelectionTests(unittest.TestCase):
    def test_init_returns_first_mirror_with_data(self):
        """跳过无数据的镜像，采用第一个能取到 K 线的镜像。"""
        client = FakeClient(_nonempty_df())
        with mock.patch.object(ap, "_try_mootdx_server") as try_srv:
            try_srv.side_effect = lambda Quotes, server: (
                client if server and server[0] != "115.238.56.198" else None
            )
            got = ap._init_mootdx_client()
            self.assertIs(got, client)
            # 首个镜像（115.238.56.198）被尝试过并因无数据被跳过
            self.assertEqual(try_srv.call_args_list[0][0][1], ("115.238.56.198", 7709))

    def test_init_falls_back_to_config_default(self):
        """curated 镜像全部无数据时，回退到配置默认（server=None）。"""
        default_client = FakeClient(_nonempty_df())
        with mock.patch.object(ap, "_try_mootdx_server") as try_srv:
            try_srv.side_effect = lambda Quotes, server: (
                default_client if server is None else None
            )
            got = ap._init_mootdx_client()
            self.assertIs(got, default_client)
            # curated 列表全部尝试过，最后落到配置默认
            self.assertEqual(try_srv.call_count, len(ap._MOOTDX_SERVERS) + 1)

    def test_init_returns_none_when_all_mirrors_fail(self):
        """全部镜像均失败时返回 None。"""
        with mock.patch.object(ap, "_try_mootdx_server", return_value=None):
            self.assertIsNone(ap._init_mootdx_client())

    def test_try_server_returns_client_on_real_data(self):
        """能取到非空 K 线的 client 被返回。"""
        client = FakeClient(_nonempty_df())
        quotes = FakeQuotes(client)
        got = ap._try_mootdx_server(quotes, ("1.1.1.1", 7709))
        self.assertIs(got, client)
        quotes.factory.assert_called_with(
            market="std", server=("1.1.1.1", 7709), timeout=5
        )

    def test_try_server_skips_empty_klines(self):
        """握手成功但返回空 K 线的镜像被丢弃（返回 None）。"""
        client = FakeClient(_empty_df())
        quotes = FakeQuotes(client)
        self.assertIsNone(ap._try_mootdx_server(quotes, ("1.1.1.1", 7709)))

    def test_try_server_builds_default_when_no_server(self):
        """server=None 时退化为不带 server 参数的默认工厂调用。"""
        client = FakeClient(_nonempty_df())
        quotes = FakeQuotes(client)
        got = ap._try_mootdx_server(quotes, None)
        self.assertIs(got, client)
        quotes.factory.assert_called_once_with(market="std")


if __name__ == "__main__":
    unittest.main()
