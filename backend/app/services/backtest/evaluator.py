"""共享候选评估器：as-of 过滤、去重、评分排序与进度计数。

选股 (screener) 与回测 (backtest) 共用同一套「候选 → 结果」语义：

- **as_of_date 契约**：只有 `as_of_date` 当日信号才算候选；任何更早日期的信号一律
  抛弃，杜绝「全局最新候选日」把旧信号当当前信号返回。
- **数据陈旧**：某只股票数据末日落后于 `as_of_date`，计入 `stale_data_count`，
  而不是进入候选。
- **进度漏斗**：字段严格拆成 `fetched / data_ok / data_failed / evaluated /
  signal_hits / rejected`，每个字段只表达一件事，不复用。
- **同分 tie-breaker**：score 降序 → liquidity 降序 → symbol 升序，保证次序确定
  且有业务意义（流动性优先），避免「同日 Top-K 退化为 DataFrame 顺序」。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import pandas as pd

#: 进度契约字段：fetch 漏斗，字段间只做数量与归属区分，不混用。
PROGRESS_FIELDS = (
    "fetched",
    "data_ok",
    "data_failed",
    "evaluated",
    "signal_hits",
    "rejected",
)


@dataclass
class EvaluationCounters:
    """单次全市场评估的漏斗计数。

    数字关系（不可混用）：
      fetched + data_failed == total
      data_ok == fetched - stale_data_count
      signal_hits + rejected == evaluated
    """

    fetched: int = 0  #: 成功取到非空数据的股票数
    data_ok: int = 0  #: fetched 中最新数据日 >= as_of_date 的股票数
    data_failed: int = 0  #: 请求总数 - fetched（取数失败/空数据）
    evaluated: int = 0  #: data_ok 中已跑完策略计算的股票数
    signal_hits: int = 0  #: evaluated 中在 as_of_date 命中信号的股票数
    rejected: int = 0  #: evaluated - signal_hits（已评估但当日无信号）
    stale_data_count: int = 0  #: fetched 中数据末日落后者

    def to_progress(self, total: int) -> dict[str, int]:
        """输出前端可见的进度字典（含 total）。total 为选股池大小。"""
        return {"total": total, **asdict(self)}

    def snapshot(self) -> dict[str, int]:
        return asdict(self)


def _timestamp_to_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def symbol_data_last_date(df: pd.DataFrame) -> str | None:
    """单只股票数据的最新数据日（YYYY-MM-DD）。"""
    if df is None or df.empty:
        return None
    if "datetime" in df.columns:
        return _timestamp_to_date(pd.to_datetime(df["datetime"]).max())
    if "trade_date" in df.columns:
        return _timestamp_to_date(pd.to_datetime(df["trade_date"]).max())
    return None


def determine_as_of_date(symbol_dfs: dict[str, pd.DataFrame]) -> str | None:
    """取全市场最新数据日作为 as_of_date。

    数据窗口起点一致，正常情形下该值即最近一个交易日；任一股票数据末日落后于
    该值，即视为数据陈旧（进入 stale_data_count）。
    """
    latest: str | None = None
    for df in symbol_dfs.values():
        if df is None or df.empty:
            continue
        last = symbol_data_last_date(df)
        if last is None:
            continue
        if latest is None or last > latest:
            latest = last
    return latest


def is_stale(df: pd.DataFrame, as_of_date: str | None) -> bool:
    """数据是否陈旧：最新数据日早于 as_of_date（或无法判定）。"""
    if not as_of_date:
        return False
    last = symbol_data_last_date(df)
    if last is None:
        return True
    return last < as_of_date


def candidate_date(rec: dict[str, Any]) -> str | None:
    """候选记录的 trade_date 转 YYYY-MM-DD。"""
    return _timestamp_to_date(rec.get("trade_date"))


def dedupe_by_symbol(
    candidates: Iterable[dict[str, Any]],
    score_key: str = "signal_strength",
) -> list[dict[str, Any]]:
    """按 symbol 去重，保留 score 最高记录；同分保留先到者。"""
    seen: dict[str, dict[str, Any]] = {}
    for c in candidates:
        sym = str(c.get("symbol", "")).strip()
        if not sym:
            continue
        score = float(c.get(score_key, 0) or 0)
        current = seen.get(sym)
        if current is None or score > float(current.get(score_key, 0) or 0):
            seen[sym] = c
    return list(seen.values())


def rank_candidates(
    candidates: Iterable[dict[str, Any]],
    score_key: str = "signal_strength",
) -> list[dict[str, Any]]:
    """确定性排序：score 降序 → liquidity 降序 → symbol 升序。

    liquidity 是业务意义的次级排序（流动性越强越优先）；symbol 升序兜底，
    保证同分时输出顺序确定，不随 DataFrame 内部顺序变化。
    """

    def sort_key(c: dict[str, Any]) -> tuple[float, float, str]:
        score = float(c.get(score_key, 0) or 0)
        liquidity = float(c.get("liquidity", 0) or 0)
        symbol = str(c.get("symbol", ""))
        return (-score, -liquidity, symbol)

    return sorted(candidates, key=sort_key)


def normalize_score(value: float, score_range: tuple[float, float] | None) -> float:
    """把策略原始 signal_strength 归一化为 [0,1] 的策略评分。

    score_range=(lo, hi) 是策略契约给出的理论范围；hi <= lo（如恒定信号）时
    value > 0 视为满档 1.0，否则 0.0。
    """
    if score_range is None:
        return float(value)
    lo, hi = score_range
    if hi <= lo:
        return 1.0 if float(value) > 0 else 0.0
    return max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))
