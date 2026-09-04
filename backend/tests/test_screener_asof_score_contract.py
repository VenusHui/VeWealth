"""VEW-27 选股 as-of 与评分契约相关测试。

覆盖：
- as_of_date 确定（取全市场最新数据日）；
- 只有 as_of_date 当日信号才算候选（最新日无信号、前一日有信号 → 结果为空）；
- 数据末日落后者计入 stale_data_count，不进入候选；
- 进度漏斗：fetched / data_ok / data_failed / evaluated / signal_hits / rejected
  字段不可复用，且满足漏斗关系；
- 策略评分归一化 [0,1]、确定性 tie-breaker（liquidity → symbol）。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.screener_job import ScreenerJob
from app.services.backtest.evaluator import (
    EvaluationCounters,
    dedupe_by_symbol,
    determine_as_of_date,
    is_stale,
    normalize_score,
    rank_candidates,
)
from app.services.screener_service import ScreenerService
import app.services.screener_service as screener_module


def _make_daily_df(symbol: str, last_date: str, n: int = 6) -> pd.DataFrame:
    """构造以 last_date 作为最后交易日的日线数据（n 根）。"""
    dates = pd.bdate_range(end=pd.Timestamp(last_date), periods=n)
    df = pd.DataFrame(
        {
            "datetime": dates,
            "open": [10.0] * n,
            "close": [10.5] * n,
            "high": [11.0] * n,
            "low": [9.0] * n,
            "volume": [100000.0] * n,
        }
    )
    df["symbol"] = symbol
    return df


class _AsOfSignalStrategy:
    """固定：把信号打在输入数据最后交易日的『前一交易日』。"""

    strategy_id = "fake_prev_day"
    score_range = (0.0, 1.0)

    def generate_candidates(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        dates = pd.to_datetime(df["datetime"]).dt.normalize()
        if len(dates) < 2:
            return pd.DataFrame()
        sig_date = dates.iloc[-1] - pd.Timedelta(days=1)
        return pd.DataFrame(
            {
                "trade_date": [sig_date],
                "symbol": [str(df["symbol"].iloc[-1])],
                "signal_strength": [0.8],
            }
        )


class _SameDaySignalStrategy:
    """固定：把信号打在输入数据最后交易日（as_of 当日）。"""

    strategy_id = "fake_same_day"
    score_range = (0.0, 1.0)

    def generate_candidates(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        dates = pd.to_datetime(df["datetime"]).dt.normalize()
        if len(dates) < 1:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "trade_date": [dates.iloc[-1]],
                "symbol": [str(df["symbol"].iloc[-1])],
                "signal_strength": [0.8],
            }
        )


class AsOfCandidateTests(unittest.TestCase):
    def setUp(self):
        self.service = ScreenerService()
        engine = create_engine("sqlite://")
        ScreenerJob.__table__.create(bind=engine)
        self.session_factory = sessionmaker(bind=engine)
        # scan_id 不存在 → _is_cancelled 返回 False；_update_progress 找不到行时静默跳过。
        patcher = patch.object(screener_module, "SessionLocal", self.session_factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _token(self, cancelled=False):
        token = MagicMock()
        token.is_cancelled.return_value = cancelled
        return token

    def test_as_of_date_is_max_data_date(self):
        dfs = {
            "AAA": _make_daily_df("AAA", "2025-06-10"),
            "BBB": _make_daily_df("BBB", "2025-06-09"),
        }
        self.assertEqual(determine_as_of_date(dfs), "2025-06-10")

    def test_latest_day_no_signal_yields_empty(self):
        """验收标准：最新日无信号、前一日有信号 → 结果为空。"""
        symbol_dfs = {"AAA": _make_daily_df("AAA", "2025-06-10")}
        as_of = determine_as_of_date(symbol_dfs)
        self.assertEqual(as_of, "2025-06-10")

        candidates, counters = self.service._evaluate(
            token=self._token(),
            strategy_id="fake_prev_day",
            strategy=_AsOfSignalStrategy(),
            symbol_dfs=symbol_dfs,
            params={},
            scan_id="scan_x",
            as_of_date=as_of,
            total=1,
        )
        self.assertEqual(candidates, [])
        self.assertEqual(counters.signal_hits, 0)
        self.assertEqual(counters.rejected, 1)
        self.assertEqual(counters.evaluated, 1)

    def test_same_day_signal_kept_as_candidate(self):
        symbol_dfs = {"AAA": _make_daily_df("AAA", "2025-06-10")}
        as_of = determine_as_of_date(symbol_dfs)
        candidates, counters = self.service._evaluate(
            token=self._token(),
            strategy_id="fake_same_day",
            strategy=_SameDaySignalStrategy(),
            symbol_dfs=symbol_dfs,
            params={},
            scan_id="scan_x",
            as_of_date=as_of,
            total=1,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["symbol"], "AAA")
        self.assertEqual(counters.signal_hits, 1)
        self.assertEqual(counters.rejected, 0)

    def test_stale_data_goes_to_stale_count_not_candidate(self):
        """数据末日落后者计入 stale_data_count，不进入候选。"""
        symbol_dfs = {
            "AAA": _make_daily_df("AAA", "2025-06-10"),
            "BBB": _make_daily_df("BBB", "2025-06-04"),
        }
        as_of = determine_as_of_date(symbol_dfs)
        self.assertEqual(as_of, "2025-06-10")

        candidates, counters = self.service._evaluate(
            token=self._token(),
            strategy_id="fake_same_day",
            strategy=_SameDaySignalStrategy(),
            symbol_dfs=symbol_dfs,
            params={},
            scan_id="scan_x",
            as_of_date=as_of,
            total=2,
        )
        # AAA 命中，BBB 数据陈旧
        self.assertEqual([c["symbol"] for c in candidates], ["AAA"])
        self.assertEqual(counters.stale_data_count, 1)
        self.assertEqual(counters.signal_hits, 1)
        self.assertEqual(counters.data_ok, 1)
        self.assertEqual(counters.rejected, 0)

    def test_progress_funnel_invariants(self):
        symbol_dfs = {
            "AAA": _make_daily_df("AAA", "2025-06-10"),
            "BBB": _make_daily_df("BBB", "2025-06-04"),
            "CCC": _make_daily_df("CCC", "2025-06-10"),
        }
        as_of = determine_as_of_date(symbol_dfs)
        _, counters = self.service._evaluate(
            token=self._token(),
            strategy_id="fake_prev_day",  # 全部信号在 as_of 前一交易日 → 全部 rejected
            strategy=_AsOfSignalStrategy(),
            symbol_dfs=symbol_dfs,
            params={},
            scan_id="scan_x",
            as_of_date=as_of,
            total=3,
        )
        progress = counters.to_progress(total=3)
        # fetched + data_failed == total
        self.assertEqual(progress["fetched"] + progress["data_failed"], 3)
        # data_ok == fetched - stale_data_count
        self.assertEqual(
            progress["data_ok"], progress["fetched"] - progress["stale_data_count"]
        )
        # signal_hits + rejected == evaluated
        self.assertEqual(
            progress["signal_hits"] + progress["rejected"], progress["evaluated"]
        )

    def test_is_stale_edge(self):
        df_fresh = _make_daily_df("AAA", "2025-06-10")
        df_stale = _make_daily_df("BBB", "2025-06-04")
        self.assertFalse(is_stale(df_fresh, "2025-06-10"))
        self.assertTrue(is_stale(df_stale, "2025-06-10"))
        # 无 as_of_date → 不判 stale
        self.assertFalse(is_stale(df_stale, None))


class ScoreAndRankingTests(unittest.TestCase):
    def test_normalize_score_ranges(self):
        # gmm: 0~1 直接映射
        self.assertAlmostEqual(normalize_score(0.3, (0.0, 1.0)), 0.3)
        # ma_cross: (v - 0)/(0.2 - 0)
        self.assertAlmostEqual(normalize_score(0.05, (0.0, 0.2)), 0.25)
        # vsd: 恒定 1.0
        self.assertEqual(normalize_score(1.0, (1.0, 1.0)), 1.0)
        self.assertEqual(normalize_score(0.0, (1.0, 1.0)), 0.0)
        # 无范围 → 原样
        self.assertEqual(normalize_score(0.42, None), 0.42)

    def test_rank_tie_breaker_deterministic(self):
        # 同分（signal_strength 相同），缺 liquidity → symbol 升序兜底
        cands = [
            {"symbol": "C", "signal_strength": 0.8, "liquidity": 0},
            {"symbol": "A", "signal_strength": 0.8, "liquidity": 0},
            {"symbol": "B", "signal_strength": 0.8, "liquidity": 0},
        ]
        ranked = rank_candidates(cands)
        self.assertEqual([c["symbol"] for c in ranked], ["A", "B", "C"])

    def test_rank_liquidity_secondary(self):
        # 同分，liquidity 高者优先
        cands = [
            {"symbol": "Z", "signal_strength": 0.5, "liquidity": 100},
            {"symbol": "Y", "signal_strength": 0.5, "liquidity": 200},
        ]
        ranked = rank_candidates(cands)
        self.assertEqual([c["symbol"] for c in ranked], ["Y", "Z"])

    def test_rank_score_desc(self):
        cands = [
            {"symbol": "A", "signal_strength": 0.9},
            {"symbol": "B", "signal_strength": 0.2},
        ]
        ranked = rank_candidates(cands)
        self.assertEqual([c["symbol"] for c in ranked], ["A", "B"])

    def test_dedupe_by_symbol_keeps_highest(self):
        cands = [
            {"symbol": "A", "signal_strength": 0.3},
            {"symbol": "A", "signal_strength": 0.9},
            {"symbol": "B", "signal_strength": 0.5},
        ]
        deduped = dedupe_by_symbol(cands)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["signal_strength"], 0.9)


if __name__ == "__main__":
    unittest.main()
