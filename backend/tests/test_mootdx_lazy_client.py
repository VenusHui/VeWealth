"""测试 mootdx 客户端惰性自愈初始化逻辑（VEW-36）。

覆盖 _get_mootdx_client 的三个关键行为：
- 首次初始化失败后，冷却期内不再重复初始化（避免锤击 TDX 镜像）；
- 冷却期结束后能够重试并成功（自愈）；
- 初始化成功后返回缓存客户端，不会反复重建。
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.providers import astock_provider as ap


class FakeQuotes:
    """模拟 mootdx factory 返回的客户端。"""

    def bars(self, *args, **kwargs):
        return []


class MootdxLazyClientTests(unittest.TestCase):
    def setUp(self):
        # 每次测试前重置模块级状态，避免跨用例泄漏
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None

    def tearDown(self):
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None

    def test_init_success_returns_client_and_caches(self):
        client = FakeQuotes()
        with mock.patch.object(ap, "_init_mootdx_client", return_value=client):
            got = ap._get_mootdx_client()
            self.assertIs(got, client)
            # 第二次调用应直接返回缓存实例，不重建
            self.assertIs(got, ap._get_mootdx_client())
            # 成功状态下失败时间被清空
            self.assertIsNone(ap._mootdx_init_failed_at)

    def test_init_failure_blocks_retry_during_cooldown(self):
        with mock.patch.object(ap, "_init_mootdx_client", return_value=None):
            self.assertIsNone(ap._get_mootdx_client())
            self.assertIsNotNone(ap._mootdx_init_failed_at)
            # 冷却期内再次调用应立即返回 None，不再触发 _init_mootdx_client
            with mock.patch.object(ap, "_init_mootdx_client") as init:
                self.assertIsNone(ap._get_mootdx_client())
                init.assert_not_called()

    def test_retry_succeeds_after_cooldown(self):
        with mock.patch.object(ap, "_init_mootdx_client", return_value=None):
            self.assertIsNone(ap._get_mootdx_client())
        # 推进时间越过冷却期
        ap._mootdx_init_failed_at = 0.0
        client = FakeQuotes()
        with mock.patch.object(ap, "_init_mootdx_client", return_value=client):
            self.assertIs(ap._get_mootdx_client(), client)
            self.assertIsNone(ap._mootdx_init_failed_at)


if __name__ == "__main__":
    unittest.main()
