"""测试数据源健康度监控器核心逻辑（仅依赖标准库）。"""

from __future__ import annotations

import unittest

from app.core.source_health import (
    EVENT_FAILURE,
    EVENT_FALLBACK,
    EVENT_RECOVERY,
    EVENT_SKIPPED,
    OVERALL_DEGRADED,
    OVERALL_OK,
    OVERALL_UNHEALTHY,
    STATUS_DOWN,
    STATUS_SKIPPED,
    STATUS_UNKNOWN,
    STATUS_UP,
    SourceHealthMonitor,
)


class SourceHealthMonitorTests(unittest.TestCase):
    def setUp(self):
        self.monitor = SourceHealthMonitor(event_limit=10, fail_threshold=3)

    def test_initial_unknown(self):
        self.assertEqual(self.monitor.overall_status(), STATUS_UNKNOWN)
        self.assertEqual(self.monitor.snapshot()["sources"], {})

    def test_success_marks_up(self):
        self.monitor.record_attempt("eastmoney", ok=True, duration_ms=12.0)
        state = self.monitor.snapshot()["sources"]["eastmoney"]
        self.assertEqual(state["status"], STATUS_UP)
        self.assertEqual(state["total_success"], 1)
        self.assertEqual(state["total_requests"], 1)
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertAlmostEqual(state["avg_latency_ms"], 12.0)

    def test_failure_marks_down_and_emits_event(self):
        self.monitor.record_attempt(
            "eastmoney", ok=False, duration_ms=50.0, error="timeout"
        )
        state = self.monitor.snapshot()["sources"]["eastmoney"]
        self.assertEqual(state["status"], STATUS_DOWN)
        self.assertEqual(state["consecutive_failures"], 1)
        self.assertEqual(state["last_error"], "timeout")
        events = self.monitor.events_recent()
        self.assertEqual(events[0]["event_type"], EVENT_FAILURE)
        self.assertEqual(events[0]["source"], "eastmoney")
        self.assertEqual(events[0]["detail"], "timeout")

    def test_recovery_after_failure(self):
        self.monitor.record_attempt("eastmoney", ok=False, duration_ms=50.0)
        self.monitor.record_attempt("eastmoney", ok=True, duration_ms=10.0)
        state = self.monitor.snapshot()["sources"]["eastmoney"]
        self.assertEqual(state["status"], STATUS_UP)
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertEqual(state["total_success"], 1)
        self.assertEqual(state["total_failure"], 1)
        events = self.monitor.events_recent()
        self.assertEqual(events[0]["event_type"], EVENT_RECOVERY)

    def test_fail_threshold_escalates_level(self):
        for _ in range(3):
            self.monitor.record_attempt("mootdx", ok=False, duration_ms=1.0)
        events = self.monitor.events_recent()
        self.assertEqual(events[0]["level"], "ERROR")

    def test_below_threshold_stays_warning(self):
        self.monitor.record_attempt("mootdx", ok=False, duration_ms=1.0)
        events = self.monitor.events_recent()
        self.assertEqual(events[0]["level"], "WARNING")

    def test_fallback_event(self):
        self.monitor.record_fallback(
            "eastmoney", "tushare", reason="kline empty"
        )
        events = self.monitor.events_recent()
        self.assertEqual(events[0]["event_type"], EVENT_FALLBACK)
        self.assertIn("tushare", events[0]["message"])
        self.assertEqual(events[0]["level"], "WARNING")

    def test_skipped_not_failure(self):
        self.monitor.record_skipped("tushare", detail="token missing")
        state = self.monitor.snapshot()["sources"]["tushare"]
        self.assertEqual(state["status"], STATUS_SKIPPED)
        # 全部 skipped 时总体状态仍为 unknown（尚无参与检查的数据源）
        self.assertEqual(self.monitor.overall_status(), STATUS_UNKNOWN)
        events = self.monitor.events_recent()
        self.assertEqual(events[0]["event_type"], EVENT_SKIPPED)

    def test_overall_status_transitions(self):
        self.monitor.record_attempt("eastmoney", ok=True)
        self.monitor.record_attempt("tencent", ok=True)
        self.assertEqual(self.monitor.overall_status(), OVERALL_OK)

        self.monitor.record_attempt("mootdx", ok=False)
        self.assertEqual(self.monitor.overall_status(), OVERALL_DEGRADED)

        self.monitor.record_attempt("eastmoney", ok=False)
        self.monitor.record_attempt("tencent", ok=False)
        self.assertEqual(self.monitor.overall_status(), OVERALL_UNHEALTHY)

    def test_skipped_source_ignored_in_overall(self):
        self.monitor.record_skipped("tushare", detail="not configured")
        self.monitor.record_attempt("eastmoney", ok=True)
        self.assertEqual(self.monitor.overall_status(), OVERALL_OK)

    def test_event_ring_buffer_bounded(self):
        for _ in range(20):
            self.monitor.record_attempt("eastmoney", ok=False, duration_ms=1.0)
        self.assertEqual(len(self.monitor.events_recent()), 10)

    def test_success_rate(self):
        self.monitor.record_attempt("eastmoney", ok=True)
        self.monitor.record_attempt("eastmoney", ok=False)
        state = self.monitor.snapshot()["sources"]["eastmoney"]
        self.assertEqual(state["success_rate"], 0.5)

    def test_metrics_shape(self):
        self.monitor.record_attempt("eastmoney", ok=True, duration_ms=5.0)
        metrics = self.monitor.metrics()
        self.assertIn("sources", metrics)
        self.assertIn("eastmoney", metrics["sources"])
        self.assertEqual(metrics["sources"]["eastmoney"]["total_requests"], 1)
        self.assertEqual(metrics["overall_status"], OVERALL_OK)

    def test_register_source_idempotent(self):
        self.monitor.register_source("mootdx")
        self.monitor.register_source("mootdx")
        self.assertEqual(len(self.monitor.snapshot()["sources"]), 1)

    def test_configure_resizes_event_buffer(self):
        self.monitor.record_attempt("eastmoney", ok=False, duration_ms=1.0)
        self.monitor.configure(event_limit=50, sources=["mootdx", "tushare"])
        self.assertEqual(len(self.monitor.events_recent()), 1)
        sources = self.monitor.snapshot()["sources"]
        self.assertIn("mootdx", sources)
        self.assertIn("tushare", sources)


if __name__ == "__main__":
    unittest.main()
