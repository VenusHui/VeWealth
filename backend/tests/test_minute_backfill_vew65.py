"""历史分钟数据回填（VEW-65 P1）单元测试。

覆盖的契约：
- 安全闸门：开关未开 / 未显式 confirm 一律拒绝，绝不静默取数落盘；
- 计划：体积估算按**实测** B/row（不是 P0 的合成口径），且不取数、不落盘；
- 区间取数：区间外 bar 被裁掉、按 trade_date 逐日分区写入、与 P0 幂等契约一致；
- 断点续跑：completed / empty 跳过，failed 重试；
- **不静默截断**：预算耗尽或覆盖不足记可重试失败（数据仍落盘），新上市标的豁免；
- 空结果三态：源可用 → 终态 empty；源故障 / 历史深度不足 → 可重试 failed；
- 两源交叉校验：OHLC 严格相等、volume 按「股」比对（腾讯「手」×100 后一致）。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.core.config import settings
from app.services.minute_backfill import (
    MEASURED_BYTES_PER_ROW,
    BackfillRefused,
    CrossCheckReport,
    MinuteBackfiller,
    _date_chunks,
    bars_per_day,
    compare_frames,
    estimate_bytes,
    trading_days_between,
)
from app.services.minute_collector import PROBE_DOWN, PROBE_OK
from app.services.minute_store import MinuteLibrary

START = date(2026, 6, 1)
END = date(2026, 6, 30)
PROBE_SYMBOL = str(getattr(settings, "SOURCE_HEALTH_PROBE_SYMBOL", "000001")).zfill(6)


def _range_bars(symbol: str, days: list[date], per_day: int = 3) -> pd.DataFrame:
    """构造跨多日的分钟 bar（列名用数据源的英文命名，验证规范化路径）。"""
    frames = []
    for day in days:
        stamps = pd.date_range(
            f"{day.isoformat()} 09:30:00", periods=per_day, freq="1min"
        )
        frames.append(
            pd.DataFrame(
                {
                    "datetime": stamps,
                    "open": [10.0] * per_day,
                    "high": [10.1] * per_day,
                    "low": [9.9] * per_day,
                    "close": [10.0 + i * 0.01 for i in range(per_day)],
                    "volume": [1000.0 * (i + 1) for i in range(per_day)],
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


class RangeProvider:
    """区间取数桩：按标的返回指定日期段的 bar，可注入空 / 异常。"""

    def __init__(self, days: list[date], empty=(), fail=(), per_day: int = 3):
        self.days = days
        self.empty = set(empty)
        self.fail = set(fail)
        self.per_day = per_day
        self.calls: list[dict] = []

    def fetch_minute_data(
        self,
        stock_code,
        start_datetime,
        end_datetime,
        period="1",
        adjust="",
        deadline=None,
        count=500,
        start_offset=0,
        **kwargs,
    ):
        self.calls.append(
            {
                "symbol": stock_code,
                "start": str(start_datetime)[:10],
                "end": str(end_datetime)[:10],
                "count": count,
                "start_offset": start_offset,
            }
        )
        if stock_code in self.fail:
            raise RuntimeError("fetch boom")
        if stock_code in self.empty:
            return None
        lo = date.fromisoformat(str(start_datetime)[:10])
        hi = date.fromisoformat(str(end_datetime)[:10])
        days = [d for d in self.days if lo <= d <= hi]
        if not days:
            return None
        return _range_bars(stock_code, days, self.per_day)


def _backfiller(tmp_path: Path, provider) -> MinuteBackfiller:
    return MinuteBackfiller(db=None, provider=provider, library=MinuteLibrary(tmp_path))


@pytest.fixture
def enabled(monkeypatch):
    """打开安全闸门的开关位（confirm 仍由各用例显式给）。"""
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_ENABLED", True)
    # 取数预算给足，避免用例被截断判定误伤（截断有专门的用例）
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_FETCH_BUDGET", 30.0)
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_CHUNK_DAYS", 30)
    return settings


# ---------------------------------------------------------------------------
# 估算口径
# ---------------------------------------------------------------------------


def test_estimate_uses_measured_bytes_per_row_not_p0_synthetic():
    """容量估算必须按实测 B/row —— P0 的 39 B/row 来自均匀随机合成数据，高约 3 倍。"""
    one_day = estimate_bytes("1", 5921, 1)
    assert one_day == int(5921 * 1 * 240 * MEASURED_BYTES_PER_ROW["1"])
    # 实测 13.8 B/row → 全市场单日 ≈ 19.6 MB，而不是 P0 口径的 56 MB
    assert 15e6 < one_day < 25e6
    assert estimate_bytes("1", 5921, 244) < 6e9  # < 6 GB/年，而不是 P0 的 13.7 GB


def test_bars_per_day_matches_session_length():
    assert bars_per_day("1") == 240
    assert bars_per_day("5") == 48
    assert bars_per_day("60") == 4


def test_trading_days_between_is_calendar_scaled():
    assert trading_days_between(date(2026, 6, 1), date(2026, 6, 7)) == 5
    assert trading_days_between(date(2026, 6, 7), date(2026, 6, 1)) == 0


def test_date_chunks_cover_window_without_gaps_or_overlap():
    chunks = _date_chunks(date(2026, 1, 1), date(2026, 1, 10), 4)
    assert chunks[0][0] == date(2026, 1, 1)
    assert chunks[-1][1] == date(2026, 1, 10)
    for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:]):
        assert next_start == prev_end + timedelta(days=1)
    assert all((b - a).days <= 3 for a, b in chunks)


def test_plan_does_not_touch_provider_or_disk(tmp_path, enabled):
    provider = RangeProvider([])
    filler = _backfiller(tmp_path, provider)
    plan = filler.plan("1", START, END, symbols=["000001", "600519"])
    assert provider.calls == []  # 计划阶段不取数
    assert plan.symbols == 2
    assert plan.est_rows == 2 * plan.trading_days * 240
    assert plan.bytes_per_row == MEASURED_BYTES_PER_ROW["1"]
    assert plan.free_bytes and plan.free_bytes > 0
    assert plan.fits is True
    assert not list(tmp_path.rglob("bars.parquet"))  # 不落盘


def test_plan_flags_when_estimate_exceeds_free_space(tmp_path, enabled, monkeypatch):
    provider = RangeProvider([])
    filler = _backfiller(tmp_path, provider)
    # 估算 ≈ 5,040 行 × 13.8 B ≈ 70 KB（×1.2 余量 ≈ 83 KB）→ 给 10 KB 必然装不下
    monkeypatch.setattr("app.services.minute_backfill._free_bytes", lambda _p: 10_000)
    plan = filler.plan("1", START, END, symbols=["000001"])
    assert plan.fits is False
    assert any("不得执行" in n for n in plan.notes)


# ---------------------------------------------------------------------------
# 安全闸门
# ---------------------------------------------------------------------------


def test_run_refused_when_switch_off(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_ENABLED", False)
    filler = _backfiller(tmp_path, RangeProvider([]))
    with pytest.raises(BackfillRefused, match="MINUTE_BACKFILL_ENABLED"):
        filler.run("1", START, END, symbols=["000001"], confirm=True)
    assert not list(tmp_path.rglob("bars.parquet"))


def test_run_refused_without_confirm(tmp_path, enabled):
    filler = _backfiller(tmp_path, RangeProvider([]))
    with pytest.raises(BackfillRefused, match="confirm"):
        filler.run("1", START, END, symbols=["000001"])
    assert not list(tmp_path.rglob("bars.parquet"))


# ---------------------------------------------------------------------------
# 取数 + 落盘
# ---------------------------------------------------------------------------


def test_run_writes_one_partition_per_day(tmp_path, enabled):
    days = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]
    provider = RangeProvider(days)
    filler = _backfiller(tmp_path, provider)
    result = filler.run("1", START, END, symbols=["000001"], confirm=True)

    assert result.fetched == 1 and result.failed == 0
    assert result.bars_written == 9
    assert result.partitions_written == 3
    lib = MinuteLibrary(tmp_path)
    assert lib.available_dates("1") == ["2026-06-01", "2026-06-02", "2026-06-03"]
    out = lib.read_bars("1", START, END)
    assert out["stock_code"].unique().tolist() == ["000001"]
    assert out["period"].unique().tolist() == ["1"]


def test_run_filters_bars_outside_window(tmp_path, enabled):
    """备源会返回跨区间窗口，区间外的 bar 不得写进分区。"""
    days = [date(2026, 5, 20), date(2026, 6, 2), date(2026, 7, 10)]
    provider = RangeProvider(days)
    filler = _backfiller(tmp_path, provider)
    result = filler.run("1", START, END, symbols=["000001"], confirm=True)
    lib = MinuteLibrary(tmp_path)
    assert lib.available_dates("1") == ["2026-06-02"]
    assert result.bars_written == 3


def test_run_is_idempotent_across_reruns(tmp_path, enabled):
    days = [date(2026, 6, 1), date(2026, 6, 2)]
    provider = RangeProvider(days)
    filler = _backfiller(tmp_path, provider)
    filler.run("1", START, END, symbols=["000001"], confirm=True)
    lib = MinuteLibrary(tmp_path)
    before = len(lib.read_bars("1", START, END))

    # force 重跑（忽略断点）：行数不变，新数据胜出
    result = filler.run("1", START, END, symbols=["000001"], confirm=True, resume=False)
    assert result.fetched == 1
    assert len(lib.read_bars("1", START, END)) == before


def test_periods_do_not_collide(tmp_path, enabled):
    days = [date(2026, 6, 1)]
    filler = _backfiller(tmp_path, RangeProvider(days))
    filler.run("1", START, END, symbols=["000001"], confirm=True)
    filler.run("5", START, END, symbols=["000001"], confirm=True)
    lib = MinuteLibrary(tmp_path)
    assert lib.available_dates("1") == ["2026-06-01"]
    assert lib.available_dates("5") == ["2026-06-01"]


def test_start_offset_skips_recent_bars_for_historical_window(tmp_path, enabled):
    """区间末尾在过去时必须跳过之后的所有 bar，否则源按「最新往旧」取会取错段。"""
    filler = _backfiller(tmp_path, RangeProvider([date(2026, 6, 1)]))
    assert filler._estimate_start_offset("1", date.today()) == 0
    offset = filler._estimate_start_offset("1", date.today() - timedelta(days=70))
    # 70 个日历日 ≈ 50 个交易日 × 240 根
    assert 10_000 < offset < 20_000


# ---------------------------------------------------------------------------
# 断点续跑
# ---------------------------------------------------------------------------


def test_resume_skips_completed_and_empty(tmp_path, enabled):
    days = [date(2026, 6, 1)]
    provider = RangeProvider(days, empty=["600519"])
    filler = _backfiller(tmp_path, provider)
    first = filler.run("1", START, END, symbols=["000001", "600519"], confirm=True)
    assert first.fetched == 1 and first.empty == 1

    calls_after_first = len(provider.calls)
    second = filler.run("1", START, END, symbols=["000001", "600519"], confirm=True)
    assert second.requested == 0
    assert second.skipped == 2
    assert len(provider.calls) == calls_after_first  # 没有再取数


def test_resume_retries_failed(tmp_path, enabled, monkeypatch):
    days = [date(2026, 6, 1)]
    monkeypatch.setattr(
        settings, "MINUTE_BACKFILL_FETCH_BUDGET", 0.0
    )  # 全部截断 → failed
    provider = RangeProvider(days)
    filler = _backfiller(tmp_path, provider)
    first = filler.run("1", START, END, symbols=["000001"], confirm=True)
    assert first.failed == 1 and first.fetched == 0

    monkeypatch.setattr(settings, "MINUTE_BACKFILL_FETCH_BUDGET", 30.0)
    second = filler.run("1", START, END, symbols=["000001"], confirm=True)
    assert second.requested == 1  # failed 会重试
    assert second.fetched == 1


def test_resume_false_ignores_journal(tmp_path, enabled):
    days = [date(2026, 6, 1)]
    provider = RangeProvider(days)
    filler = _backfiller(tmp_path, provider)
    filler.run("1", START, END, symbols=["000001"], confirm=True)
    again = filler.run("1", START, END, symbols=["000001"], confirm=True, resume=False)
    assert again.requested == 1 and again.skipped == 0


# ---------------------------------------------------------------------------
# 不静默截断
# ---------------------------------------------------------------------------


def test_budget_exhaustion_is_retryable_failure_not_completion(
    tmp_path, enabled, monkeypatch
):
    """预算耗尽 → 取回半段。数据仍落盘（幂等合并），但不得记完成。"""
    days = [date(2026, 6, 1), date(2026, 6, 2)]
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_FETCH_BUDGET", 0.0)
    provider = RangeProvider(days)
    filler = _backfiller(tmp_path, provider)
    result = filler.run("1", START, END, symbols=["000001"], confirm=True)

    assert result.fetched == 0 and result.failed == 1
    assert any("截断" in e for e in result.errors)
    # 数据没白取：已落盘，重跑会合并补齐
    assert MinuteLibrary(tmp_path).available_dates("1") == ["2026-06-01", "2026-06-02"]


def test_short_coverage_without_truncation_is_retryable(tmp_path, enabled):
    """源只给了区间尾部（如备源窗口短）→ 覆盖不足 → 可重试失败。"""
    provider = RangeProvider([date(2026, 6, 29), date(2026, 6, 30)])
    filler = _backfiller(tmp_path, provider)
    result = filler.run("1", START, END, symbols=["000001"], confirm=True)
    assert result.failed == 1 and result.fetched == 0
    assert any("覆盖不足" in e for e in result.errors)


def test_short_coverage_exempted_for_new_listing(tmp_path, enabled, monkeypatch):
    """上市日晚于区间起点 → 短覆盖合法，记完成（否则新上市标的永远重试）。"""
    provider = RangeProvider([date(2026, 6, 29), date(2026, 6, 30)])
    filler = _backfiller(tmp_path, provider)
    monkeypatch.setattr(
        filler, "_listing_dates", lambda symbols: {"000001": date(2026, 6, 28)}
    )
    result = filler.run("1", START, END, symbols=["000001"], confirm=True)
    assert result.fetched == 1 and result.failed == 0


def test_coverage_check_accepts_full_window(tmp_path, enabled):
    days = [date(2026, 6, 1), date(2026, 6, 15), date(2026, 6, 30)]
    filler = _backfiller(tmp_path, RangeProvider(days))
    result = filler.run("1", START, END, symbols=["000001"], confirm=True)
    assert result.fetched == 1 and result.failed == 0


# ---------------------------------------------------------------------------
# 空结果分类（源健康三态）
# ---------------------------------------------------------------------------


def test_empty_is_terminal_when_source_probe_ok(tmp_path, enabled):
    provider = RangeProvider([date(2026, 6, 1)], empty=["600519"])
    filler = _backfiller(tmp_path, provider)
    result = filler.run("1", START, END, symbols=["600519"], confirm=True)
    assert result.source_probe == PROBE_OK
    assert result.empty == 1 and result.failed == 0


def test_empty_is_retryable_when_source_down(tmp_path, enabled):
    """源故障时全区间取空不得记终态 —— 否则这段历史永久缺失。"""
    provider = RangeProvider([], empty=[PROBE_SYMBOL, "600519"])
    filler = _backfiller(tmp_path, provider)
    result = filler.run("1", START, END, symbols=["600519"], confirm=True)
    assert result.source_probe == PROBE_DOWN
    assert result.failed == 1 and result.empty == 0
    assert any("可重试" in e for e in result.errors)


def test_empty_ratio_guard_applies_at_market_scale(tmp_path, enabled, monkeypatch):
    """全市场口径下空结果过半 → 源只坏了一部分，按可重试失败处理，不记终态。"""
    pool = ["000001", "600519", "600000", "600001"]
    provider = RangeProvider([date(2026, 6, 1)], empty=pool[1:])  # 4 只里空 3 只
    filler = _backfiller(tmp_path, provider)
    monkeypatch.setattr(
        filler._collector, "resolve_universe", lambda day, **kw: (pool, "snapshot")
    )
    result = filler.run("1", START, END, confirm=True)

    assert result.universe_source == "snapshot"
    assert result.empty == 0
    assert result.failed == 3
    assert any("占比" in e for e in result.errors)


def test_empty_ratio_guard_skipped_for_explicit_symbol_list(tmp_path, enabled):
    """显式标的清单没有「市场占比」含义：单只自选股取空应记终态，而不是永远重试。"""
    provider = RangeProvider([date(2026, 6, 1)], empty=["600519"])
    filler = _backfiller(tmp_path, provider)
    result = filler.run("1", START, END, symbols=["600519"], confirm=True)
    assert result.universe_source == "explicit"
    assert result.empty == 1 and result.failed == 0


def test_fetch_exception_is_retryable(tmp_path, enabled):
    provider = RangeProvider([date(2026, 6, 1)], fail=["600519"])
    filler = _backfiller(tmp_path, provider)
    result = filler.run("1", START, END, symbols=["600519"], confirm=True)
    assert result.failed == 1
    assert any("fetch boom" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 两源交叉校验
# ---------------------------------------------------------------------------


def _bars_frame(volume_scale: float = 1.0) -> pd.DataFrame:
    stamps = pd.date_range("2026-06-01 09:30:00", periods=3, freq="1min")
    return pd.DataFrame(
        {
            "stock_code": ["000001"] * 3,
            "trade_time": stamps,
            "open": [10.0, 10.1, 10.2],
            "high": [10.5, 10.6, 10.7],
            "low": [9.5, 9.6, 9.7],
            "close": [10.1, 10.2, 10.3],
            "volume": [
                1000.0 * volume_scale,
                2000.0 * volume_scale,
                3000.0 * volume_scale,
            ],
        }
    )


def test_compare_frames_passes_on_identical_sources():
    report = CrossCheckReport(
        period="5", start_date="2026-06-01", end_date="2026-06-01"
    )
    compare_frames(_bars_frame(), _bars_frame(), report)
    assert report.ok
    assert report.compared_bars == 3
    assert report.ohlc_mismatch == 0 and report.volume_mismatch == 0


def test_compare_frames_detects_volume_unit_mismatch():
    """腾讯「手」未换算成「股」时，volume 会差 100 倍 —— 必须被检出。"""
    report = CrossCheckReport(
        period="5", start_date="2026-06-01", end_date="2026-06-01"
    )
    compare_frames(_bars_frame(), _bars_frame(volume_scale=0.01), report)
    assert report.volume_mismatch == 3
    assert report.ohlc_mismatch == 0
    assert not report.ok
    assert report.samples[0]["ohlc_equal"] is True


def test_compare_frames_detects_ohlc_mismatch():
    secondary = _bars_frame()
    secondary.loc[1, "close"] = 99.0
    report = CrossCheckReport(
        period="5", start_date="2026-06-01", end_date="2026-06-01"
    )
    compare_frames(_bars_frame(), secondary, report)
    assert report.ohlc_mismatch == 1
    assert not report.ok


def test_compare_frames_counts_one_sided_bars():
    secondary = _bars_frame().iloc[:2]
    report = CrossCheckReport(
        period="5", start_date="2026-06-01", end_date="2026-06-01"
    )
    compare_frames(_bars_frame(), secondary, report)
    assert report.only_primary == 1
    assert report.only_secondary == 0
    assert report.compared_bars == 2


def test_cross_check_reports_secondary_unavailable(tmp_path, enabled):
    """VEW-63（腾讯 ifzq）未合入时，交叉校验如实报告缺源，而不是假装通过。"""
    filler = _backfiller(tmp_path, RangeProvider([date(2026, 6, 1)]))
    report = filler.cross_check("5", START, END, symbols=["000001"])
    assert report.secondary_unavailable is True
    assert report.ok is False


# ---------------------------------------------------------------------------
# 观测字段
# ---------------------------------------------------------------------------


def test_result_dict_has_p0_shaped_fields(tmp_path, enabled):
    provider = RangeProvider([date(2026, 6, 1)])
    filler = _backfiller(tmp_path, provider)
    payload = filler.run("1", START, END, symbols=["000001"], confirm=True).as_dict()
    for key in (
        "period",
        "requested",
        "fetched",
        "empty",
        "failed",
        "skipped",
        "bars_written",
        "elapsed_sec",
        "universe_source",
        "source_probe",
    ):
        assert key in payload
    assert payload["universe_source"] == "explicit"


# ---------------------------------------------------------------------------
# VEW-65 评审①②：多分块截断 / 续跑占比分母（原 31 个用例恰好绕过这两条路径）
# ---------------------------------------------------------------------------

# 90 天窗口 = 恰好 3 个 30 天分块，用于构造「中间块出问题」的场景
MULTI_START = date(2026, 6, 1)
MULTI_END = date(2026, 8, 29)
MULTI_CHUNKS = _date_chunks(MULTI_START, MULTI_END, 30)


class ScriptedProvider:
    """按 ``(标的, 块区间)`` 决定返回哪些 bar —— 用于多分块 / 部分缺失场景。"""

    def __init__(self, script):
        self.script = script  # callable(symbol, lo, hi) -> list[date] | None
        self.calls: list[dict] = []

    def fetch_minute_data(
        self,
        stock_code,
        start_datetime,
        end_datetime,
        period="1",
        adjust="",
        deadline=None,
        count=500,
        start_offset=0,
        **kwargs,
    ):
        lo = date.fromisoformat(str(start_datetime)[:10])
        hi = date.fromisoformat(str(end_datetime)[:10])
        self.calls.append({"symbol": stock_code, "start": lo, "end": hi})
        days = self.script(stock_code, lo, hi)
        if not days:
            return None
        return _range_bars(stock_code, days, 3)


def test_window_actually_spans_multiple_chunks():
    """守住前提：默认分块下 90 天窗口确实是 3 块（否则下面两条用例形同虚设）。"""
    assert len(MULTI_CHUNKS) == 3
    assert MULTI_CHUNKS[0][0] == MULTI_START
    assert MULTI_CHUNKS[-1][1] == MULTI_END


def test_middle_chunk_hole_is_retryable_not_completion(tmp_path, enabled):
    """评审①回归：中间块开头缺一截 → 可重试失败。

    合并后的最早日期由第 1 块决定（= 区间起点），整体覆盖度校验照样通过；只有**逐块**
    判定才能发现这个洞。修复前该标的会被记 completed（终态），这段历史永久缺失。
    """
    middle_start, middle_end = MULTI_CHUNKS[1]

    def script(symbol, lo, hi):
        if lo == middle_start:
            # 中间块只返回最后两天：块首 29 天缺失
            return [middle_end - timedelta(days=1), middle_end]
        return [d for d in (lo, hi) if d <= hi]

    filler = _backfiller(tmp_path, ScriptedProvider(script))
    result = filler.run("1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True)

    assert result.fetched == 0, "中间块有洞却记了完成 —— 历史会永久缺失"
    assert result.failed == 1
    assert any("块内覆盖不足" in e for e in result.errors)
    # 取到的部分仍然落盘（幂等合并，重跑补齐）
    assert MinuteLibrary(tmp_path).available_dates("1")


def test_truncation_flag_survives_multi_chunk_window(tmp_path, enabled, monkeypatch):
    """评审①回归：多分块下中间块被截断必须仍被检出。

    截断标志若走 ``DataFrame.attrs``，``pd.concat`` 会把它丢掉（pandas 只在所有输入的
    attrs 完全相同时才保留），中间块被截断就再也检不出来。这里让**只有中间块**截断，
    断言它照样记 failed。
    """
    days = [d for cs, ce in MULTI_CHUNKS for d in (cs, ce)]
    filler = _backfiller(tmp_path, RangeProvider(days))
    real = filler._fetch_chunk
    middle_start = MULTI_CHUNKS[1][0]
    seen = {"middle": False}

    def fake(symbol, period, cs, ce):
        frame, took, truncated = real(symbol, period, cs, ce)
        if cs == middle_start:
            seen["middle"] = True
            return frame, took, True  # 只有中间块被预算截断
        return frame, took, truncated

    monkeypatch.setattr(filler, "_fetch_chunk", fake)
    frames, _, problems = filler._fetch_symbol_window("000001", "1", MULTI_CHUNKS)

    assert seen["middle"] and frames, "中间块没走到，用例前提不成立"
    assert any("截断" in p.reason for p in problems), "中间块的截断标志被 concat 丢掉了"
    # 截断是源侧条件，不能吃终态上限
    assert all(not p.countable for p in problems)


def test_run_records_failure_when_only_middle_chunk_truncated(
    tmp_path, enabled, monkeypatch
):
    """同上，但走完整的 ``run()`` —— 评审给的就是这条端到端路径。"""
    days = [d for cs, ce in MULTI_CHUNKS for d in (cs, ce)]
    filler = _backfiller(tmp_path, RangeProvider(days))
    real = filler._fetch_chunk
    middle_start = MULTI_CHUNKS[1][0]

    def fake(symbol, period, cs, ce):
        frame, took, truncated = real(symbol, period, cs, ce)
        return frame, took, (True if cs == middle_start else truncated)

    monkeypatch.setattr(filler, "_fetch_chunk", fake)
    result = filler.run("1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True)

    assert result.fetched == 0 and result.failed == 1
    assert any("截断" in e for e in result.errors)


def test_chunk_with_no_bars_at_all_is_not_flagged_as_hole():
    """整块取空**不**按块内洞处理：区间内长期停牌与源故障无法区分，交给整体判据。

    误杀会让停牌标的永远无法记终态（每次重跑都重取），方向与 P0「可疑才重试」相悖。
    """
    from app.services.minute_backfill import _chunk_problem

    assert _chunk_problem(None, date(2026, 7, 1), date(2026, 7, 30), False) is None
    empty = pd.DataFrame({"trade_time": pd.to_datetime([])})
    assert _chunk_problem(empty, date(2026, 7, 1), date(2026, 7, 30), False) is None


# ---------------------------------------------------------------------------
# 收敛：覆盖不足按标的计次，到上限转 gapped（评审二轮）
#
# 块首没有 bar 有两种成因，判据分不开：可重试的（截断 / 源深度抖动）与永久的
# （块首那段停牌，复牌后才有 bar）。只能靠次数区分 —— 没有上限时停牌标的永远停在
# failed，每轮重取整段区间、skipped 永远填不满，运维上还与真实源故障长得一样。
# ---------------------------------------------------------------------------


def _suspended_provider(resume_day: date) -> ScriptedProvider:
    """标的停牌到 ``resume_day``，之后每个工作日都有 bar。"""

    def script(symbol, lo, hi):
        if hi < resume_day:
            return []
        days, d = [], max(lo, resume_day)
        while d <= hi:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return days

    return ScriptedProvider(script)


def test_long_suspension_converges_instead_of_failing_forever(
    tmp_path, enabled, monkeypatch
):
    """评审二轮回归：块首因**停牌**缺失必须收敛，不能永远 failed。

    区间 06-01..08-29 分 3 块，标的停牌到 07-20 复牌 → 中间块 [07-01, 07-30] 块首缺
    19 天 > 12 天容差，且该块**不是空的**（有复牌后的 bar），``list_date`` 也豁免不了
    老标的。修复前连跑三轮 ``requested=1 skipped=0 failed=1`` 完全一致 —— 永远不收敛。
    """
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_MAX_ATTEMPTS", 1)
    resume = MULTI_CHUNKS[1][0] + timedelta(days=19)
    assert (resume - MULTI_CHUNKS[1][0]).days > 12, "前提：块首缺失超出容差"

    provider = _suspended_provider(resume)
    first = _backfiller(tmp_path, provider).run(
        "1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True
    )
    assert (first.fetched, first.gapped, first.failed) == (0, 1, 0)
    assert any("不再重试" in e for e in first.errors)
    # 缺口数据照样落盘（只是不再重取）
    assert MinuteLibrary(tmp_path).available_dates("1")

    second = _backfiller(tmp_path, provider).run(
        "1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True
    )
    assert second.skipped == 1, "第二轮没收敛 —— 停牌标的会被永远重取整段区间"
    assert second.requested == 0


def test_suspension_needs_max_attempts_before_terminal(tmp_path, enabled):
    """上限内的几轮仍是可重试 failed（给真故障留重试），到上限才转 gapped。

    这里用 ``settings`` 的**默认值**（3），确保默认配置本身就收敛。
    """
    resume = MULTI_CHUNKS[1][0] + timedelta(days=19)
    provider = _suspended_provider(resume)

    def run():
        return _backfiller(tmp_path, provider).run(
            "1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True
        )

    for round_no in (1, 2):
        r = run()
        assert (r.failed, r.gapped, r.skipped) == (1, 0, 0), f"第 {round_no} 轮"
    third = run()
    assert (third.failed, third.gapped) == (0, 1), "到上限应转终态，而不是继续 failed"
    assert run().skipped == 1, "转终态后重跑应跳过"


def test_source_failure_does_not_consume_gap_attempts(tmp_path, enabled, monkeypatch):
    """源级故障**不**计次 —— 否则连续几轮源故障会把全市场推进终态、永久丢历史。

    上限设成 1（最激进）：若源故障也计次，第一轮就会转 ``gapped``、第二轮 ``skipped=1``。
    正确行为是每轮都记可重试 ``failed``，源恢复后还能重取。
    """
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_MAX_ATTEMPTS", 1)
    provider = RangeProvider([])  # 全区间取空（含探针标的 000001）→ 源故障
    for round_no in range(1, 4):
        r = _backfiller(tmp_path, provider).run(
            "1", START, END, symbols=["000002"], confirm=True
        )
        assert r.gapped == 0, f"第 {round_no} 轮把源故障记成了终态"
        assert r.failed == 1
        assert r.skipped == 0, f"第 {round_no} 轮源故障被跳过 —— 源恢复后取不回来了"


def test_retry_gaps_puts_terminal_symbols_back(tmp_path, enabled, monkeypatch):
    """``retry_gaps=True`` 把 gapped 放回待采 —— 否则只能删日志文件才能重试。"""
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_MAX_ATTEMPTS", 1)
    resume = MULTI_CHUNKS[1][0] + timedelta(days=19)
    provider = _suspended_provider(resume)

    def run(**kwargs):
        return _backfiller(tmp_path, provider).run(
            "1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True, **kwargs
        )

    assert run().gapped == 1
    assert run().skipped == 1  # 默认是终态
    again = run(retry_gaps=True)
    assert again.requested == 1, "retry_gaps 没把标的放回待采"
    assert again.gapped == 1  # 重取仍缺 → 再次转终态


def test_gap_counter_is_per_symbol(tmp_path, enabled, monkeypatch):
    """计数按标的独立：健康标的照常完成，不被别的标的的缺口拖住。"""
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_MAX_ATTEMPTS", 1)
    resume = MULTI_CHUNKS[1][0] + timedelta(days=19)
    suspended = _suspended_provider(resume)
    healthy = ScriptedProvider(lambda symbol, lo, hi: [lo, hi] if lo <= hi else [])

    class Both:
        def fetch_minute_data(self, stock_code, *a, **kw):
            src = suspended if stock_code == "000001" else healthy
            return src.fetch_minute_data(stock_code, *a, **kw)

    r = _backfiller(tmp_path, Both()).run(
        "1", MULTI_START, MULTI_END, symbols=["000001", "000002"], confirm=True
    )
    assert r.fetched == 1, "健康标的应记完成"
    assert r.gapped == 1 and r.failed == 0


def test_chunk_problem_countable_only_for_chunk_coverage():
    """计次标志必须区分「该标的的事」与「源侧条件」（VEW-65 三轮评审）。

    截断是纯墙钟判据 —— 慢但在线的镜像每块数据完整也会命中，计次等于「源一慢全市场
    就收敛到有缺口完成」；块内覆盖不足才可能是该标的停牌。
    """
    from app.services.minute_backfill import _chunk_problem

    truncated = _chunk_problem(None, date(2026, 7, 1), date(2026, 7, 30), True)
    assert truncated is not None and truncated.countable is False

    # 块首缺一截（取到 bar 但最早晚于块起点 + 容差）→ 该标的自己的事
    late = pd.DataFrame(
        {"trade_time": pd.to_datetime(["2026-07-20 09:30", "2026-07-21 09:30"])}
    )
    hole = _chunk_problem(late, date(2026, 7, 1), date(2026, 7, 30), False)
    assert hole is not None and hole.countable is True


def test_slow_mirror_truncation_never_reaches_terminal(tmp_path, enabled, monkeypatch):
    """评审三轮路径 1/3：慢但在线的镜像（每块数据完整，只是撞墙钟预算）不能转终态。

    ``truncated = time.monotonic() >= deadline - 0.05`` 是纯墙钟判据，与数据完整性
    无关。修复前连跑 3 轮 → ``gapped=1``，之后换成健康源也是 ``skipped=1``、再也不
    重取 —— 源侧条件却付出了历史代价。
    """
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_MAX_ATTEMPTS", 1)
    days = [d for cs, ce in MULTI_CHUNKS for d in (cs, ce)]

    def make(tmp):
        f = _backfiller(tmp, RangeProvider(days))
        real = f._fetch_chunk

        def fake(symbol, period, cs, ce):
            frame, took, _ = real(symbol, period, cs, ce)
            return frame, took, True  # 数据完整，但报预算截断

        f._fetch_chunk = fake
        return f

    for round_no in range(1, 4):
        r = make(tmp_path).run(
            "1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True
        )
        assert r.gapped == 0, f"第 {round_no} 轮把源侧截断记成了终态"
        assert r.failed == 1 and r.skipped == 0, f"第 {round_no} 轮"
        assert any("截断" in e for e in r.errors)


def test_source_depth_shortfall_never_reaches_terminal(tmp_path, enabled, monkeypatch):
    """评审三轮路径 4：整体覆盖不足（源端深度）不能转终态。

    07-13 起才有 bar：每块块首都落在 +12 天容差内（逐块判据不触发），只有整体判据
    触发。修复前 3 轮 → ``gapped=1``。
    """
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_MAX_ATTEMPTS", 1)

    def script(symbol, lo, hi):
        d0 = date(2026, 7, 13)
        if hi < d0:
            return []
        d, out = max(lo, d0), []
        while d <= hi:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        return out

    provider = ScriptedProvider(script)
    for round_no in range(1, 4):
        r = _backfiller(tmp_path, provider).run(
            "1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True
        )
        assert r.gapped == 0, f"第 {round_no} 轮把源端深度不足记成了终态"
        assert r.failed == 1 and r.skipped == 0, f"第 {round_no} 轮"
        assert any("覆盖不足" in e for e in r.errors)


def test_market_scale_ratio_guard_suppresses_counting(tmp_path, enabled, monkeypatch):
    """市场级兜底：本轮报覆盖问题的标的过半时视作源侧事件，整轮不计次。

    单标的判据分不开「这个标的停牌」与「源整体变浅」—— 东财备源降级只给最近几天时，
    每个标的的最新一块都会块首缺失。占比是唯一能分开的信号（与 ``_classify_empty``
    同一机制、同一门槛）。
    """
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_MAX_ATTEMPTS", 1)
    middle_start, middle_end = MULTI_CHUNKS[1]

    def script(symbol, lo, hi):
        # 所有标的都在中间块块首缺一截 → 逐块判据对每个标的都触发
        if lo == middle_start:
            return [middle_end - timedelta(days=1), middle_end]
        return [d for d in (lo, hi) if d <= hi]

    symbols = [f"00000{i}" for i in range(1, 5)]
    filler = _backfiller(tmp_path, ScriptedProvider(script))
    # 全市场口径（symbols=None）才会启用占比兜底 —— 显式清单没有市场含义
    monkeypatch.setattr(
        filler._collector,
        "resolve_universe",
        lambda _end: (symbols, "security_universe"),
    )

    for round_no in range(1, 4):
        r = filler.run("1", MULTI_START, MULTI_END, confirm=True)
        assert r.gapped == 0, f"第 {round_no} 轮：全市场覆盖问题被记成了终态"
        assert r.failed == len(symbols) and r.skipped == 0
        assert any("疑似源侧事件" in e for e in r.errors)


def test_explicit_symbols_bypass_market_scale_ratio_guard(
    tmp_path, enabled, monkeypatch
):
    """显式清单不受占比兜底影响 —— 否则单标的验证永远无法记终态。"""
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_MAX_ATTEMPTS", 1)
    resume = MULTI_CHUNKS[1][0] + timedelta(days=19)
    provider = _suspended_provider(resume)
    r = _backfiller(tmp_path, provider).run(
        "1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True
    )
    assert r.gapped == 1, "显式清单下占比没有市场含义，不该被兜底压住"


def test_probe_fetch_exception_degrades_to_down(tmp_path, enabled):
    """探针取数抛异常不能穿出 ``run()`` —— 它跑在第一个标的之前，抛出去会让整个
    12–30 小时的回填在开始前就失败。按 ``PROBE_DOWN`` 降级（空结果保持可重试）。
    """

    class Boom:
        def fetch_minute_data(self, *a, **kw):
            raise RuntimeError("镜像连接被重置")

    filler = _backfiller(tmp_path, Boom())
    result = filler.run("1", START, END, symbols=["000001"], confirm=True)
    assert result.failed == 1 and result.gapped == 0
    assert result.skipped == 0


def test_journal_persists_attempts_and_gaps(tmp_path):
    """计数与终态要跨轮持久化，否则每轮都从零开始、上限形同虚设。"""
    from app.services.minute_backfill import _BackfillJournal

    first = _BackfillJournal(tmp_path, "1", START, END)
    assert first.record_gap_failure("000001", "原因A", 3) is False
    assert first.record_gap_failure("000002", "原因B", 1) is True
    first.flush()

    reloaded = _BackfillJournal(tmp_path, "1", START, END)
    assert reloaded.attempts == {"000001": 1}
    assert reloaded.gapped == {"000002": "原因B"}
    assert reloaded.done() == {"000002"}
    # 计数接着上一轮累：再失败两次即到上限
    assert reloaded.record_gap_failure("000001", "原因C", 3) is False
    assert reloaded.record_gap_failure("000001", "原因C", 3) is True


def test_journal_counter_semantics(tmp_path):
    """计数是「连续」语义；``mark`` 的源级桶不碰计数。"""
    from app.services.minute_backfill import _BackfillJournal

    journal = _BackfillJournal(tmp_path, "1", START, END)
    journal.mark("000002", "failed")  # 源级故障
    assert "000002" not in journal.attempts
    assert "000002" not in journal.done()
    # 成功一次即清零：中间成功过就不该算进上限
    journal.record_gap_failure("000001", "原因", 3)
    journal.mark("000001", "completed")
    assert "000001" not in journal.attempts
    assert "000001" not in journal.gapped and "000001" in journal.done()
    # 上限 1：一次即终态
    assert journal.record_gap_failure("000003", "原因", 1) is True


def test_coverage_reason_is_not_character_split(tmp_path, enabled, monkeypatch):
    """错误消息里的原因不能被按**单字**拆开（``_coverage_short`` 返回 str、
    ``problems`` 是 list，直接 ``'; '.join`` 会把字符串拆成字符）。

    这条 errors 是队长判断缺口成因的唯一入口，拆成单字等于没有信息。
    """
    monkeypatch.setattr(settings, "MINUTE_BACKFILL_MAX_ATTEMPTS", 1)
    # 07-13 复牌：中间块块首缺 12 天，恰好落在逐块容差内（不触发逐块判据），
    # 只有「区间起点够不着」的整体判据触发 —— 正好是返回 str 的那条路径。
    provider = _suspended_provider(date(2026, 7, 13))
    r = _backfiller(tmp_path, provider).run(
        "1", MULTI_START, MULTI_END, symbols=["000001"], confirm=True
    )
    assert r.failed + r.gapped == 1
    assert "疑似截断" in r.errors[0], f"原因被拆成了单字: {r.errors[0]!r}"


def test_empty_ratio_denominator_uses_attempted_on_resume(
    tmp_path, enabled, monkeypatch
):
    """评审②回归：续跑时空结果占比的分母必须是**本轮尝试数**。

    修复前分母用 ``universe_size``（扣掉 skipped 之前的全量），续跑批次小的时候占比被
    系统性低估，「本轮待采的标的全部取空」这种最该兜底的情形反而漏过 → 记终态 empty，
    这批标的历史永久缺失且重跑不会再来。
    """
    pool = [f"60000{i}" for i in range(10)] + [f"00000{i}" for i in range(90)]
    # run1：10 只抛异常 → failed（其余 90 只正常取到）
    first_provider = RangeProvider([date(2026, 6, 1)], fail=pool[:10])
    first_filler = _backfiller(tmp_path, first_provider)
    monkeypatch.setattr(
        first_filler._collector,
        "resolve_universe",
        lambda day, **kw: (pool, "snapshot"),
    )
    first = first_filler.run("1", START, END, confirm=True)
    assert first.failed == 10 and first.fetched == 90

    # run2：源部分故障，这 10 只全取空；探针标的（000001）正常
    second_provider = RangeProvider([date(2026, 6, 1)], empty=pool[:10])
    second_filler = _backfiller(tmp_path, second_provider)
    monkeypatch.setattr(
        second_filler._collector,
        "resolve_universe",
        lambda day, **kw: (pool, "snapshot"),
    )
    second = second_filler.run("1", START, END, confirm=True)

    assert second.requested == 10 and second.skipped == 90
    assert second.empty == 0, "本轮待采 100% 取空却记了终态 —— 历史永久缺失"
    assert second.failed == 10
    assert any("占比" in e for e in second.errors)


# ---------------------------------------------------------------------------
# VEW-65 评审③④：交叉校验的假通过与覆盖率
# ---------------------------------------------------------------------------


def test_cross_check_ok_is_false_when_nothing_compared():
    """评审③回归：两边交集为空时 mismatch 天然是 0，不能据此报「通过」。"""
    report = CrossCheckReport(period="1", start_date="", end_date="")
    assert report.compared_bars == 0
    assert report.ok is False, "一根 bar 都没比却报通过（假通过）"


def test_cross_check_ok_requires_coverage():
    """评审③④回归：只比上窗口尾巴不算通过。"""
    report = CrossCheckReport(period="1", start_date="", end_date="")
    report.compared_bars = 800  # 第二源单页上限
    report.only_primary = 4200  # 主源 4 个月窗口的其余部分
    assert report.coverage < 0.2
    assert report.ok is False


def test_cross_check_ok_true_only_on_full_agreement():
    report = CrossCheckReport(period="1", start_date="", end_date="")
    report.compared_bars = 100
    assert report.coverage == 1.0
    assert report.ok is True
    # 有任一 mismatch 即不通过
    report.volume_mismatch = 1
    assert report.ok is False


def test_cross_check_reports_secondary_empty_separately(tmp_path, enabled, monkeypatch):
    """第二源「在但该窗口没数据」与「模块缺失」是两回事，必须分开报告。"""
    filler = _backfiller(tmp_path, RangeProvider([date(2026, 6, 1)]))
    monkeypatch.setattr(filler, "_fetch_secondary", lambda *a, **kw: pd.DataFrame())
    report = filler.cross_check("1", START, END, ["000001"])

    assert report.secondary_empty == 1
    assert report.secondary_unavailable is False
    assert report.compared_bars == 0 and report.ok is False


def _install_fake_tencent(monkeypatch, pages_by_cursor: dict):
    """把假的 ``app.providers.astock_data`` 塞进 sys.modules，模拟 VEW-63 取数接口。"""
    import sys
    import types

    def tencent_minute_bars(symbol, period, start_time="", count=800, _record=True):
        return pages_by_cursor.get(start_time, [])

    def tencent_minute_frame(bars):
        if not bars:
            return None
        return pd.DataFrame(bars)

    module = types.ModuleType("app.providers.astock_data")
    module.tencent_minute_bars = tencent_minute_bars
    module.tencent_minute_frame = tencent_minute_frame
    monkeypatch.setitem(sys.modules, "app.providers.astock_data", module)


def _tencent_bars(day: date, hours=(9, 10)) -> list[dict]:
    return [
        {
            "datetime": pd.Timestamp(f"{day.isoformat()} {h:02d}:30:00"),
            "open": 10.0,
            "close": 10.1,
            "high": 10.2,
            "low": 9.9,
            "volume": 1000.0,
        }
        for h in hours
    ]


def test_secondary_pages_back_until_window_start(tmp_path, enabled, monkeypatch):
    """评审④回归：第二源要按区间翻页，不能只取最新一页（1min 单页仅 ~3.3 个交易日）。"""
    pages = {
        "": _tencent_bars(date(2026, 6, 20)),  # 第 1 页：最新
        "2026-06-20 09:30:00": _tencent_bars(date(2026, 6, 10)),
        "2026-06-10 09:30:00": _tencent_bars(date(2026, 6, 1)),  # 够到区间起点 → 停
    }
    _install_fake_tencent(monkeypatch, pages)
    filler = _backfiller(tmp_path, RangeProvider([date(2026, 6, 1)]))

    frame = filler._fetch_secondary("000001", "1", START, END)

    assert frame is not None and not frame.empty
    assert set(pd.to_datetime(frame["datetime"]).dt.date) == {
        date(2026, 6, 1),
        date(2026, 6, 10),
        date(2026, 6, 20),
    }


def test_secondary_paging_stops_when_cursor_does_not_advance(
    tmp_path, enabled, monkeypatch
):
    """对未知契约的防御：源忽略 ``start_time`` 时不能原地打转。"""
    calls = {"n": 0}
    fixed = _tencent_bars(date(2026, 6, 20))

    def bars(symbol, period, start_time="", count=800, _record=True):
        calls["n"] += 1
        return fixed  # 无论游标是什么都返回同一页

    import sys
    import types

    module = types.ModuleType("app.providers.astock_data")
    module.tencent_minute_bars = bars
    module.tencent_minute_frame = lambda b: pd.DataFrame(b) if b else None
    monkeypatch.setitem(sys.modules, "app.providers.astock_data", module)

    filler = _backfiller(tmp_path, RangeProvider([date(2026, 6, 1)]))
    frame = filler._fetch_secondary("000001", "1", START, END)

    assert calls["n"] == 2, "游标没推进却没有及时停手"
    assert frame is not None


# ---------------------------------------------------------------------------
# VEW-65 评审⑤⑥：执行侧盘余量硬校验 + 门槛含一年增量
# ---------------------------------------------------------------------------


def test_run_refuses_when_disk_headroom_insufficient(tmp_path, enabled, monkeypatch):
    """评审⑤回归：闸门挡住「误跑」，这条挡「明知故跑」。"""
    import app.services.minute_backfill as mb

    monkeypatch.setattr(mb, "_free_bytes", lambda path: 1_000)
    filler = _backfiller(tmp_path, RangeProvider([date(2026, 6, 1)]))
    with pytest.raises(BackfillRefused, match="盘余量不足"):
        filler.run("1", START, END, symbols=["000001"], confirm=True)


def test_run_force_bypasses_disk_headroom(tmp_path, enabled, monkeypatch):
    """显式 force=True 才放行 —— 责任人要在日志里留下「我知道放不下」的痕迹。"""
    import app.services.minute_backfill as mb

    monkeypatch.setattr(mb, "_free_bytes", lambda path: 1_000)
    filler = _backfiller(tmp_path, RangeProvider([date(2026, 6, 1)]))
    result = filler.run("1", START, END, symbols=["000001"], confirm=True, force=True)
    assert result.fetched == 1


def test_disk_check_skipped_when_free_space_unmeasurable(
    tmp_path, enabled, monkeypatch
):
    """量不出盘余量时**不拦**：拿不到事实就不假装有事实，由前两层闸门兜底。"""
    import app.services.minute_backfill as mb

    monkeypatch.setattr(mb, "_free_bytes", lambda path: None)
    filler = _backfiller(tmp_path, RangeProvider([date(2026, 6, 1)]))
    result = filler.run("1", START, END, symbols=["000001"], confirm=True)
    assert result.fetched == 1


def test_plan_fits_includes_annual_growth(tmp_path, enabled, monkeypatch):
    """评审⑥回归：闸门 = 一次性回填 + 至少一年增量，只看一次性会低估门槛。"""
    import app.services.minute_backfill as mb

    filler = _backfiller(tmp_path, RangeProvider([date(2026, 6, 1)]))
    plan = filler.plan("1", START, END, symbols=["000001"])

    assert plan.annual_bytes > plan.est_bytes, "一年增量应远大于 1 个月的一次性回填"
    assert plan.required_bytes == int((plan.est_bytes + plan.annual_bytes) * 1.2)

    # 恰好够一次性回填、不够「一次性 + 一年增量」→ 必须判放不下
    monkeypatch.setattr(mb, "_free_bytes", lambda path: int(plan.est_bytes * 1.2) + 1)
    tight = filler.plan("1", START, END, symbols=["000001"])
    assert tight.fits is False
