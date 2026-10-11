"""基准与因子对齐到分钟 bar。

分钟级回测的净值曲线是分钟粒度，而基准（指数）与因子通常是**日线**。按日期直接
reindex 会把当日日线值铺到当日所有分钟 bar 上，产生**前视**：当日 10:00 的 bar 用上
了当日 15:00 才知道的日线收盘 / 因子值。本模块把「可用时点」显式化：

- ``available_from="session_close"``（默认，**因子**用）：第 D 个交易日的值最早在 D 日
  收盘后才可用，因此只作用于 **D 之后**的分钟 bar。无前视。D 日自身（含 15:00 收盘
  bar）一律不取用 D 的值，宁可保守。
- ``available_from="session_open"``（**基准展示**用）：第 D 日的值作用于 D 日全部分钟
  bar，得到「当日基准点位」的阶梯序列。这**含前视**，只能用于事后展示型曲线，不可
  用于生成信号。

基准若本身有分钟 bar，则直接对齐到分钟网格（``ffill``，无前视）；只有日线时降级为
阶梯序列并在 meta 里显式标注 ``resolution="daily"``，不静默假装是分钟基准。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import pandas as pd

#: 日线值在分钟网格上的可用时点
AvailableFrom = Literal["session_close", "session_open"]


def _grid(minute_index) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(minute_index)).sort_values()


def _daily_last_by_session(frame: pd.DataFrame, value_col: str) -> pd.Series:
    """日线帧 → 每交易日最后一个值（按交易日升序）。"""
    work = frame.copy()
    time_col = "datetime" if "datetime" in work.columns else None
    stamps = pd.to_datetime(work[time_col]) if time_col else pd.to_datetime(work.index)
    work = pd.DataFrame(
        {
            "session": pd.DatetimeIndex(stamps).normalize(),
            "value": pd.to_numeric(work[value_col], errors="coerce"),
        }
    ).dropna(subset=["value"])
    if work.empty:
        return pd.Series(dtype="float64")
    return work.groupby("session")["value"].last().sort_index()


def align_daily_to_minute(
    daily_frame: Optional[pd.DataFrame],
    minute_index,
    value_col: str = "close",
    available_from: AvailableFrom = "session_close",
) -> pd.Series:
    """把日线序列对齐到分钟网格，按 ``available_from`` 决定可用时点。

    Returns:
        与 ``minute_index`` 等长、同索引的 Series；无对应日线值的 bar 为 NaN。
    """
    grid = _grid(minute_index)
    if daily_frame is None or getattr(daily_frame, "empty", True):
        return pd.Series(index=grid, dtype="float64")
    if value_col not in daily_frame.columns:
        return pd.Series(index=grid, dtype="float64")

    daily = _daily_last_by_session(daily_frame, value_col)
    if daily.empty:
        return pd.Series(index=grid, dtype="float64")

    bar_sessions = grid.normalize()
    if available_from == "session_open":
        # 当日值作用于当日全部 bar（含前视，仅展示用）
        return pd.Series(
            daily.reindex(bar_sessions).to_numpy(), index=grid, dtype="float64"
        )
    if available_from != "session_close":
        raise ValueError(
            f"未知 available_from {available_from!r}，支持: session_close / session_open"
        )

    # session_close：bar 所在交易日 D 只能取 D **之前**最后一个日线交易日的值
    positions = daily.index.searchsorted(bar_sessions, side="left") - 1
    values = [float(daily.iloc[pos]) if pos >= 0 else float("nan") for pos in positions]
    return pd.Series(values, index=grid, dtype="float64")


def align_to_minute_grid(
    frame: Optional[pd.DataFrame],
    minute_index,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """把（可能是分钟粒度的）序列重采样到分钟网格：前向填充，不回填（无前视）。"""
    grid = _grid(minute_index)
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(index=grid, columns=columns)
    work = frame.copy()
    if "datetime" not in work.columns:
        work = work.reset_index().rename(columns={"index": "datetime"})
    work["datetime"] = pd.to_datetime(work["datetime"])
    work = (
        work.drop_duplicates(subset=["datetime"], keep="last")
        .set_index("datetime")
        .sort_index()
    )
    if columns:
        work = work[[col for col in columns if col in work.columns]]
    return work.reindex(grid.union(work.index)).sort_index().ffill().reindex(grid)


def align_benchmark_to_minute(
    benchmark_frame: Optional[pd.DataFrame],
    minute_index,
    value_col: str = "close",
) -> tuple[pd.Series, dict[str, Any]]:
    """基准对齐到分钟网格，并返回 (序列, meta)。

    基准自带分钟 bar 时按分钟网格前向填充；只有日线时降级为按交易日对齐的阶梯序列，
    在 meta 里标注 ``resolution="daily"`` 与降级说明 —— 日线基准在分钟曲线上的对比
    只能到日粒度，这一点必须显式，不能让人以为拿到了分钟基准。
    """
    grid = _grid(minute_index)
    if benchmark_frame is None or getattr(benchmark_frame, "empty", True):
        return (
            pd.Series(index=grid, dtype="float64"),
            {
                "resolution": "none",
                "lookahead": False,
                "note": "基准数据不可用",
            },
        )

    stamps = pd.to_datetime(
        benchmark_frame["datetime"]
        if "datetime" in benchmark_frame.columns
        else benchmark_frame.index
    )
    intraday = bool((stamps.dt.normalize().duplicated()).any())

    if intraday:
        series = align_to_minute_grid(benchmark_frame, grid, columns=[value_col])[
            value_col
        ]
        return series.astype("float64"), {
            "resolution": "minute",
            "lookahead": False,
            "note": "基准自带分钟 bar，已按分钟网格前向填充对齐",
        }

    series = align_daily_to_minute(
        benchmark_frame, grid, value_col=value_col, available_from="session_open"
    )
    return series, {
        "resolution": "daily",
        "lookahead": True,
        "note": (
            "基准仅有日线，已按交易日对齐为阶梯序列；当日 bar 使用当日日线值，"
            "含前视，仅可用于展示型对比，不可用于生成信号"
        ),
    }
