"""A 股交易时段与集合竞价语义（分钟级撮合用）。

日线回测把「一天」当成一根 bar，撮合时只需要开盘价与收盘价；分钟级必须显式处理
时段结构，否则「开盘成交」与「收盘成交」会落到同一根 bar 上：

- **集合竞价**：09:15–09:25 开盘集合竞价（09:25 产生开盘价），14:57–15:00 收盘集合
  竞价。分钟 K 线里没有 09:15–09:25 的 bar —— 开盘集合竞价的结果**就是当日第一根
  bar 的 open**，所以「在开盘价成交」等价于「在开盘集合竞价成交」，成交记录以
  ``fill_phase="call_auction"`` 标注；其余 bar 标注 ``"continuous"``。
  收盘集合竞价时段内不再有连续竞价成交，落在该时段的信号当日已无法成交，最早只能
  进下一交易日的开盘集合竞价 —— 由 ``minute.engine`` 用 :func:`is_closing_auction`
  判断并顺延。
- **连续竞价**：09:30–11:30 / 13:00–15:00，共 240 分钟。
- **停牌**：某标的某交易日整日无 bar，或整日成交量为 0，视为停牌/无行情，当日不可
  成交（持仓卖不出、买单不成交），一律顺延或作废并告警，不静默跳过。

时段用**收盘时刻**标识 bar：1min 的首根是 09:31、末根是 15:00；60min 的首根是
10:30、末根是 15:00（A 股 60 分钟线按 09:30–10:30 / 10:30–11:30 / 13:00–14:00 /
14:00–15:00 切分）。
"""

from __future__ import annotations

from datetime import time

import pandas as pd

#: 收盘集合竞价时段（14:57–15:00）。此时段内不再有连续竞价成交，落在其中的信号当日
#: 已无法成交 —— 这是本模块唯一被撮合链依赖的时段常量，开盘集合竞价时段（09:15–09:25）
#: 不需要常量：它的结果就是当日第一根 bar 的 open，见模块文档。
CLOSING_AUCTION = (time(14, 57), time(15, 0))

#: 连续竞价时段（起, 止），止为收盘时刻（含）
CONTINUOUS_SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))


def _minutes_of_day(value: time) -> int:
    return value.hour * 60 + value.minute


def _time_of_minutes(total: int) -> time:
    return time(total // 60, total % 60)


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


def bars_per_day(period: str | int) -> int:
    """该周期每个交易日的 bar 数。

    直接数时段表而不是 ``240 // period`` —— 后者只在周期整除 240 时与时段表一致，
    而时段表才是取数侧真正对齐的口径。
    """
    return len(session_bar_times(period))


def is_closing_auction(ts) -> bool:
    """该时刻是否落在收盘集合竞价时段内（14:57–15:00，含 15:00 的收盘成交）。"""
    moment = pd.Timestamp(ts).time()
    return CLOSING_AUCTION[0] <= moment <= CLOSING_AUCTION[1]


def is_halted_day(day_frame: pd.DataFrame | None) -> bool:
    """整日无 bar，或整日成交量为 0，视为停牌/无行情。

    ``volume`` **缺失或全为 NaN** 时返回 False：那是「源没给成交量」，不是「确实零成
    交」。两者混为一谈会让没有成交量字段的标的整天不可成交（买单作废、持仓卖不出）。
    """
    if day_frame is None or day_frame.empty:
        return True
    if "volume" not in day_frame.columns:
        return False
    try:
        volume = pd.to_numeric(day_frame["volume"], errors="coerce").dropna()
    except (TypeError, ValueError):  # pragma: no cover - 防御性
        return False
    if volume.empty:
        return False
    return float(volume.sum()) <= 0


def previous_close_by_session(frame: pd.DataFrame) -> dict[pd.Timestamp, float]:
    """每个交易日 → 该标的**前一交易日**收盘价，用于算涨跌停上下限。

    分钟 bar 里没有「昨日收盘」字段，只能由序列自身聚合。窗口首日没有前序交易日，
    故不出现在结果里 —— 该日的涨跌停检查会被跳过。调用方必须保证帧内含有回测首日之
    前的行情（``minute.service`` 默认向前多取若干自然日），否则引擎会就该日显式告警。
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
