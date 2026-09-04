"""共享候选评估器：as-of 过滤、去重、评分排序与进度计数。

选股 (screener) 与回测 (backtest) 共用「候选 → 结果」的评分语义：

- **as_of_date 契约**：只有 `as_of_date` 当日信号才算候选；任何更早日期的信号一律
  抛弃，杜绝「全局最新候选日」把旧信号当当前信号返回。（回测是时序引擎，按交易日
  逐日评估，故 as-of / 漏斗用于选股扫描；本模块同时向两端提供统一的评分归一化、
  liquidity 代理与 tie-break 排序定义。）
- **数据陈旧**：某只股票数据末日落后于 `as_of_date`，计入 `stale_data_count`，
  而不是进入候选。
- **进度漏斗**：字段严格拆成 `fetched / data_ok / data_failed / evaluated /
  signal_hits / rejected`，每个字段只表达一件事，不复用。
- **同分 tie-breaker**：score 降序 → liquidity 降序 → symbol 升序，保证次序确定
  且有业务意义（流动性优先），避免「同日 Top-K 退化为 DataFrame 顺序」。该顺序由
  本模块的 TIEBREAK_* 常量统一定义，选股排序与回测 ranking policy 共用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import pandas as pd

#: 同分排序的统一定义：score 降序 → liquidity 降序 → symbol 升序。
#: 选股的 rank_candidates 与回测的 SignalThenLiquidityRanking 都据此排序，
#: 保证两端 tie-break 语义完全一致（单一事实来源）。
TIEBREAK_COLUMNS: tuple[str, ...] = ("signal_strength", "liquidity", "symbol")
TIEBREAK_ASCENDING: tuple[bool, ...] = (False, False, True)

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


def liquidity_at_date(
    df: pd.DataFrame,
    target_date: str | pd.Timestamp | None,
    window: int = 20,
) -> float:
    """业务意义的流动性代理：截至 target_date 的窗口内日均成交额（close × volume）。

    A 股无公开成交额列时以 `close*volume` 近似。选股端传 as_of_date（全市场同一天），
    回测端传每条候选自身的 trade_date。数据缺失 / 列缺失 / target_date 为空时返回 0，
    使同分次序仍由 symbol 兜底而不报错。
    """
    if df is None or df.empty:
        return 0.0
    required = {"datetime", "close", "volume"}
    if not required.issubset(df.columns):
        return 0.0
    try:
        work = df[["datetime", "close", "volume"]].copy()
        work["_date"] = pd.to_datetime(work["datetime"]).dt.normalize()
        work["_turn"] = pd.to_numeric(work["close"], errors="coerce") * pd.to_numeric(
            work["volume"], errors="coerce"
        )
        work = work.dropna(subset=["_turn"])
        if work.empty:
            return 0.0
        if target_date is not None:
            work = work[work["_date"] <= pd.Timestamp(target_date)]
        if work.empty:
            return 0.0
        return float(work["_turn"].tail(window).mean())
    except (ValueError, TypeError, KeyError):
        return 0.0


def attach_liquidity_dict(
    candidates: Iterable[dict[str, Any]],
    symbol_dfs: dict[str, pd.DataFrame],
    target_date: str | pd.Timestamp | None,
    window: int = 20,
) -> list[dict[str, Any]]:
    """给 dict 形态候选注入真实 liquidity（选股端用，全体候选同属 as_of_date）。

    每个候选按其 symbol 在该日窗口的日均成交额填充 `liquidity`；找不到行情则置 0。
    """
    out: list[dict[str, Any]] = []
    for c in candidates:
        sym = str(c.get("symbol", "")).strip()
        doc = dict(c)
        doc["liquidity"] = liquidity_at_date(symbol_dfs.get(sym), target_date, window)
        out.append(doc)
    return out


def attach_liquidity_df(
    candidates_df: pd.DataFrame,
    market_data_map: dict[str, pd.DataFrame],
    window: int = 20,
) -> pd.DataFrame:
    """给 DataFrame 形态候选注入真实 liquidity（回测端用，逐候选按其 trade_date）。

    每条候选的 liquidity 取该 symbol 在其 trade_date 窗口内的日均成交额；
    缺行情的 symbol 置 0，不会改变同分次序（仍由 symbol 兜底）。
    """
    if candidates_df is None or candidates_df.empty:
        return candidates_df if candidates_df is not None else pd.DataFrame()
    if (
        "symbol" not in candidates_df.columns
        or "trade_date" not in candidates_df.columns
    ):
        return candidates_df
    out = candidates_df.copy()
    if "liquidity" not in out.columns:
        out["liquidity"] = 0.0
    for idx, row in out.iterrows():
        sym = str(row.get("symbol") or "").strip()
        sdf = market_data_map.get(sym)
        if sdf is None or sdf.empty:
            continue
        out.loc[idx, "liquidity"] = liquidity_at_date(
            sdf, row.get("trade_date"), window
        )
    return out


def rank_candidates(
    candidates: Iterable[dict[str, Any]],
    score_key: str = "signal_strength",
) -> list[dict[str, Any]]:
    """确定性排序：score 降序 → liquidity 降序 → symbol 升序（TIEBREAK_* 常量定义）。

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
