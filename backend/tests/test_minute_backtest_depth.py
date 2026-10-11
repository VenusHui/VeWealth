"""VEW-66 分钟级回测：源深度登记与超深度裁决。

纯网络方案下各源窗口滚动且互不相同（VEW-61 / VEW-63 实测）。这里锁定三件事：

- 有效深度取各源最大值，且 1min 明显短于 5/15/30/60min；
- 请求超出深度时，``strict`` 报错、``truncate`` 截断到可用下界并留下说明 —— 两种
  策略都**不允许**静默返回不完整区间；
- 整个区间都落在深度之外时必须报错（截断只会得到空区间，那是最难发现的静默失败）。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.backtest.minute.depth import (
    SUPPORTED_PERIODS,
    MinuteDepthExceededError,
    depth_report,
    effective_depth_days,
    normalize_period,
    resolve_request_window,
    source_depth_days,
)

AS_OF = "2026-02-10"


def test_source_depth_covers_both_sources_and_periods():
    assert source_depth_days("1") == {"mootdx": 120, "tencent": 26}
    assert source_depth_days("60") == {"mootdx": 730, "tencent": 365}


def test_effective_depth_is_widest_source_and_shorter_for_1min():
    # 1min 有效深度 = mootdx 的 4 个月；5/15/30/60min = mootdx 的 2 年
    assert effective_depth_days("1") == 120
    for period in ("5", "15", "30", "60"):
        assert effective_depth_days(period) == 730
    assert effective_depth_days("1") < effective_depth_days("5")


def test_window_inside_depth_is_not_truncated():
    decision = resolve_request_window(
        "1", "2026-01-01", "2026-02-01", policy="truncate", as_of=AS_OF
    )
    assert decision.exceeded is False
    assert decision.truncated is False
    assert decision.note is None
    assert decision.effective_start == date(2026, 1, 1)


def test_truncate_policy_clamps_to_floor_and_notes_it():
    # as_of 2026-02-10，1min 有效深度 120 天 → 下界 2025-10-13
    decision = resolve_request_window(
        "1", "2025-01-01", "2026-02-01", policy="truncate", as_of=AS_OF
    )
    assert decision.exceeded is True
    assert decision.truncated is True
    assert decision.depth_floor == date(2025, 10, 13)
    assert decision.effective_start == date(2025, 10, 13)
    # 原始请求区间必须原样保留，否则事后无法判断被截掉了多少
    assert decision.requested_start == date(2025, 1, 1)
    assert decision.note and "截断" in decision.note


def test_strict_policy_raises_instead_of_returning_partial_range():
    with pytest.raises(MinuteDepthExceededError) as excinfo:
        resolve_request_window(
            "1", "2025-01-01", "2026-02-01", policy="strict", as_of=AS_OF
        )
    assert "深度" in str(excinfo.value)


def test_window_fully_outside_depth_raises_even_under_truncate():
    with pytest.raises(MinuteDepthExceededError):
        resolve_request_window(
            "1", "2020-01-01", "2020-02-01", policy="truncate", as_of=AS_OF
        )


def test_unsupported_period_and_policy_raise():
    with pytest.raises(ValueError):
        normalize_period("7")
    with pytest.raises(ValueError):
        resolve_request_window("1", "2026-01-01", "2026-02-01", policy="loose")


def test_start_after_end_raises():
    with pytest.raises(ValueError):
        resolve_request_window("1", "2026-02-01", "2026-01-01", as_of=AS_OF)


def test_depth_report_exposes_every_supported_period():
    report = depth_report(AS_OF)
    assert report["as_of"] == AS_OF
    assert set(report["periods"]) == set(SUPPORTED_PERIODS)
    assert report["periods"]["1"]["effective_depth_days"] == 120
    assert report["periods"]["1"]["floor"] == "2025-10-13"
    # 深度只是前置裁决，不能当作可复现承诺 —— 报告里必须写明
    assert "滚动" in report["note"]
