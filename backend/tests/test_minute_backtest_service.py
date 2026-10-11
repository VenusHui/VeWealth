"""VEW-66 分钟级回测的数据入口与编排：深度暴露 + 取数留痕。

纯网络按需取数下最容易出错、也最难被发现的两件事，在这里逐条锁定：

- **深度**：请求超出源窗口时必须报错（``strict``）或截断并注明（``truncate``），
  传给数据源的窗口也必须是截断后的窗口 —— 不能静默返回不完整区间；
- **留痕**：结果里必须记录取数时间与各源**实际返回**的区间，否则事后无法判断一次
  回测究竟跑在什么数据上（源窗口滚动前移，同区间不可复现）。

取数函数与基准取数都通过注入替换，测试不依赖任何活源。
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.services.backtest.costs import CostModel
from app.services.backtest.minute.data import (
    MinuteDataLoader,
    estimate_bar_count,
    provenance_summary,
)
from app.services.backtest.minute.depth import MinuteDepthExceededError
from app.services.backtest.minute.service import (
    estimate_fetch_bars,
    run_minute_backtest,
)
from app.services.backtest.minute.session import session_bar_times

AS_OF = "2026-01-10"
_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]


def _minute_frame(days: list[str], price: float = 10.0) -> pd.DataFrame:
    rows = [
        [f"{day} {t.strftime('%H:%M')}:00", price, price, price, price, 1000.0]
        for day in days
        for t in session_bar_times("1")
    ]
    frame = pd.DataFrame(rows, columns=_COLUMNS)
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    return frame


class _FakeFetch:
    """记录调用参数的假取数函数。"""

    def __init__(self, frames: dict[str, pd.DataFrame], source: str = "mootdx"):
        self.frames = frames
        self.source = source
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def __call__(self, symbol, period, start_datetime, end_datetime, count, deadline):
        self.calls.append(
            {
                "symbol": symbol,
                "period": period,
                "start_datetime": start_datetime,
                "end_datetime": end_datetime,
                "count": count,
                "deadline": deadline,
            }
        )
        if self.error is not None:
            raise self.error
        frame = self.frames.get(symbol)
        if frame is None:
            return None
        out = frame.copy()
        out.attrs["source"] = self.source
        return out


def _loader(frames: dict[str, pd.DataFrame], source: str = "mootdx"):
    fetch = _FakeFetch(frames, source=source)
    return MinuteDataLoader(fetch_minute=fetch), fetch


def test_loader_records_source_window_bar_count_and_fetch_time():
    fetch = _FakeFetch({"000001": _minute_frame(["2026-01-05", "2026-01-06"])})
    loader = MinuteDataLoader(fetch_minute=fetch)
    result = loader.load("000001", "1", "2026-01-05", "2026-01-06", as_of=AS_OF)

    assert result.provenance.source == "mootdx"
    assert result.provenance.period == "1"
    assert result.provenance.bar_count == 480
    assert result.provenance.actual_start == "2026-01-05 09:31:00"
    assert result.provenance.actual_end == "2026-01-06 15:00:00"
    assert result.provenance.gap is False
    assert result.provenance.truncated is False
    assert result.provenance.depth_exceeded is False
    # 取数时刻必须留痕：源窗口滚动，不记就无法复现判断
    assert result.provenance.fetched_at
    pd.Timestamp(result.provenance.fetched_at)


def test_loader_flags_gap_when_source_returns_less_than_requested():
    fetch = _FakeFetch({"000001": _minute_frame(["2026-01-05", "2026-01-06"])})
    loader = MinuteDataLoader(fetch_minute=fetch)
    result = loader.load("000001", "1", "2026-01-05", "2026-01-09", as_of=AS_OF)

    assert result.provenance.gap is True
    assert result.provenance.actual_end == "2026-01-06 15:00:00"


def test_loader_truncates_to_depth_floor_and_passes_clamped_window_to_source():
    fetch = _FakeFetch({"000001": _minute_frame(["2026-01-05"])})
    loader = MinuteDataLoader(fetch_minute=fetch)
    # as_of 2026-01-10，1min 有效深度 120 天 → 下界 2025-09-12
    result = loader.load("000001", "1", "2025-01-01", "2026-01-06", as_of=AS_OF)

    assert result.decision.requested_start.isoformat() == "2025-01-01"
    assert result.decision.effective_start.isoformat() == "2025-09-12"
    assert result.provenance.truncated is True
    assert result.provenance.depth_exceeded is True
    assert result.provenance.depth_note and "截断" in result.provenance.depth_note
    # 传给数据源的必须是截断后的窗口，否则等于向源要一段它给不出的数据
    assert fetch.calls[0]["start_datetime"].startswith("2025-09-12")


def test_loader_strict_policy_raises_instead_of_returning_partial():
    fetch = _FakeFetch({"000001": _minute_frame(["2026-01-05"])})
    loader = MinuteDataLoader(fetch_minute=fetch)
    with pytest.raises(MinuteDepthExceededError):
        loader.load(
            "000001", "1", "2025-01-01", "2026-01-06", policy="strict", as_of=AS_OF
        )
    assert fetch.calls == []


def test_loader_converts_source_exception_into_failure_reason():
    fetch = _FakeFetch({})
    fetch.error = RuntimeError("socket closed")
    loader = MinuteDataLoader(fetch_minute=fetch)
    result = loader.load("000001", "1", "2026-01-05", "2026-01-06", as_of=AS_OF)

    assert result.df is None
    assert "socket closed" in result.provenance.failure_reason
    assert result.provenance.gap is True


def test_loader_marks_empty_result_with_failure_reason():
    fetch = _FakeFetch({})
    loader = MinuteDataLoader(fetch_minute=fetch)
    result = loader.load("000001", "1", "2026-01-05", "2026-01-06", as_of=AS_OF)
    assert result.df is None
    assert result.provenance.failure_reason


def test_estimate_bar_count_scales_with_period_and_span():
    assert (
        estimate_bar_count(
            pd.Timestamp("2026-01-05").date(), pd.Timestamp("2026-01-05").date(), "1"
        )
        == 240
    )
    assert estimate_fetch_bars("5", "2026-01-05", "2026-01-05") == 48
    # 同一区间下 1min 的根数是 5min 的 5 倍
    assert estimate_fetch_bars(
        "1", "2026-01-05", "2026-02-05"
    ) == 5 * estimate_fetch_bars("5", "2026-01-05", "2026-02-05")


def test_provenance_summary_aggregates_sources_gaps_and_window():
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06"])}
    loader, _ = _loader(frames, source="tencent")
    results = [
        loader.load("000001", "1", "2026-01-05", "2026-01-06", as_of=AS_OF),
        loader.load("000002", "1", "2026-01-05", "2026-01-06", as_of=AS_OF),
    ]
    summary = provenance_summary(results)

    # 无数据的那只不带源名，不能被算进 tencent 的账上
    assert summary["source_counts"] == {"tencent": 1, "none": 1}
    assert summary["failure_count"] == 1  # 000002 无数据
    assert summary["degraded"] is True
    assert summary["fetched_at_min"] and summary["fetched_at_max"]
    assert summary["actual_window"]["start"] == "2026-01-05 09:31:00"
    assert summary["actual_window"]["end"] == "2026-01-06 15:00:00"
    assert {row["symbol"] for row in summary["symbols"]} == {"000001", "000002"}


# ---------------------------------------------------------------------------
# 编排入口
# ---------------------------------------------------------------------------


def _run_service(frames, orders, **kwargs):
    loader, fetch = _loader(frames, source=kwargs.pop("source", "mootdx"))
    result = run_minute_backtest(
        symbols=kwargs.pop("symbols", ["000001"]),
        orders_df=orders,
        period=kwargs.pop("period", "1"),
        start_date=kwargs.pop("start_date", "2026-01-05"),
        end_date=kwargs.pop("end_date", "2026-01-07"),
        initial_cash=100000.0,
        cost_model=CostModel(),
        loader=loader,
        as_of=AS_OF,
        **kwargs,
    )
    return result, fetch


def test_service_end_to_end_records_provenance_and_uses_daily_metrics_basis():
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06", "2026-01-07"])}
    orders = pd.DataFrame([{"symbol": "000001", "trade_date": "2026-01-05"}])
    result, _ = _run_service(frames, orders)

    # 分钟净值曲线 vs 日频指标基准：两者都在，且指标基准是日频
    assert len(result["equity_curve"]) == 720
    assert len(result["equity_curve_eod"]) == 3
    assert result["diagnostics"]["metrics_basis"] == "session_eod"
    assert result["summary"]["total_trades"] == 2
    assert len(result["trades"]) == 2
    assert len(result["positions_snapshot"]) == 3

    diagnostics = result["diagnostics"]
    assert diagnostics["period"] == "1"
    assert diagnostics["bars_per_session"] == 240
    assert diagnostics["depth_decision"]["truncated"] is False
    assert diagnostics["depth"]["periods"]["1"]["effective_depth_days"] == 120
    provenance = diagnostics["data_provenance"]
    assert provenance["source_counts"] == {"mootdx": 1}
    assert provenance["fetched_at_min"]
    assert provenance["symbols"][0]["actual_start"] == "2026-01-05 09:31:00"
    assert provenance["symbols"][0]["actual_end"] == "2026-01-07 15:00:00"


def test_service_warns_when_request_exceeds_depth_and_returns_truncated_range():
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06", "2026-01-07"])}
    orders = pd.DataFrame([{"symbol": "000001", "trade_date": "2026-01-05"}])
    result, fetch = _run_service(
        frames, orders, start_date="2025-01-01", end_date="2026-01-07"
    )

    assert result["diagnostics"]["depth_decision"]["truncated"] is True
    assert result["diagnostics"]["load_window"]["start"] == "2025-09-12"
    assert result["diagnostics"]["requested_window"]["start"] == "2025-01-01"
    assert any("深度不足" in w for w in result["warnings"])
    assert fetch.calls[0]["start_datetime"].startswith("2025-09-12")


def test_service_strict_policy_raises():
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06"])}
    with pytest.raises(MinuteDepthExceededError):
        _run_service(
            frames,
            None,
            start_date="2025-01-01",
            end_date="2026-01-06",
            depth_policy="strict",
        )


def test_service_raises_when_no_symbol_has_data():
    with pytest.raises(ValueError) as excinfo:
        _run_service({}, None)
    assert "无可用分钟数据" in str(excinfo.value)


def test_service_reports_unavailable_symbols_as_warnings():
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06", "2026-01-07"])}
    result, _ = _run_service(
        frames,
        pd.DataFrame([{"symbol": "000001", "trade_date": "2026-01-05"}]),
        symbols=["000001", "000002"],
    )
    assert result["diagnostics"]["symbols_with_data"] == 1
    assert any("000002" in w for w in result["warnings"])


def test_service_aligns_daily_benchmark_and_flags_lookahead():
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06", "2026-01-07"])}
    benchmark_frame = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "close": [3000.0, 3030.0, 3060.0],
        }
    )
    result, _ = _run_service(
        frames,
        None,
        benchmark="000300",
        benchmark_loader=lambda code, start, end: benchmark_frame,
    )
    meta = result["diagnostics"]["benchmark"]
    assert meta["code"] == "000300"
    assert meta["resolution"] == "daily"
    assert meta["lookahead"] is True
    assert meta["points"] > 0
    assert result["benchmark_curve"][0]["value_norm"] == 1.0


def test_service_benchmark_failure_does_not_fail_the_run():
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06"])}

    def _boom(code, start, end):
        raise RuntimeError("benchmark down")

    result, _ = _run_service(frames, None, benchmark="000300", benchmark_loader=_boom)
    meta = result["diagnostics"]["benchmark"]
    assert meta["resolution"] == "none"
    assert "benchmark down" in meta["note"]


def test_service_rejects_empty_symbols_and_inverted_window():
    frames = {"000001": _minute_frame(["2026-01-05"])}
    with pytest.raises(ValueError):
        _run_service(frames, None, symbols=[])
    with pytest.raises(ValueError):
        _run_service(frames, None, start_date="2026-01-07", end_date="2026-01-05")


def test_service_warmup_extends_load_window_but_keeps_trade_start():
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06"])}
    result, fetch = _run_service(
        frames, None, start_date="2026-01-06", end_date="2026-01-06", warmup_sessions=5
    )
    # warmup 只向前扩取数区间；净值仍从用户区间起点开始
    assert result["diagnostics"]["load_window"]["start"] < "2026-01-06"
    assert result["equity_curve"][0]["datetime"].startswith("2026-01-06")
    assert fetch.calls[0]["start_datetime"] < "2026-01-06"
