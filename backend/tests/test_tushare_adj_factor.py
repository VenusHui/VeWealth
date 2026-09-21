"""Tushare adj_factor 缓存与配额管理测试（VEW-55）。

覆盖：
- apply_adjust 的 qfq/hfq 复权计算与因子缺失降级；
- AdjFactorStore 缓存命中不再触发真实拉取；
- 每日配额耗尽后拒绝拉取（返回缓存或 None）；
- 最小间隔守卫（两次真实拉取之间）；
- 配额状态跨天重置与磁盘持久化。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from app.providers import tushare_adj as ta


def _adj_df(factors):
    """构造 trade_date 升序的 adj_factor DataFrame。"""
    return pd.DataFrame(
        {"trade_date": list(factors.keys()), "adj_factor": list(factors.values())}
    )


def _raw_df(dates, prices=None):
    """构造含 trade_date 与 OHLC 的非复权日线 DataFrame。"""
    prices = prices or list(range(1, len(dates) + 1))
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": [p * 1.01 for p in prices],
            "close": prices,
            "high": [p * 1.05 for p in prices],
            "low": [p * 0.95 for p in prices],
            "vol": [1000] * len(dates),
        }
    )


class ApplyAdjustTests(unittest.TestCase):
    def test_qfq_normalizes_by_latest_factor(self):
        """qfq：factor = adj_factor / 最新 adj_factor。"""
        raw = _raw_df(["20260101", "20260102", "20260103"], prices=[10, 11, 12])
        adj = _adj_df({"20260101": 2.0, "20260102": 2.0, "20260103": 4.0})
        out = ta.apply_adjust(raw, adj, "qfq")
        self.assertIsNotNone(out)
        # 最新交易日 factor=1，其他日期 factor=0.5
        self.assertAlmostEqual(out["close"].iloc[0], 5.0)
        self.assertAlmostEqual(out["close"].iloc[1], 5.5)
        self.assertAlmostEqual(out["close"].iloc[2], 12.0)

    def test_hfq_multiplies_by_factor(self):
        """hfq：factor = adj_factor。"""
        raw = _raw_df(["20260101", "20260102"], prices=[10, 12])
        adj = _adj_df({"20260101": 2.0, "20260102": 4.0})
        out = ta.apply_adjust(raw, adj, "hfq")
        self.assertIsNotNone(out)
        self.assertAlmostEqual(out["close"].iloc[0], 20.0)
        self.assertAlmostEqual(out["close"].iloc[1], 48.0)

    def test_none_adjust_returns_copy(self):
        """adjust 为空时原样返回（不改变数据）。"""
        raw = _raw_df(["20260101"], prices=[10])
        out = ta.apply_adjust(raw, _adj_df({"20260101": 2.0}), "")
        self.assertIsNotNone(out)
        self.assertAlmostEqual(out["close"].iloc[0], 10.0)

    def test_missing_factor_returns_none(self):
        """因子整体缺失（NaN）时返回 None，由调用方降级为非复权。"""
        raw = _raw_df(["20260101", "20260102"], prices=[10, 11])
        # 因子序列与日线日期无交集 → 全部 NaN
        adj = _adj_df({"20260105": 2.0})
        self.assertIsNone(ta.apply_adjust(raw, adj, "qfq"))

    def test_suspended_day_factor_filled(self):
        """停牌日缺因子时用相邻因子填充，不整体降级。"""
        raw = _raw_df(["20260101", "20260102"], prices=[10, 11])
        adj = _adj_df({"20260101": 2.0, "20260103": 4.0})
        out = ta.apply_adjust(raw, adj, "qfq")
        self.assertIsNotNone(out)
        # 20260102 因子由 ffill 填充为 2.0，归一后 factor=0.5
        self.assertAlmostEqual(out["close"].iloc[1], 5.5)

    def test_empty_inputs_return_none(self):
        self.assertIsNone(
            ta.apply_adjust(pd.DataFrame(), _adj_df({"20260101": 1.0}), "qfq")
        )
        self.assertIsNone(ta.apply_adjust(_raw_df(["20260101"]), pd.DataFrame(), "qfq"))


class AdjFactorStoreTests(unittest.TestCase):
    def _make_store(self, quota=5, min_interval=60):
        tmp = tempfile.mkdtemp(prefix="tushare_adj_test_")
        return ta.AdjFactorStore(
            cache_dir=Path(tmp), daily_quota=quota, min_interval=min_interval
        )

    def test_cache_hit_skips_fetch(self):
        """缓存命中后 get_or_fetch 不再触发真实拉取。"""
        store = self._make_store()
        adj = _adj_df({"20260101": 2.0, "20260102": 4.0})
        store._factors["000001.SZ"] = adj
        with mock.patch.object(store, "_fetch_from_tushare") as fetch:
            got = store.get_or_fetch("000001.SZ")
            fetch.assert_not_called()
        self.assertIs(got, adj)

    def test_fetch_writes_cache_and_disk(self):
        """首次拉取写入内存与磁盘缓存，配额被消耗。"""
        store = self._make_store(quota=2, min_interval=0)
        adj = _adj_df({"20260101": 2.0})
        with mock.patch.object(store, "_fetch_from_tushare", return_value=adj) as fetch:
            got = store.get_or_fetch("000001.SZ")
            self.assertIs(got, adj)
            fetch.assert_called_once_with("000001.SZ")
            self.assertEqual(store.quota_remaining, 1)
            # 磁盘文件已写
            self.assertTrue(store._cache_path("000001.SZ").exists())
            # 第二次走缓存
            got2 = store.get_or_fetch("000001.SZ")
            self.assertIs(got2, adj)
            fetch.assert_called_once()

    def test_quota_exhausted_returns_cached_or_none(self):
        """每日配额耗尽后：有缓存返回缓存，无缓存返回 None 且不拉取。"""
        store = self._make_store(quota=1, min_interval=0)
        adj = _adj_df({"20260101": 2.0})
        with mock.patch.object(store, "_fetch_from_tushare", return_value=adj):
            self.assertIsNotNone(store.get_or_fetch("000001.SZ"))
        self.assertEqual(store.quota_remaining, 0)

        # 配额耗尽：新股票不拉取
        with mock.patch.object(store, "_fetch_from_tushare") as fetch:
            self.assertIsNone(store.get_or_fetch("600519.SH"))
            fetch.assert_not_called()

        # 配额耗尽：已缓存股票仍返回缓存
        self.assertIsNotNone(store.get_or_fetch("000001.SZ"))

    def test_min_interval_guard(self):
        """两次真实拉取之间受最小间隔限制。"""
        store = self._make_store(quota=5, min_interval=60)
        adj = _adj_df({"20260101": 2.0})
        with mock.patch.object(store, "_fetch_from_tushare", return_value=adj):
            self.assertIsNotNone(store.get_or_fetch("000001.SZ"))
        # 间隔未到：第二只股票被拒绝
        with mock.patch.object(store, "_fetch_from_tushare") as fetch:
            self.assertIsNone(store.get_or_fetch("600519.SH"))
            fetch.assert_not_called()
        # 推进时间后再拉取
        store._last_fetch_at = time.monotonic() - 120
        with mock.patch.object(store, "_fetch_from_tushare", return_value=adj):
            self.assertIsNotNone(store.get_or_fetch("600519.SH"))

    def test_failed_fetch_refunds_quota(self):
        """真实拉取失败后退还配额。"""
        store = self._make_store(quota=1, min_interval=0)
        with mock.patch.object(store, "_fetch_from_tushare", return_value=None):
            self.assertIsNone(store.get_or_fetch("000001.SZ"))
        self.assertEqual(store.quota_remaining, 1)

    def test_quota_state_persists_across_instances(self):
        """配额状态持久化到磁盘，新实例能读到当日已用额度。"""
        cache_dir = Path(tempfile.mkdtemp(prefix="tushare_adj_quota_"))
        store = ta.AdjFactorStore(cache_dir=cache_dir, daily_quota=5, min_interval=0)
        with mock.patch.object(
            store, "_fetch_from_tushare", return_value=_adj_df({"20260101": 2.0})
        ):
            store.get_or_fetch("000001.SZ")
            store.get_or_fetch("600519.SH")
        self.assertEqual(store.quota_remaining, 3)

        # 新实例从磁盘恢复当日计数
        store2 = ta.AdjFactorStore(cache_dir=cache_dir, daily_quota=5, min_interval=0)
        self.assertEqual(store2.quota_remaining, 3)


if __name__ == "__main__":
    unittest.main()
