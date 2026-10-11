"""A 股交易时段与集合竞价语义（分钟级撮合用）。

日线回测把「一天」当成一根 bar，撮合时只需要开盘价与收盘价；分钟级必须显式处理
时段结构，否则「开盘成交」与「收盘成交」会落到同一根 bar 上：

- **集合竞价**：09:15–09:25 开盘集合竞价（09:25 产生开盘价），14:57–15:00 收盘集合
  竞价。分钟 K 线里没有 09:15–09:25 的 bar —— 开盘集合竞价的结果**就是当日第一根
  bar 的 open**，所以「在开盘价成交」等价于「在开盘集合竞价成交」，成交记录以
  ``fill_phase="call_auction"`` 标注；其余 bar 标注 ``"continuous"``。
- **连续竞价**：09:30–11:30 / 13:00–15:00，共 240 分钟。
- **停牌**：某标的某交易日整日无 bar，或整日成交量为 0，视为停牌/无行情，当日不可
  成交（持仓卖不出、买单不成交），一律顺延或作废并告警，不静默跳过。

时段用**收盘时刻**标识 bar：1min 的首根是 09:31、末根是 15:00；60min 的首根是
10:30、末根是 15:00（A 股 60 分钟线按 09:30–10:30 / 10:30–11:30 / 13:00–14:00 /
14:00–15:00 切分）。
"""

from __future__ import annotations

from datetime import date, time

import pandas as pd

#: 连续竞价总分钟数（上午 120 + 下午 120）
MINUTES_PER_SESSION = 240

#: 开盘集合竞价时段（09:25 出开盘价，该结果落在当日第一根 bar 的 open 上）
OPENING_AUCTION = (time(9, 15), time(9, 25))

#: 收盘集合竞价时段（14:57–15:00）。此时段内产生的信号当日已无法连续竞价成交。
CLOSING_AUCTION = (time(14, 57), time(15, 0))

#: 连续竞价时段（起, 止），止为收盘时刻（含）
CONTINUOUS_SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))


def _minutes_of_day(value: time) -> int:
    return value.hour * 60 + value.minute


def _time_of_minutes(total: int) -> time:
    return time(total // 60, total % 60)


def bars_per_day(period: str | int) -> int:
    """该周期每个交易日的 bar 数（240 分钟 / 周期）。"""
    minutes = int(str(period).strip())
    if minutes <= 0:
        raise ValueError(f"周期必须为正整数分钟: {period!r}")
    return MINUTES_PER_SESSION // minutes


def session_bar_times(period: str | int) -> list[time]:
    """该周期的 bar 收盘时刻列表（上午段 + 下午段，按时间升序）。"""
    minutes = int(str(period).strip())
    if minutes <= 0:
        raise ValueError(f"周期必须为正整数分钟: {period!r}")
    times: list[time] = []
    for start, end in CONTINUOUS_SESSIONS:
        cursor = _minutes_of_day(start) + minutes
        limit = _minutes_of_day(end)
        while cursor <= limit:
            times.append(_time_of_minutes(cursor))
            cursor += minutes
    return times


def open_bar_time(period: str | int) -> time:
    """当日第一根 bar 的收盘时刻（开盘集合竞价的成交价落在它的 open 上）。"""
    return session_bar_times(period)[0]


def is_session_open_bar(ts, period: str | int) -> bool:
    """该 bar 是否为当日第一根 —— 即开盘集合竞价的成交 bar。"""
    return pd.Timestamp(ts).time() == open_bar_time(period)


def is_closing_auction(ts) -> bool:
    """该时刻是否落在收盘集合竞价时段内。"""
    moment = pd.Timestamp(ts).time()
    return CLOSING_AUCTION[0] <= moment <= CLOSING_AUCTION[1]


def session_date_of(ts) -> pd.Timestamp:
    """bar 时刻 → 所属交易日（归一化到 00:00）。"""
    return pd.Timestamp(ts).normalize()


def is_halted_day(day_frame: pd.DataFrame | None) -> bool:
    """整日无 bar，或整日成交量为 0，视为停牌/无行情。"""
    if day_frame is None or day_frame.empty:
        return True
    if "volume" not in day_frame.columns:
        return False
    try:
        return (
            float(pd.to_numeric(day_frame["volume"], errors="coerce").fillna(0).sum())
            <= 0
        )
    except (TypeError, ValueError):  # pragma: no cover - 防御性
        return False


def previous_close_by_session(frame: pd.DataFrame) -> dict[pd.Timestamp, float]:
    """每个交易日 → 该标的**前一交易日**收盘价，用于算涨跌停上下限。

    分钟 bar 里没有「昨日收盘」字段，只能由序列自身聚合。窗口首日没有前序交易日，
    故不出现在结果里（该日的涨跌停检查会被跳过 —— 回测用 warmup 覆盖首日，见
    ``minute.service``）。
    """
    if frame is None or frame.empty or "close" not in frame.columns:
        return {}
    work = pd.DataFrame(
        {
            "session": pd.to_datetime(frame["datetime"]).dt.normalize(),
            "close": pd.to_numeric(frame["close"], errors="coerce"),
        }
    ).dropna(subset=["close"])
    if work.empty:
        return {}
    last_close = work.groupby("session")["close"].last().sort_index()
    sessions = list(last_close.index)
    return {
        sessions[idx]: float(last_close.iloc[idx - 1])
        for idx in range(1, len(sessions))
    }


def session_dates(frames: dict[str, pd.DataFrame]) -> list[date]:
    """多标的分钟帧的交易日并集（升序）。"""
    seen: set[pd.Timestamp] = set()
    for frame in frames.values():
        if frame is None or frame.empty:
            continue
        seen.update(pd.to_datetime(frame["datetime"]).dt.normalize().unique())
    return [pd.Timestamp(ts).date() for ts in sorted(seen)]
