"""分钟级按需取数：深度裁决 + 结构化 provenance。

数据入口是**纯网络按需取数**（mootdx 为主、腾讯 ``ifzq`` 为次，VEW-66 范围修订），
不落常驻库。两条要求在本模块落地：

1. **深度显式暴露**：请求区间超出源深度时，按 ``strict`` 报错或按 ``truncate`` 截断，
   并在 provenance 里标注 ``depth_exceeded`` / ``truncated`` / ``depth_note`` ——
   绝不静默返回不完整区间。
2. **不可复现必须留痕**：源窗口随时间前移，今天取得到的区间几个月后取不到。每次取数
   都记录 ``fetched_at``（取数时刻）与 ``actual_start`` / ``actual_end``（**实际返回**
   的区间），使事后能判断一次回测究竟跑在什么数据上。

取数函数可注入（``fetch_minute``），便于测试与替换源；默认走
``get_data_provider().fetch_minute_data``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd

from app.providers.provenance import DataProvenance
from app.services.backtest.minute.depth import (
    DepthDecision,
    resolve_request_window,
)
from app.services.backtest.minute.session import bars_per_day

#: 分钟 bar 的标准列（与数据源层输出契约一致）
MINUTE_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]

#: 单标的单次取数的墙钟预算（秒）。数据源层默认 12s 是给前端 15s 超时留的余量；回测
#: 不是交互路径，长区间翻页需要更大预算，但仍有界 —— 超预算即返回已取部分，并由
#: provenance 的 gap 暴露出来。
DEFAULT_FETCH_BUDGET_SECONDS = 60.0

#: 交易日 / 自然日换算系数（与日线 warmup 回拉同口径）
_CALENDAR_FACTOR = 1.6

FetchMinuteFn = Callable[..., Optional[pd.DataFrame]]


def _default_fetch_minute(
    symbol: str,
    period: str,
    start_datetime: str,
    end_datetime: str,
    count: int,
    deadline: float,
) -> Optional[pd.DataFrame]:
    from app.providers import get_data_provider

    return get_data_provider().fetch_minute_data(
        stock_code=symbol,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        period=period,
        adjust="",
        count=count,
        deadline=deadline,
    )


def estimate_bar_count(start: date, end: date, period: str) -> int:
    """按自然日跨度估算需要拉取的 bar 数（交易日 ≈ 自然日 / 1.6）。"""
    span_days = max((end - start).days, 0)
    sessions = max(1, int(span_days / _CALENDAR_FACTOR) + 1)
    return sessions * bars_per_day(period)


def normalize_minute_frame(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """把源返回的分钟帧归一化为标准列 + 时间升序去重；无有效行返回 None。"""
    if df is None or getattr(df, "empty", True):
        return None
    missing = [col for col in ("datetime", "open", "close") if col not in df.columns]
    if missing:
        return None

    work = df.copy()
    work["datetime"] = pd.to_datetime(work["datetime"], errors="coerce")
    work = work.dropna(subset=["datetime"])
    if work.empty:
        return None
    for col in ("open", "high", "low", "close", "volume"):
        if col not in work.columns:
            if col == "high":
                work[col] = work[["open", "close"]].max(axis=1)
            elif col == "low":
                work[col] = work[["open", "close"]].min(axis=1)
            else:
                work[col] = 0.0
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["open", "close"])
    if work.empty:
        return None
    # 翻页交界可能重叠，按 datetime 去重后再排序
    work = work.sort_values("datetime").drop_duplicates(
        subset=["datetime"], keep="last"
    )
    return work.reset_index(drop=True)


def _actual_window(
    df: Optional[pd.DataFrame], requested_start: date, requested_end: date
) -> tuple[Optional[str], Optional[str], bool]:
    """返回 (actual_start, actual_end, gap)。

    ``gap`` 用**截断后**的窗口判定：截断本身已由 ``truncated`` 显式标注，这里的缺口
    专指「源连截断后的窗口都没覆盖满」—— 纯网络方案下最难发现的那种静默失败。
    """
    if df is None or df.empty:
        return None, None, True
    actual_start = pd.Timestamp(df["datetime"].iloc[0]).strftime("%Y-%m-%d %H:%M:%S")
    actual_end = pd.Timestamp(df["datetime"].iloc[-1]).strftime("%Y-%m-%d %H:%M:%S")
    gap = False
    if pd.Timestamp(actual_start).normalize() > pd.Timestamp(requested_start):
        gap = True
    if pd.Timestamp(actual_end).normalize() < pd.Timestamp(requested_end):
        gap = True
    return actual_start, actual_end, gap


@dataclass
class MinuteLoadResult:
    """单标的分钟取数结果：帧 + provenance + 本次深度裁决。"""

    symbol: str
    period: str
    df: Optional[pd.DataFrame]
    provenance: DataProvenance
    decision: DepthDecision


class MinuteDataLoader:
    """按需拉取分钟 bar，并把深度裁决与实际区间写进 provenance。"""

    def __init__(
        self,
        fetch_minute: FetchMinuteFn | None = None,
        budget_seconds: float = DEFAULT_FETCH_BUDGET_SECONDS,
        now: Callable[[], datetime] | None = None,
    ):
        self._fetch_minute = fetch_minute or _default_fetch_minute
        self._budget_seconds = float(budget_seconds)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def load(
        self,
        symbol: str,
        period: str | int,
        start_date: date | str,
        end_date: date | str,
        policy: str = "truncate",
        as_of: date | str | None = None,
    ) -> MinuteLoadResult:
        """取一只标的的分钟数据（区间已按深度策略裁决）。

        Raises:
            MinuteDepthExceededError: ``strict`` 策略下请求超出源深度，或整个区间都在
                可用深度之外。
        """
        decision = resolve_request_window(
            period, start_date, end_date, policy=policy, as_of=as_of
        )
        count = estimate_bar_count(
            decision.effective_start, decision.effective_end, decision.period
        )
        start_datetime = f"{decision.effective_start} 09:00:00"
        end_datetime = f"{decision.effective_end} 16:00:00"

        failure_reason: str | None = None
        raw: Optional[pd.DataFrame] = None
        try:
            raw = self._fetch_minute(
                symbol,
                decision.period,
                start_datetime,
                end_datetime,
                count,
                self._deadline(),
            )
        except Exception as exc:  # 源异常不能吞掉：转成 provenance 的失败原因
            failure_reason = f"{type(exc).__name__}: {exc}"

        df = normalize_minute_frame(raw)
        source = None
        if raw is not None:
            source = getattr(raw, "attrs", {}).get("source")
        actual_start, actual_end, gap = _actual_window(
            df, decision.effective_start, decision.effective_end
        )
        if df is None and failure_reason is None:
            failure_reason = "数据源返回空（区间内无分钟数据或取数失败）"

        provenance = DataProvenance(
            source=source,
            adjustment="",
            requested_start=decision.effective_start.isoformat(),
            requested_end=decision.effective_end.isoformat(),
            actual_start=actual_start,
            actual_end=actual_end,
            bar_count=0 if df is None else int(len(df)),
            last_bar=actual_end,
            gap=gap,
            failure_reason=failure_reason,
            period=decision.period,
            fetched_at=self._now().isoformat(),
            truncated=decision.truncated,
            depth_exceeded=decision.exceeded,
            depth_note=decision.note,
        )
        return MinuteLoadResult(
            symbol=str(symbol),
            period=decision.period,
            df=df,
            provenance=provenance,
            decision=decision,
        )

    def _deadline(self) -> float:
        import time as _time

        return _time.monotonic() + self._budget_seconds


def provenance_summary(results: list[MinuteLoadResult]) -> dict[str, Any]:
    """把逐标的 provenance 聚合为 diagnostics 用的视图。

    记录取数时间与各源**实际返回**的区间 —— 纯网络方案下这是判断「一次回测究竟跑在
    什么数据上」的唯一依据。
    """
    source_counts: dict[str, int] = {}
    gap_count = 0
    truncated_count = 0
    failure_count = 0
    empty_count = 0
    fetched_at_values: list[str] = []
    actual_starts: list[str] = []
    actual_ends: list[str] = []
    per_symbol: list[dict[str, Any]] = []

    for result in results:
        prov = result.provenance
        key = prov.source or "none"
        source_counts[key] = source_counts.get(key, 0) + 1
        if prov.gap:
            gap_count += 1
        if prov.truncated:
            truncated_count += 1
        if prov.failure_reason:
            failure_count += 1
        if result.df is None or result.df.empty:
            empty_count += 1
        if prov.fetched_at:
            fetched_at_values.append(prov.fetched_at)
        if prov.actual_start:
            actual_starts.append(prov.actual_start)
        if prov.actual_end:
            actual_ends.append(prov.actual_end)
        per_symbol.append(
            {
                "symbol": result.symbol,
                "period": prov.period,
                "source": prov.source,
                "requested_start": prov.requested_start,
                "requested_end": prov.requested_end,
                "actual_start": prov.actual_start,
                "actual_end": prov.actual_end,
                "bar_count": prov.bar_count,
                "fetched_at": prov.fetched_at,
                "gap": prov.gap,
                "truncated": prov.truncated,
                "depth_exceeded": prov.depth_exceeded,
                "failure_reason": prov.failure_reason,
            }
        )

    return {
        "source_counts": source_counts,
        "gap_count": gap_count,
        "truncated_count": truncated_count,
        "failure_count": failure_count,
        "empty_count": empty_count,
        "degraded": bool(gap_count or truncated_count or failure_count),
        "fetched_at_min": min(fetched_at_values) if fetched_at_values else None,
        "fetched_at_max": max(fetched_at_values) if fetched_at_values else None,
        "actual_window": {
            "start": min(actual_starts) if actual_starts else None,
            "end": max(actual_ends) if actual_ends else None,
        },
        "symbols": per_symbol,
    }
