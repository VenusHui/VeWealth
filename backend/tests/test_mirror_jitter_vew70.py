"""镜像校验区分「瞬时抖动」与「结构性不可用」（VEW-70）。

VEW-69 实测：唯一可用镜像 115.238.90.165 的**日线端点**在流动性标的上间歇性返回空
（连打 3 次约一半失败），而同一镜像的 5min 端点完全正常。原来的校验对
``_MOOTDX_PROBE_FREQUENCIES`` 做严格 AND 且**一次空返回即否决整个镜像**，于是：

- 分钟链路（5min 是深度图默认周期）被一个抖动的日线端点拖下水；
- 判死会走 ``_invalidate_mootdx_client`` 摘除缓存 client，之后**每个**请求都要重走
  镜像扫描，而扫描有预算、常常扫不完，直接落到备源。

本文件固定新的裁决口径：

- 单周期失败后**重连**重试，最多 ``_MOOTDX_PROBE_ATTEMPTS`` 次 —— 重连是有效单位：
  生产实测丢包是**连接级**的，被丢的连接永不恢复，同连接重复调用零收益；
- 用尽尝试后按失败性质裁决：全是快速空返回（连接通、服务器明确回「没有数据」）才判
  结构性不可用；出现过超时则只判「未证实」，有其它周期确认可取数就降级采用
  （``MIRROR_DEGRADED``），一条正面证据都没有时才判 ``MIRROR_UNKNOWN``；
- 取数路径的空返回确认同样走有界 quorum，单次抖动不再摘除缓存 client、不再触发重扫；
- 分周期健康档案记录每个 ``(镜像, 周期)`` 的成功率与最近成功时间。
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

import pandas as pd

from app.core.source_health import source_monitor
from app.providers import astock_provider as ap
from app.providers.mirror_health import MirrorHealthRegistry, mirror_health
from app.providers.probes import probe_mootdx

FREQ_5MIN = 0
FREQ_DAILY = 4

# 「慢失败」的测试阈值（秒）。真值是 1.0s（要盖住一次 socket 超时），测试里压到 50ms
# 以免每个慢失败用例真睡 1 秒；「超时必须算慢失败」这条不变量由
# MirrorVerdictTests.test_slow_failure_threshold_sits_below_the_probe_socket_timeout 钉住。
TEST_SLOW_SECONDS = 0.05
TEST_SLOW_DELAY = 0.1


def _df(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [1.0] * rows,
            "close": [1.0] * rows,
            "high": [1.0] * rows,
            "low": [1.0] * rows,
            "volume": [100.0] * rows,
            "amount": [100.0] * rows,
        }
    )


def _empty() -> pd.DataFrame:
    return pd.DataFrame()


class FakeTdxApi:
    """最小 tdxpy API 替身：让 ``_reconnect_probe_client`` 能工作并记录重连次数。

    重连是探测重试的**有效单位**（生产实测：丢包是连接级的，被丢的连接永不恢复），
    所以假 client 必须有可重连的 ``client``，否则测出来的是「同连接重试」——那正是
    生产上无效的那种。
    """

    def __init__(self, ip: str = "1.1.1.1", port: int = 7709):
        self.ip = ip
        self.port = port
        self.reconnects = 0

    def disconnect(self):
        return None

    def connect(self, ip=None, port=7709, time_out=None, **kwargs):
        self.reconnects += 1
        return True


class PeriodScriptedClient:
    """按 ``frequency`` 消费预设响应的假 client，可注入单次调用耗时。

    ``scripts`` 形如 ``{0: [_df(), _empty()], 4: [...]}``；队列耗尽后恒返回空，
    与真实镜像「该周期没有数据」的表现一致。
    """

    def __init__(self, scripts, delay: float = 0.0, ip: str = "1.1.1.1"):
        self._scripts = {freq: list(items) for freq, items in scripts.items()}
        self._delay = delay
        self.calls: list[dict] = []
        self.client = FakeTdxApi(ip=ip)

    def bars(self, *args, **kwargs):
        freq = kwargs.get("frequency")
        self.calls.append(dict(kwargs))
        if self._delay:
            time.sleep(self._delay)
        queue = self._scripts.get(freq)
        if not queue:
            return _empty()
        item = queue.pop(0)
        return item() if callable(item) else item

    def frequency_calls(self, freq: int) -> int:
        return sum(1 for call in self.calls if call.get("frequency") == freq)


class FakeQuotes:
    """模拟 mootdx Quotes：factory 返回预设 client。"""

    def __init__(self, client):
        self._client = client
        self.factory = mock.Mock(return_value=client)


class MirrorJitterBase(unittest.TestCase):
    def setUp(self):
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None
        ap._mootdx_scan_in_progress = False
        mirror_health.reset()
        source_monitor.reset()
        # 退避只为「让连接状态稳定」，与裁决无关；测试里置 0 以免每个用例多等 0.4s。
        backoff = mock.patch.object(ap, "_MOOTDX_PROBE_ATTEMPT_BACKOFF", 0.0)
        backoff.start()
        self.addCleanup(backoff.stop)
        slow = mock.patch.object(
            ap, "_MOOTDX_PROBE_RETRY_MAX_SECONDS", TEST_SLOW_SECONDS
        )
        slow.start()
        self.addCleanup(slow.stop)

    def tearDown(self):
        ap._mootdx_client = None
        ap._mootdx_init_failed_at = None
        ap._mootdx_scan_in_progress = False
        mirror_health.reset()
        source_monitor.reset()


class MirrorVerdictTests(MirrorJitterBase):
    """_mirror_serves_bars / _try_mootdx_server 的裁决口径。"""

    def test_jittery_secondary_period_is_retried_and_mirror_accepted(self):
        """日线端点前两次空、第三次返回数据 → 重试后判定可用（VEW-69 实测形态）。"""
        client = PeriodScriptedClient(
            {FREQ_5MIN: [_df()], FREQ_DAILY: [_empty(), _empty(), _df()]}
        )
        verdict = ap._mirror_serves_bars(client, ("115.238.90.165", 7709))

        self.assertEqual(verdict, ap.MIRROR_SERVES)
        # 主用周期先测（首次请求即返回），日线重试到第三次才成功
        self.assertEqual(client.frequency_calls(FREQ_5MIN), 1)
        self.assertEqual(client.frequency_calls(FREQ_DAILY), 3)
        # 降级/否决都不该发生：镜像照常被采用
        self.assertIs(
            ap._try_mootdx_server(
                FakeQuotes(
                    PeriodScriptedClient(
                        {FREQ_5MIN: [_df()], FREQ_DAILY: [_empty(), _df()]}
                    )
                ),
                ("115.238.90.165", 7709),
            ).__class__,
            PeriodScriptedClient,
        )

    def test_structural_empty_period_still_rejects_mirror(self):
        """日线端点**连续**快速空返回 → 结构性不可用，镜像仍被拒绝（VEW-36 意图不变）。"""
        client = PeriodScriptedClient({FREQ_5MIN: [_df()] * 3, FREQ_DAILY: []})
        self.assertEqual(
            ap._mirror_serves_bars(client, ("1.1.1.1", 7709)),
            ap.MIRROR_UNAVAILABLE,
        )
        self.assertEqual(client.frequency_calls(FREQ_DAILY), ap._MOOTDX_PROBE_ATTEMPTS)

    def test_slow_failure_after_primary_confirmed_is_degraded_not_rejected(self):
        """日线慢失败（超时）时，5min 已确认可取数 → 降级采用，不再整源判死。"""
        client = PeriodScriptedClient(
            {FREQ_5MIN: [_df()], FREQ_DAILY: []}, delay=TEST_SLOW_DELAY
        )
        verdict = ap._mirror_serves_bars(client, ("115.238.90.165", 7709))

        self.assertEqual(verdict, ap.MIRROR_DEGRADED)
        # 慢失败同样按 _MOOTDX_PROBE_ATTEMPTS 重连重试，用尽后才判「未证实」
        self.assertEqual(client.frequency_calls(FREQ_DAILY), ap._MOOTDX_PROBE_ATTEMPTS)
        self.assertIs(
            ap._try_mootdx_server(
                FakeQuotes(
                    PeriodScriptedClient(
                        {FREQ_5MIN: [_df()], FREQ_DAILY: []},
                        delay=TEST_SLOW_DELAY,
                    )
                ),
                ("115.238.90.165", 7709),
            ).__class__,
            PeriodScriptedClient,
            "降级结论必须被采用，否则等于没修",
        )

    def test_slow_failure_without_any_positive_evidence_is_not_adopted(self):
        """主用周期就慢失败、毫无正面证据 → 本轮不采用（宁可不用，也不当成结论）。"""
        client = PeriodScriptedClient(
            {FREQ_5MIN: [], FREQ_DAILY: [_df()]},
            delay=TEST_SLOW_DELAY,
        )
        self.assertEqual(
            ap._mirror_serves_bars(client, ("1.1.1.1", 7709)), ap.MIRROR_UNKNOWN
        )
        # 主用周期没结论就不再花预算测其它周期
        self.assertEqual(client.frequency_calls(FREQ_DAILY), 0)

    def test_exhausted_budget_before_any_attempt_is_unknown(self):
        """预算已耗尽 → 一次都没测成，不能当成「测到没有」。"""
        client = PeriodScriptedClient({FREQ_5MIN: [_df()], FREQ_DAILY: [_df()]})
        self.assertEqual(
            ap._mirror_serves_bars(
                client, ("1.1.1.1", 7709), deadline=time.monotonic() - 1
            ),
            ap.MIRROR_UNKNOWN,
        )
        self.assertEqual(client.calls, [])

    def test_blackhole_mirror_costs_exactly_the_attempt_budget(self):
        """黑洞镜像（TCP 通、从不回包）每个周期只花 ``_MOOTDX_PROBE_ATTEMPTS`` 次尝试。

        每次尝试 = 重连 + 取数，单次成本 _MOOTDX_PROBE_TIMEOUT，总成本由
        ProbeTuningInvariantTests 换算成秒钉住 —— 这是「探测不能吃光扫描预算」的
        那道闸门（VEW-62）。
        """
        client = PeriodScriptedClient({}, delay=TEST_SLOW_DELAY)
        self.assertEqual(
            ap._mirror_serves_bars(client, ("1.1.1.1", 7709)), ap.MIRROR_UNKNOWN
        )
        self.assertEqual(len(client.calls), ap._MOOTDX_PROBE_ATTEMPTS)

    def test_retries_reconnect_rather_than_repeating_on_the_same_socket(self):
        """重试必须**重连**：同连接重复调用在生产上零收益（VEW-70 实测 4/4 仍失败）。

        丢包是连接级的 —— 被丢的那条连接永不恢复，只有重新建 TCP 才能重新抽签。
        所以 N 次尝试里应该有 N-1 次重连。
        """
        client = PeriodScriptedClient({FREQ_5MIN: [], FREQ_DAILY: [_df()]})

        ap._mirror_serves_bars(client, ("1.1.1.1", 7709))

        self.assertEqual(client.client.reconnects, ap._MOOTDX_PROBE_ATTEMPTS - 1)

    def test_cold_start_timeout_recovers_after_a_reconnect(self):
        """生产实测形态：新连接首个请求超时，重连后立刻取到数据（0.02s）。

        2026-10-05 生产容器内实测 115.238.90.165（每个样本都新建连接）：成功的调用
        0.02s 返回、失败的调用吃满整个 socket 超时且那条连接永不恢复；重连重试能把
        5min 端点救回来。校验恰好总跑在刚建好的连接上，是这种失败最集中的时刻 ——
        不给重连重试，主用周期就拿不到正面证据，整个镜像不被采用，正是 VEW-69 报的
        「分钟端点完全正常的镜像被整源判死」。
        """

        def _slow_empty():
            time.sleep(TEST_SLOW_DELAY)
            return _empty()

        client = PeriodScriptedClient(
            {FREQ_5MIN: [_slow_empty, _df()], FREQ_DAILY: [_df()]}
        )
        verdict = ap._mirror_serves_bars(client, ("115.238.90.165", 7709))

        self.assertEqual(verdict, ap.MIRROR_SERVES)
        self.assertEqual(client.frequency_calls(FREQ_5MIN), 2)
        self.assertEqual(client.client.reconnects, 1)
        self.assertEqual(client.frequency_calls(FREQ_DAILY), 1)


class FetchPathQuorumTests(MirrorJitterBase):
    """取数路径的空返回确认：单次抖动不再摘除 client、不再触发全量重扫。"""

    def _provider(self):
        return object.__new__(ap.AStockDataProvider)

    def test_single_jittery_empty_keeps_client_and_does_not_rescan(self):
        """探针标的前两次空、第三次有数据 → 保留 client，且不触发镜像重扫。"""
        client = PeriodScriptedClient(
            {FREQ_5MIN: [_empty(), _empty(), _df()], FREQ_DAILY: [_df()]}
        )
        ap._mootdx_client = client

        with mock.patch.object(ap, "_init_mootdx_client") as rescan:
            result = self._provider()._fetch_kline_mootdx("000001", "5", "", "")

        self.assertIsNone(result)
        self.assertIs(ap._mootdx_client, client, "抖动不得摘除缓存 client")
        rescan.assert_not_called()
        self.assertEqual(len(client.calls), 3)

    def test_confirmed_structural_empty_invalidates_client(self):
        """连续快速空返回确认结构性不可用 → 仍然摘除（不放弃原有的自愈能力）。"""
        client = PeriodScriptedClient({FREQ_5MIN: [], FREQ_DAILY: [_df()]})
        ap._mootdx_client = client

        result = self._provider()._fetch_kline_mootdx("000001", "5", "", "")

        self.assertIsNone(result)
        self.assertIsNone(ap._mootdx_client)
        self.assertEqual(len(client.calls), 1 + ap._MOOTDX_PROBE_ATTEMPTS)

    def test_slow_failure_does_not_invalidate_client(self):
        """慢失败（超时）是「没测到」，不构成摘除依据。"""
        client = PeriodScriptedClient(
            {FREQ_5MIN: [], FREQ_DAILY: [_df()]},
            delay=TEST_SLOW_DELAY,
        )
        ap._mootdx_client = client

        self.assertIsNone(self._provider()._fetch_kline_mootdx("000001", "5", "", ""))
        self.assertIs(ap._mootdx_client, client)


class SourceProbePeriodTests(MirrorJitterBase):
    """源级探针按周期判定，不再用单一日线周期代表整个源（VEW-70 第 4 项）。"""

    def test_jittery_daily_keeps_source_up(self):
        client = PeriodScriptedClient(
            {FREQ_5MIN: [_df()], FREQ_DAILY: [_empty(), _df()]}
        )
        ap._mootdx_client = client

        result = probe_mootdx()

        self.assertEqual(result.status, "up")
        self.assertIn("5min=ok", result.detail)
        self.assertIn("daily=ok", result.detail)
        self.assertIs(ap._mootdx_client, client, "抖动不得摘除 client")

    def test_structural_empty_marks_source_down_and_invalidates(self):
        client = PeriodScriptedClient({FREQ_5MIN: [_df()], FREQ_DAILY: []})
        ap._mootdx_client = client

        result = probe_mootdx()

        self.assertEqual(result.status, "down")
        self.assertIn("daily=dead", result.detail)
        self.assertIsNone(ap._mootdx_client)

    def test_slow_failure_defers_without_touching_source_status(self):
        """慢失败没结论 → 只留痕、保留上次状态，不把抖动写成源故障。"""
        client = PeriodScriptedClient(
            {FREQ_5MIN: [_df()], FREQ_DAILY: []},
            delay=TEST_SLOW_DELAY,
        )
        ap._mootdx_client = client
        source_monitor.record_attempt("mootdx", ok=True, duration_ms=10.0)
        before = source_monitor.metrics()["sources"]["mootdx"]

        result = probe_mootdx()

        self.assertEqual(result.status, "unknown")
        after = source_monitor.metrics()["sources"]["mootdx"]
        self.assertEqual(after["status"], "up", "顺延不得改写源状态")
        self.assertEqual(after["total_requests"], before["total_requests"])
        self.assertIs(ap._mootdx_client, client)


class ProbeTuningInvariantTests(unittest.TestCase):
    """裁决所依赖的参数不变量。单独成类：这些是真值，不受上面测试替身的影响。"""

    def test_slow_failure_threshold_sits_below_the_probe_socket_timeout(self):
        """socket 超时必须落进「慢失败」一侧。

        否则黑洞镜像（TCP 通、从不回包）会被当成快速抖动反复重试，单个候选的成本
        从 5.3s 涨到 16s，吃光 _MOOTDX_SCAN_BUDGET，让整轮扫描失败 —— 那是比误判
        更糟的结果（VEW-62 的并发竞速正是为此调参的）。
        """
        self.assertLess(ap._MOOTDX_PROBE_RETRY_MAX_SECONDS, ap._MOOTDX_PROBE_TIMEOUT)

    def test_retry_actually_retries(self):
        self.assertGreaterEqual(ap._MOOTDX_PROBE_ATTEMPTS, 2)

    def test_worst_case_candidate_cost_stays_within_the_scan_budget(self):
        """最坏候选成本必须装得进 _MOOTDX_SCAN_BUDGET。

        一次尝试 = 重连 + 一次取数，失败成本是 _MOOTDX_PROBE_TIMEOUT（探测期已关掉
        tdxpy 的库内重试，所以一次 ``bars()`` 就是一次连接尝试，成本可见、可计数）。
        生产实测失败的调用吃满整个超时（2.5s），成功的 0.02s 返回。

        上界 = 尝试次数 × 超时 + (尝试次数 - 1) × 退避（退避只在两次尝试之间）。
        超过 _MOOTDX_SCAN_BUDGET 就会退化成 issue 里那条
        `并发探测 15 个镜像超出探测预算, 放弃本轮` —— 那正是本 issue 要消除的症状，
        所以这条不变量是「重试次数」这个旋钮的上限。
        """
        worst = (
            ap._MOOTDX_PROBE_ATTEMPTS * ap._MOOTDX_PROBE_TIMEOUT
            + (ap._MOOTDX_PROBE_ATTEMPTS - 1) * ap._MOOTDX_PROBE_ATTEMPT_BACKOFF
        )

        self.assertLess(worst, ap._MOOTDX_SCAN_BUDGET)

    def test_primary_period_is_probed_first(self):
        """主用周期（5min）必须排在最前。

        ``MIRROR_DEGRADED`` 的裁决依赖「先拿到正面证据、再裁决慢失败的那个周期」：
        顺序反过来的话，日线慢失败会先被判成 ``MIRROR_UNKNOWN`` 而被丢弃。
        """
        self.assertEqual(ap._MOOTDX_PROBE_FREQUENCIES[0], FREQ_5MIN)
        self.assertIn(FREQ_DAILY, ap._MOOTDX_PROBE_FREQUENCIES)


class MirrorHealthRegistryTests(MirrorJitterBase):
    """分周期健康档案：按 (镜像, 周期) 记录成功率与最近成功时间。"""

    def test_records_success_rate_and_last_success_per_period(self):
        registry = MirrorHealthRegistry()
        registry.record("1.1.1.1:7709", FREQ_DAILY, ok=True, duration_ms=12.0)
        registry.record("1.1.1.1:7709", FREQ_DAILY, ok=False, error="返回空")
        registry.record("1.1.1.1:7709", FREQ_5MIN, ok=True, duration_ms=8.0)

        snap = registry.snapshot()
        daily = snap["periods"]["daily"]
        self.assertEqual(
            (daily["total"], daily["success"], daily["failure"]), (2, 1, 1)
        )
        self.assertEqual(daily["success_rate"], 0.5)
        self.assertEqual(daily["consecutive_failures"], 1)
        self.assertIsNotNone(daily["last_success_at"])
        self.assertIsNotNone(daily["last_failure_at"])
        # 「分钟可用、日线抖动」在档案里一眼可分
        self.assertEqual(snap["periods"]["5min"]["success_rate"], 1.0)
        self.assertEqual(sorted(snap["mirrors"]["1.1.1.1:7709"]), ["5min", "daily"])

    def test_has_recent_success_is_scoped_to_mirror_and_period(self):
        registry = MirrorHealthRegistry()
        registry.record("1.1.1.1:7709", FREQ_5MIN, ok=True)

        self.assertTrue(registry.has_recent_success("1.1.1.1:7709", FREQ_5MIN, 60))
        self.assertFalse(registry.has_recent_success("1.1.1.1:7709", FREQ_DAILY, 60))
        self.assertFalse(registry.has_recent_success("2.2.2.2:7709", FREQ_5MIN, 60))
        self.assertFalse(registry.has_recent_success("1.1.1.1:7709", FREQ_5MIN, -1))

    def test_probe_samples_are_written_to_the_shared_registry(self):
        """校验路径的样本进全局档案 —— 判定依据与对外呈现必须是同一批样本。"""
        client = PeriodScriptedClient(
            {FREQ_5MIN: [_df()], FREQ_DAILY: [_empty(), _df()]}
        )
        ap._mirror_serves_bars(client, ("1.1.1.1", 7709))

        snap = mirror_health.snapshot()
        self.assertEqual(snap["periods"]["daily"]["total"], 2)
        self.assertEqual(snap["periods"]["daily"]["success"], 1)
        self.assertEqual(snap["periods"]["5min"]["success"], 1)

    def test_health_endpoint_exposes_mirror_periods(self):
        from app.routers.health import source_health

        mirror_health.record("1.1.1.1:7709", FREQ_DAILY, ok=False, error="返回空")
        payload = source_health(refresh=False)

        self.assertIn("mirror_periods", payload)
        self.assertEqual(payload["mirror_periods"]["periods"]["daily"]["failure"], 1)


if __name__ == "__main__":
    unittest.main()
