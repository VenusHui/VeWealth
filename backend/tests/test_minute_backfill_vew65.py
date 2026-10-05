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
