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


def test_estimate_bar_count_scales_with_period():
    start, end = "2026-01-05", "2026-02-05"
    assert estimate_fetch_bars("1", start, end) == 5 * estimate_fetch_bars(
        "5", start, end
    )
    assert estimate_fetch_bars("5", start, end) == 12 * estimate_fetch_bars(
        "60", start, end
    )


def _business_days(start: str, end: str) -> int:
    """窗口内的工作日数。

    仓库里没有交易日历，工作日是真实交易日的**上界**（节假日只会更少）—— 估算根数连
    工作日都覆盖不了，就一定覆盖不了真实交易日。
    """
    return len(pd.bdate_range(start, end))


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-06-15", "2026-10-02"),  # 评审给出的反例：109 自然日 / 80 个工作日
        ("2025-09-12", "2026-01-07"),  # 1min 有效深度边界（约 120 自然日）
        ("2025-01-01", "2026-01-01"),  # 5/15/30/60min 的长窗口
        ("2026-06-15", "2026-06-30"),  # 短窗口
        ("2026-09-28", "2026-10-09"),  # 跨国庆，工作日被压缩
    ],
)
def test_estimate_bar_count_never_underestimates_the_window(start, end):
    """``count`` 是源侧硬上限（从最新一根往前最多取 count 根），估小会静默砍掉窗口头部
    且不可恢复 —— 所以估算必须只往多的方向偏。"""
    for period, per_day in (("1", 240), ("5", 48), ("60", 4)):
        estimated = estimate_bar_count(
            pd.Timestamp(start).date(), pd.Timestamp(end).date(), period
        )
        lower_bound = _business_days(start, end) * per_day
        assert estimated >= lower_bound, (
            f"{start}~{end} period={period}: 估算 {estimated} 根 < 工作日下界 "
            f"{lower_bound} 根"
        )


class _ProviderLikeFetch:
    """复刻 provider 的取数语义：**从最新一根往前最多取 count 根**，再按区间过滤。

    ``count`` 是硬上限，不是提示 —— 估小就等于向源要一段它给不出的历史，窗口头部被静默
    砍掉。用真实的 provider 语义（而不是「返回整段历史」的宽松假函数）才能验出估算方向。
    """

    def __init__(self, history: pd.DataFrame, source: str = "mootdx"):
        self.history = history
        self.source = source
        self.calls: list[dict] = []

    def __call__(self, symbol, period, start_datetime, end_datetime, count, deadline):
        self.calls.append({"count": count, "start_datetime": start_datetime})
        window = self.history.tail(count)
        mask = (window["datetime"] >= pd.Timestamp(start_datetime)) & (
            window["datetime"] <= pd.Timestamp(end_datetime)
        )
        out = window[mask].copy()
        out.attrs["source"] = self.source
        return out


def test_loader_covers_the_whole_window_when_count_is_the_binding_limit():
    """评审的反例：源提供完整 80 个交易日，count 是源侧硬上限。

    估算偏小时源只返回「最新 count 根」，窗口头部被砍掉并误报 gap。
    """
    days = pd.bdate_range("2026-06-15", "2026-10-02")
    assert len(days) == 80
    history = _minute_frame([day.strftime("%Y-%m-%d") for day in days])
    fetch = _ProviderLikeFetch(history)
    loader = MinuteDataLoader(fetch_minute=fetch)
    result = loader.load("000001", "1", "2026-06-15", "2026-10-02", as_of="2026-10-05")

    assert result.provenance.bar_count == 80 * 240
    assert result.provenance.actual_start == "2026-06-15 09:31:00"
    assert result.provenance.gap is False


def test_loader_lead_start_extends_fetch_without_changing_adjudication():
    """``lead_start`` 只改变向源请求的窗口，裁决与留痕仍以用户区间为准。"""
    fetch = _FakeFetch({"000001": _minute_frame(["2026-01-05"])})
    loader = MinuteDataLoader(fetch_minute=fetch)
    result = loader.load(
        "000001", "1", "2026-01-05", "2026-01-06", as_of=AS_OF, lead_start="2025-12-29"
    )

    assert fetch.calls[0]["start_datetime"].startswith("2025-12-29")
    assert result.decision.requested_start.isoformat() == "2026-01-05"
    assert result.decision.effective_start.isoformat() == "2026-01-05"
    assert result.provenance.requested_start == "2026-01-05"
    assert result.provenance.truncated is False


def test_loader_lead_start_never_shrinks_the_adjudicated_window():
    """``lead_start`` 晚于用户区间起点时忽略 —— 不能借它少取用户要的数据。"""
    fetch = _FakeFetch({"000001": _minute_frame(["2026-01-05"])})
    loader = MinuteDataLoader(fetch_minute=fetch)
    loader.load(
        "000001", "1", "2026-01-05", "2026-01-06", as_of=AS_OF, lead_start="2026-01-06"
    )
    assert fetch.calls[0]["start_datetime"].startswith("2026-01-05")


def test_loader_clamps_lead_start_to_depth_floor_under_strict_policy():
    """缓冲不是用户请求：它落在深度之外时只夹紧，不能让 ``strict`` 报错。"""
    fetch = _FakeFetch({"000001": _minute_frame(["2026-01-05"])})
    loader = MinuteDataLoader(fetch_minute=fetch)
    # as_of 2026-01-10 → 1min 下界 2025-09-12；lead_start 2025-01-01 远在界外
    result = loader.load(
        "000001",
        "1",
        "2026-01-05",
        "2026-01-06",
        policy="strict",
        as_of=AS_OF,
        lead_start="2025-01-01",
    )

    assert fetch.calls[0]["start_datetime"].startswith("2025-09-12")
    assert result.decision.truncated is False
    assert result.provenance.truncated is False


