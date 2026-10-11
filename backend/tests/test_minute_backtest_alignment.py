"""VEW-66 基准与因子对齐到分钟 bar。

分钟级回测的净值是分钟粒度，基准与因子却是日线。这里锁定「可用时点」这一核心语义：

- 因子（``session_close``）：第 D 个交易日的值只作用于 **D 之后**的分钟 bar ——
  D 日自己的 15:00 收盘 bar 也不得取用 D 的值，否则就是前视；
- 基准展示（``session_open``）：当日值作用于当日全部 bar，含前视，仅可用于展示；
- 基准自带分钟 bar 时按分钟网格对齐；只有日线时降级为阶梯序列并**显式标注**
  ``resolution="daily"``，不假装是分钟基准。
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.services.backtest.minute.alignment import (
    align_benchmark_to_minute,
    align_daily_to_minute,
    align_to_minute_grid,
)
from app.services.backtest.minute.service import align_factor_to_minute

_GRID = pd.DatetimeIndex(
    [
        "2026-01-05 09:31:00",
        "2026-01-05 15:00:00",
        "2026-01-06 09:31:00",
        "2026-01-06 15:00:00",
        "2026-01-07 09:31:00",
        "2026-01-07 15:00:00",
    ]
)


def _daily(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                ["2026-01-05", "2026-01-06", "2026-01-07"][: len(closes)]
            ),
            "close": closes,
        }
    )


def _values(series: pd.Series) -> list[float | None]:
    return [None if pd.isna(value) else float(value) for value in series.tolist()]


def test_session_close_mode_never_uses_same_session_value():
    series = align_daily_to_minute(
        _daily([100.0, 110.0, 121.0]), _GRID, available_from="session_close"
    )
    # 01-05 无前序交易日 → 无值；01-06 用 01-05 的值；01-07 用 01-06 的值。
    # 关键在于 01-05 15:00（当日收盘 bar）拿不到 01-05 自己的收盘值 —— 否则即前视。
    assert _values(series) == [None, None, 100.0, 100.0, 110.0, 110.0]


def test_session_open_mode_uses_same_session_value_for_display():
    series = align_daily_to_minute(
        _daily([100.0, 110.0, 121.0]), _GRID, available_from="session_open"
    )
    assert _values(series) == [100.0, 100.0, 110.0, 110.0, 121.0, 121.0]


def test_align_factor_to_minute_matches_session_close_mode():
    factor = align_factor_to_minute(_daily([100.0, 110.0, 121.0]), _GRID)
    assert _values(factor) == _values(
        align_daily_to_minute(
            _daily([100.0, 110.0, 121.0]), _GRID, available_from="session_close"
        )
    )


def test_unknown_available_from_raises():
    with pytest.raises(ValueError):
        align_daily_to_minute(_daily([100.0]), _GRID, available_from="session_noon")


def test_missing_or_empty_daily_frame_yields_all_nan():
    for frame in (None, pd.DataFrame({"datetime": [], "close": []})):
        series = align_daily_to_minute(frame, _GRID)
        assert series.isna().all()
        assert len(series) == len(_GRID)


def test_missing_value_column_yields_all_nan():
    frame = pd.DataFrame({"datetime": pd.to_datetime(["2026-01-05"]), "px": [1.0]})
    assert align_daily_to_minute(frame, _GRID, value_col="close").isna().all()


def test_align_to_minute_grid_forward_fills_without_backfill():
    frame = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                ["2026-01-05 09:31:00", "2026-01-05 15:00:00", "2026-01-07 09:31:00"]
            ),
            "close": [1.0, 2.0, 3.0],
        }
    )
    aligned = align_to_minute_grid(frame, _GRID, columns=["close"])["close"]
    # 01-06 没有源数据，前向填充 01-05 的值；不回填（01-05 09:31 之前的 bar 不存在于此网格）
    assert _values(aligned) == [1.0, 2.0, 2.0, 2.0, 3.0, 3.0]


def test_daily_benchmark_is_flagged_daily_with_lookahead():
    series, meta = align_benchmark_to_minute(_daily([100.0, 110.0, 121.0]), _GRID)
    assert meta["resolution"] == "daily"
    assert meta["lookahead"] is True
    assert "前视" in meta["note"]
    assert _values(series) == [100.0, 100.0, 110.0, 110.0, 121.0, 121.0]


def test_intraday_benchmark_aligns_on_minute_grid_without_lookahead():
    frame = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                ["2026-01-06 09:31:00", "2026-01-06 15:00:00", "2026-01-07 09:31:00"]
            ),
            "close": [10.0, 11.0, 12.0],
        }
    )
    series, meta = align_benchmark_to_minute(frame, _GRID)
    assert meta["resolution"] == "minute"
    assert meta["lookahead"] is False
    assert _values(series) == [None, None, 10.0, 11.0, 12.0, 12.0]


def test_missing_benchmark_reports_none_resolution():
    series, meta = align_benchmark_to_minute(None, _GRID)
    assert meta["resolution"] == "none"
    assert meta["lookahead"] is False
    assert series.isna().all()
