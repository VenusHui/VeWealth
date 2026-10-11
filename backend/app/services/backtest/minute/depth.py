"""分钟级取数的「源深度」登记与超深度裁决。

纯网络方案下各源的可回溯窗口是**滚动**的且互不相同（VEW-66 范围修订）：今天能取到
的区间几个月后取不到，同一次回测重跑拿到的数据不同。因此本模块的深度常量只用于
**前置裁决** —— 判断这次请求是否超出源的能力；它不构成可复现承诺。每次取数**实际**
返回了哪段区间、由哪个源返回、什么时候取的，由 ``minute.data`` 逐标的记入 provenance。

深度取值来自实测，不是估算：

- mootdx：1min ≈ 4 个月；5/15/30/60min ≈ 2 年（VEW-61 翻页实测）。
- 腾讯 ``ifzq``：m1 ≈ 18 个交易日；m5/m15 ≈ 6 个月；m30/m60 ≈ 12 个月（VEW-63 实测）。

东财刻意不进本表：它是 mootdx 之后的中间回退源，分钟单次深度最短（VEW-61 实测
1min 仅 5 天），把它的窗口并进「有效深度」只会高估能力。它仍可在有效窗口内服务，
实际由谁服务、返回了哪段区间，一律由 provenance 记录。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

#: 每源、每周期可回溯的自然日上限（滚动窗口，非承诺）
SOURCE_DEPTH_DAYS: dict[str, dict[str, int]] = {
    "mootdx": {"1": 120, "5": 730, "15": 730, "30": 730, "60": 730},
    "tencent": {"1": 26, "5": 183, "15": 183, "30": 365, "60": 365},
}

#: 本模块支持的回测周期（分钟字符串）
SUPPORTED_PERIODS: tuple[str, ...] = ("1", "5", "15", "30", "60")

#: 超出深度时的处置策略：
#: - ``strict``：直接报错，绝不返回不完整区间；
#: - ``truncate``：截断到可用下界并在 provenance / warnings 里显式注明。
DepthPolicy = Literal["strict", "truncate"]


class MinuteDepthExceededError(ValueError):
    """请求区间超出分钟数据源的可用深度。"""


def _as_date(value: date | datetime | str | None) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        raise ValueError("日期不能为空")
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"日期格式错误（应为 YYYY-MM-DD）: {value}") from exc


def normalize_period(period: str | int) -> str:
    """把周期入参归一化为分钟字符串，并校验在本模块支持范围内。"""
    text = str(period).strip()
    if text not in SUPPORTED_PERIODS:
        raise ValueError(
            f"不支持的分钟周期 {period!r}，支持: {', '.join(SUPPORTED_PERIODS)}"
        )
    return text


def period_minutes(period: str | int) -> int:
    return int(normalize_period(period))


def source_depth_days(period: str | int) -> dict[str, int]:
    """各源在该周期上的可回溯自然日上限。"""
    key = normalize_period(period)
    return {
        source: int(windows[key])
        for source, windows in SOURCE_DEPTH_DAYS.items()
        if key in windows
    }


def effective_depth_days(period: str | int) -> int:
    """有效深度 = 各源窗口的最大值（只要有一源能覆盖，就算可用）。"""
    windows = source_depth_days(period)
    if not windows:
        return 0
    return max(windows.values())


def depth_floor(period: str | int, as_of: date | datetime | str | None = None) -> date:
    """可用深度下界：早于该日期的区间任何源都取不到。"""
    as_of_date = _as_date(as_of) if as_of is not None else date.today()
    return as_of_date - timedelta(days=effective_depth_days(period))


def depth_report(as_of: date | datetime | str | None = None) -> dict[str, object]:
    """各周期的深度快照，供 diagnostics 暴露给调用方。"""
    as_of_date = _as_date(as_of) if as_of is not None else date.today()
    periods: dict[str, dict[str, object]] = {}
    for period in SUPPORTED_PERIODS:
        windows = source_depth_days(period)
        days = max(windows.values()) if windows else 0
        periods[period] = {
            "source_depth_days": windows,
            "effective_depth_days": days,
            "floor": (as_of_date - timedelta(days=days)).isoformat(),
        }
    return {
        "as_of": as_of_date.isoformat(),
        "note": "各源窗口滚动前移，深度仅用于前置裁决；实际返回区间见 data_provenance",
        "periods": periods,
    }


@dataclass(frozen=True)
class DepthDecision:
    """一次请求窗口的深度裁决结果。"""

    period: str
    policy: str
    requested_start: date
    requested_end: date
    effective_start: date
    depth_days: int
    depth_floor: date
    exceeded: bool
    note: str | None = None

    @property
    def effective_end(self) -> date:
        return self.requested_end

    @property
    def truncated(self) -> bool:
        return bool(self.exceeded and self.effective_start > self.requested_start)


def resolve_request_window(
    period: str | int,
    start_date: date | datetime | str,
    end_date: date | datetime | str,
    policy: DepthPolicy = "truncate",
    as_of: date | datetime | str | None = None,
) -> DepthDecision:
    """裁决请求窗口是否超出分钟源深度，并按策略给出可执行窗口。

    ``strict`` 下超深度直接抛 :class:`MinuteDepthExceededError`；``truncate`` 下截断到
    可用下界并在 ``note`` 里注明。两种策略下，若整个请求区间都落在可用深度之外，都
    会报错 —— 此时截断只会得到空区间，"返回空结果"是最难被发现的那种静默失败。
    """
    key = normalize_period(period)
    if policy not in ("strict", "truncate"):
        raise ValueError(f"未知深度策略 {policy!r}，支持: strict / truncate")

    start = _as_date(start_date)
    end = _as_date(end_date)
    if start > end:
        raise ValueError("start_date 不能晚于 end_date")

    as_of_date = _as_date(as_of) if as_of is not None else date.today()
    days = effective_depth_days(key)
    floor = as_of_date - timedelta(days=days)

    if end < floor:
        raise MinuteDepthExceededError(
            f"请求区间 {start}~{end} 完全落在 {key} 分钟线可用深度之外"
            f"（下界 {floor}，有效深度 {days} 自然日）"
        )

    exceeded = start < floor
    if exceeded and policy == "strict":
        windows = source_depth_days(key)
        raise MinuteDepthExceededError(
            f"请求起始 {start} 早于 {key} 分钟线可用深度下界 {floor}"
            f"（有效深度 {days} 自然日，各源 {windows}）；"
            "strict 策略下不返回不完整区间，请改用 truncate 或缩短区间"
        )

    effective_start = max(start, floor)
    note = None
    if exceeded:
        windows = source_depth_days(key)
        note = (
            f"{key} 分钟线有效深度 {days} 自然日（各源 {windows}），"
            f"可用下界 {floor}；请求起始 {start} 超出深度，"
            f"已截断至 {effective_start}，实际回测区间不完整"
        )

    return DepthDecision(
        period=key,
        policy=policy,
        requested_start=start,
        requested_end=end,
        effective_start=effective_start,
        depth_days=days,
        depth_floor=floor,
        exceeded=exceeded,
        note=note,
    )