def test_loader_records_original_request_window_and_effective_start():
    fetch = _FakeFetch({"000001": _minute_frame(["2026-01-05"])})
    loader = MinuteDataLoader(fetch_minute=fetch)
    result = loader.load("000001", "1", "2025-01-01", "2026-01-06", as_of=AS_OF)

    # 原始请求区间原样保留，截断后的有效区间另给 —— 否则事后看不出被截掉多少
    assert result.provenance.requested_start == "2025-01-01"
    assert result.decision.effective_start.isoformat() == "2025-09-12"
    assert result.provenance.truncated is True


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
        as_of=kwargs.pop("as_of", AS_OF),
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


def test_service_always_reserves_limit_reference_buffer_before_start():
    """首日前收盘价只能靠帧内的前一交易日聚合，取数区间必须始终向前留缓冲 ——
    与 warmup_sessions 无关（默认 0 时也要有），否则回测首日静默失去涨跌停约束。"""
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06"])}
    result, fetch = _run_service(
        frames, None, start_date="2026-01-06", end_date="2026-01-06"
    )

    load_window = result["diagnostics"]["load_window"]
    assert load_window["warmup_sessions"] == 0
    assert load_window["limit_reference_days"] > 0
    assert load_window["desired_start"] < "2026-01-06"
    # 缓冲落在深度之内，所以实际生效的取数起点就是带缓冲的那个，没有被夹紧
    assert load_window["buffer_clamped"] is False
    assert load_window["start"] == load_window["desired_start"]
    assert fetch.calls[0]["start_datetime"].startswith(load_window["start"])


def test_service_records_per_symbol_depth_truncation():
    """深度截断是**逐标的**的事实：交给 loader 原始区间，让它自己裁决并留痕。

    顶层先截断再交给 loader 会让逐标的的 truncated / depth_exceeded / depth_note 恒为
    空值，truncated_count 与对应告警因此不可达。
    """
    frames = {"000001": _minute_frame(["2026-01-05", "2026-01-06", "2026-01-07"])}
    result, _ = _run_service(
        frames, None, start_date="2025-01-01", end_date="2026-01-07"
    )

    provenance = result["diagnostics"]["data_provenance"]
    assert provenance["truncated_count"] == 1
    assert provenance["degraded"] is True
    row = provenance["symbols"][0]
    assert row["truncated"] is True
    assert row["depth_exceeded"] is True
    assert row["depth_note"] and "截断" in row["depth_note"]
    # 逐标的记的是**原始**请求区间，截断后的区间由顶层 depth_decision 给
    assert row["requested_start"] < result["diagnostics"]["load_window"]["start"]
    assert any("取数区间被深度截断" in w for w in result["warnings"])


def test_service_strict_policy_judges_the_user_window_not_the_internal_buffer():
    """``strict`` 只裁决**用户请求区间**：内部向前多取的缓冲被深度挡住时夹紧即可。

    回归：把内部缓冲一起送进裁决，会让一个完全落在深度内的请求被判死，报错还拿内部
    起点当「请求起始」让用户缩短区间 —— 而用户的区间本来就在深度内。
    """
    frames = {"000001": _minute_frame(["2026-06-10", "2026-06-11"])}
    # as_of 2026-10-05 → 1min 有效深度 120 天 → 下界 2026-06-07，用户起点 06-10 在界内
    result, fetch = _run_service(
        frames,
        None,
        start_date="2026-06-10",
        end_date="2026-10-05",
        depth_policy="strict",
        as_of="2026-10-05",
    )

    assert result["diagnostics"]["depth_decision"]["truncated"] is False
    load_window = result["diagnostics"]["load_window"]
    # 缓冲（warmup 1 天 + 首日前收盘 7 天）想要 2026-06-02，被夹到深度下界
    assert load_window["desired_start"] == "2026-06-02"
    assert load_window["buffer_clamped"] is True
    assert load_window["start"] == "2026-06-07"
    assert fetch.calls[0]["start_datetime"].startswith("2026-06-07")
    assert not any("深度不足" in w for w in result["warnings"])


def test_service_does_not_report_truncation_when_only_the_buffer_is_clamped():
    """缓冲被夹紧 ≠ 用户区间被截断。

    回归：把内部缓冲算进用户区间的裁决后，同一请求会收到「结果不覆盖原始请求起点
    2026-06-10」的告警，而 ``equity_curve`` 恰恰从 2026-06-10 09:31 开始 —— 归因错的
    告警比没有告警更糟。
    """
    frames = {"000001": _minute_frame(["2026-06-10", "2026-06-11"])}
    result, _ = _run_service(
        frames,
        None,
        start_date="2026-06-10",
        end_date="2026-10-05",
        as_of="2026-10-05",
    )

    diagnostics = result["diagnostics"]
    assert diagnostics["depth_decision"]["truncated"] is False
    assert diagnostics["load_window"]["buffer_clamped"] is True
    assert not any("深度不足" in w for w in result["warnings"])
    # 结果确实覆盖了用户起点 —— 说「不覆盖」就是假的
    assert result["equity_curve"][0]["datetime"].startswith("2026-06-10")
