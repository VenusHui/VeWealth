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


class FakeSocket:
    """最小 socket 替身：记录探测模式套上去的超时，供恢复断言检查。"""

    def __init__(self):
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value


class FakeTdxApi:
    """最小 tdxpy API 替身：让 ``_reconnect_probe_client`` 能工作并记录重连次数。

    重连是探测重试的**有效单位**（生产实测：丢包是连接级的，被丢的连接永不恢复），
    所以假 client 必须有可重连的 ``client``，否则测出来的是「同连接重试」——那正是
    生产上无效的那种。
    """

    def __init__(self, ip: str = "1.1.1.1", port: int = 7709, connect_ok: bool = True):
        self.ip = ip
        self.port = port
        self.reconnects = 0
        # tdxpy 的 ``connect`` 在 ``raise_exception=False``（默认）下**返回 False
        # 而不抛异常**，所以替身必须能模拟「重连失败」这条路径（评审必修 ②）。
        self.connect_ok = connect_ok
        # 底层 socket 与 tdxpy 的库内重试开关：探测模式会改它们，交回取数路径前
        # 必须恢复（评审必修 ③）。
        self.client = FakeSocket()
        self.auto_retry = True
        self.retry_strategy = "tdxpy-default"

    def disconnect(self):
        return None

    def connect(self, ip=None, port=7709, time_out=None, **kwargs):
        self.reconnects += 1
        return True if self.connect_ok else False


class SlowConnectTdxApi(FakeTdxApi):
    """建连本身要花时间的替身：真实镜像上重连最坏吃掉一整个探测超时。

    复审必修 ② 的形态就出在这里 —— 重连发生在**一轮探测中途**，判定若只看
    ``now >= deadline``，重连后的那点余量会被当成「还装得下一次完整尝试」，于是
    候选冲破竞速窗口（实测重连 2.0s 时候选跑到 7.20s，窗口只有 6.5s）。
    """

    def __init__(self, connect_delay: float, **kwargs):
        super().__init__(**kwargs)
        self._connect_delay = connect_delay

    def connect(self, ip=None, port=7709, time_out=None, **kwargs):
        time.sleep(self._connect_delay)
        return super().connect(ip, port, time_out, **kwargs)


class PeriodScriptedClient:
    """按 ``frequency`` 消费预设响应的假 client，可注入单次调用耗时。

    ``scripts`` 形如 ``{0: [_df(), _empty()], 4: [...]}``；队列耗尽后恒返回空，
    与真实镜像「该周期没有数据」的表现一致。
    """

    def __init__(
        self, scripts, delay: float = 0.0, ip: str = "1.1.1.1", connect_ok: bool = True
    ):
        self._scripts = {freq: list(items) for freq, items in scripts.items()}
        self._delay = delay
        self.calls: list[dict] = []
        self.client = FakeTdxApi(ip=ip, connect_ok=connect_ok)

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


class ServerRoutingQuotes:
    """按 ``server`` 返回不同 client 的假 Quotes：让竞速分支可以逐个候选脚本化。"""

    def __init__(self, clients: dict):
        self._clients = clients
        self.servers: list = []

    def factory(self, *args, **kwargs):
        server = kwargs.get("server")
        self.servers.append(server)
        return self._clients[server]


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


class RaceBranchTests(MirrorJitterBase):
    """验收场景必须走**竞速分支**也成立（评审必修 ①）。

    上面那些用例直接调 ``_mirror_serves_bars``，不经过预筛与竞速；而生产上预筛后可达
    候选通常不止 1 个（VEW-62 实测 15 个接受连接），走的是
    ``_probe_mirrors_concurrently`` —— 它的 ``as_completed`` 有超时截断，窗口是
    「预算 − 预筛」。只测裁决层会漏掉「窗口装不下候选 → 整轮放弃」这类问题。
    """

    def test_race_adopts_a_degraded_candidate(self):
        """5min 正常 + 日线慢失败的候选，在多候选竞速里也要被判 degraded 并胜出。"""
        jittery = PeriodScriptedClient(
            {FREQ_5MIN: [_df()], FREQ_DAILY: []}, delay=TEST_SLOW_DELAY
        )
        blackhole = PeriodScriptedClient({}, delay=TEST_SLOW_DELAY)
        quotes = ServerRoutingQuotes(
            {("1.1.1.1", 7709): jittery, ("2.2.2.2", 7709): blackhole}
        )
        deadline = time.monotonic() + ap._MOOTDX_SCAN_BUDGET

        winner = ap._probe_mirrors_concurrently(
            quotes, [("1.1.1.1", 7709), ("2.2.2.2", 7709)], deadline=deadline
        )

        self.assertIsNotNone(winner, "竞速不得整轮放弃")
        self.assertEqual(winner[0], ("1.1.1.1", 7709))
        self.assertIs(winner[1], jittery)
        self.assertLess(time.monotonic(), deadline, "必须在竞速窗口内出结论")

    def test_race_truncation_would_drop_the_same_candidate(self):
        """对照：窗口小于候选实际成本时，同一个候选会被整轮丢掉。

        这条钉住的是**机制**（上面那条不变量钉住的是真实常数）：没有它，前一条用例
        在窗口被改小之后仍会通过，测不出「截断」这件事。

        真实常数下窗口（6.5s）已经装得下候选预算（5.4s），所以这条只能缩窗口来演示
        机制：窗口只给半次尝试（0.2s），而候选光跑完 5min 那一发就要 0.4s。候选的
        future 要跑完整个裁决才出结果，窗口一过 ``as_completed`` 就抛
        FuturesTimeoutError，整轮放弃。
        """
        per_attempt = 0.4
        jittery = PeriodScriptedClient(
            {FREQ_5MIN: [_df()], FREQ_DAILY: []}, delay=per_attempt
        )
        quotes = ServerRoutingQuotes({("1.1.1.1", 7709): jittery})
        deadline = time.monotonic() + per_attempt / 2

        winner = ap._probe_mirrors_concurrently(
            quotes, [("1.1.1.1", 7709), ("2.2.2.2", 7709)], deadline=deadline
        )

        self.assertIsNone(winner, "窗口不够时必须整轮放弃（这正是要避免的形态）")


class CostBoundedRetryTests(MirrorJitterBase):
    """重试次数由**成本**截断，不由固定次数（评审必修 ①）。

    生产实测探测成本是双峰的：成功调用 0.02s，失败调用吃满整个 socket 超时。按固定
    「每周期 3 次」设上限的写法会让两次尝试各自有界、合计无界 —— 单候选最坏
    3 × 2.5s = 7.9s，装不进 6.5s 的竞速窗口，竞速整轮放弃、备源被冷却 30s。这里把
    timeout 与候选预算按比例缩小，用**真实墙钟**把两条路径都测出来（不变量那条测的是
    真实常数，这条测的是机制）。
    """

    def _shrink(self, timeout: float, budget: float) -> None:
        for name, value in (
            ("_MOOTDX_PROBE_TIMEOUT", timeout),
            ("_MOOTDX_PROBE_CANDIDATE_BUDGET", budget),
        ):
            patcher = mock.patch.object(ap, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_slow_failures_are_truncated_by_the_candidate_budget(self):
        """慢失败：装得下几次试几次，候选预算用尽即收手（结论仍是「未证实」）。

        本用例的算式（两次 0.4s 装得下、第三次 0.6s 装不下）**以退避为 0 为前提**，
        基类把退避置 0 正是为了快。退避计入成本这条由
        :meth:`test_backoff_counts_toward_the_candidate_cost` 单独钉住。
        """
        self._shrink(timeout=0.2, budget=0.5)
        client = PeriodScriptedClient({FREQ_5MIN: [], FREQ_DAILY: [_df()]}, delay=0.2)

        verdict = ap._mirror_serves_bars(client, ("1.1.1.1", 7709))

        # 两次 = 0.4 装得下，第三次要 0.6 > 0.5 —— 按成本停在 2 次，而不是 3 次
        self.assertEqual(client.frequency_calls(FREQ_5MIN), 2)
        self.assertEqual(verdict, ap.MIRROR_UNKNOWN, "慢失败只判未证实，不判死")

    def test_backoff_counts_toward_the_candidate_cost(self):
        """退避也是候选成本的一部分，不能只在「退避免费」时成立（复审次要项 ①）。

        真实退避 0.2s 下慢失败路径停在 **1** 次而不是 2 次 —— 上面的用例把这个变量
        置 0 了，测不到这条。这里按比例把退避放大到 0.15s：一次 0.2s 的尝试加一次
        0.15s 的退避已经 0.35s，再要一次 0.2s 的尝试就是 0.55s > 0.5s 的候选预算。
        """
        self._shrink(timeout=0.2, budget=0.5)
        backoff = mock.patch.object(ap, "_MOOTDX_PROBE_ATTEMPT_BACKOFF", 0.15)
        backoff.start()
        self.addCleanup(backoff.stop)
        client = PeriodScriptedClient({FREQ_5MIN: []}, delay=0.2)

        verdict = ap._mirror_serves_bars(client, ("1.1.1.1", 7709))

        self.assertEqual(client.frequency_calls(FREQ_5MIN), 1)
        self.assertEqual(verdict, ap.MIRROR_UNKNOWN)

    def test_mirror_dead_is_bounded_by_the_candidate_budget_without_a_deadline(self):
        """取数路径的确认探测（日线 / CYQ 路径不带 deadline）同样受候选预算约束。

        复审必修 ①：``_mootdx_mirror_dead`` 此前把 ``deadline=None`` 原样传下去，
        ``_probe_period_with_retry`` 里三处「装得下」判定全被跳过，一次确认最坏跑满
        3 × 2.5s + 退避 ≈ 7.9s —— 而它持的是**全局取数锁**。现在它与
        ``_mirror_serves_bars`` 同一写法：自造候选预算 deadline，有传入值就取 min。
        """
        self._shrink(timeout=0.2, budget=0.5)
        client = PeriodScriptedClient({FREQ_5MIN: []}, delay=0.2)

        started = time.monotonic()
        dead = ap._mootdx_mirror_dead(client, FREQ_5MIN)
        elapsed = time.monotonic() - started

        # 两次 = 0.4 装得下，第三次要 0.6 > 0.5 —— 停在 2 次，而不是跑满 3 次
        self.assertEqual(client.frequency_calls(FREQ_5MIN), 2)
        self.assertFalse(dead, "慢失败只判「未证实」，不据此摘除 client")
        self.assertLess(elapsed, ap._MOOTDX_PROBE_ATTEMPTS * 0.2, "上界由候选预算给出")

    def test_fast_empties_still_get_the_full_attempt_count(self):
        """快速空返回成本近零，仍跑满 ``_MOOTDX_PROBE_ATTEMPTS`` 次。

        结构性不可用是这套裁决里唯一的判死依据（VEW-36 的 AND 意图），不能因为
        「按成本截断」被一起砍掉 —— 那会把误判率从 p³ 退回 p²。
        """
        self._shrink(timeout=0.2, budget=0.5)
        client = PeriodScriptedClient({FREQ_5MIN: [], FREQ_DAILY: [_df()]})

        verdict = ap._mirror_serves_bars(client, ("1.1.1.1", 7709))

        self.assertEqual(client.frequency_calls(FREQ_5MIN), ap._MOOTDX_PROBE_ATTEMPTS)
        self.assertEqual(verdict, ap.MIRROR_UNAVAILABLE)

    def test_candidate_never_exceeds_its_budget_once_evidence_is_in(self):
        """已有正面证据时，装不下的那个周期按「未证实」收口，候选不越过预算。

        这是候选预算真正的收口点：主用周期用掉预算后再给日线起一次完整尝试，候选就
        会多出一个超时、冲破竞速窗口 —— 正是评审必修 ① 的形态。日线这一发只是把
        ``serves`` 细化成 ``degraded``，两个结论都采用，不值得为它越界。
        """
        self._shrink(timeout=0.2, budget=0.4)

        def _slow(result):
            def _call():
                time.sleep(0.15)
                return result

            return _call

        client = PeriodScriptedClient(
            {FREQ_5MIN: [_slow(_empty()), _slow(_df())], FREQ_DAILY: [_df()]}
        )

        started = time.monotonic()
        verdict = ap._mirror_serves_bars(client, ("1.1.1.1", 7709))
        elapsed = time.monotonic() - started

        # 5min 第二次尝试（0.3s）才成功；日线要 0.3 + 0.2 > 0.4，装不下 → 不测
        self.assertEqual(verdict, ap.MIRROR_DEGRADED)
        self.assertEqual(client.frequency_calls(FREQ_DAILY), 0)
        self.assertLessEqual(elapsed, 0.4 + TEST_SLOW_SECONDS)

    def test_a_reconnect_that_eats_the_budget_stops_the_retry(self):
        """重连后要**重新**判「一次完整尝试还装得下」（复审必修 ②）。

        重连本身可能吃掉剩下的预算，判定必须与循环开头同一条。只判 ``now >=
        deadline`` 会漏掉「重连后还剩一点余量」这一档：那时起跑的一次尝试吃满整个
        超时，候选就越过竞速窗口。重连该做还是要做（它是有效单位），只是不再起新的
        尝试 —— 结论是「未证实」，不是判死。
        """
        self._shrink(timeout=0.2, budget=0.5)
        client = PeriodScriptedClient({FREQ_5MIN: []}, delay=0.15)
        client.client = SlowConnectTdxApi(connect_delay=0.2)

        verdict = ap._probe_period_with_retry(
            client, FREQ_5MIN, "1.1.1.1:7709", time.monotonic() + 0.5
        )

        # 首次尝试 0.15s；重连 0.2s 后只剩 0.15s，装不下一次 0.2s 的尝试
        self.assertEqual(client.frequency_calls(FREQ_5MIN), 1)
        self.assertEqual(client.client.reconnects, 1, "重连本身仍要做")
        self.assertEqual(verdict, ap._PERIOD_UNPROVEN)

    def test_candidate_stays_inside_its_budget_when_reconnects_are_slow(self):
        """端到端：重连慢时候选仍不越过预算（复审必修 ② 的 7.20s 形态）。"""
        self._shrink(timeout=0.2, budget=0.4)
        client = PeriodScriptedClient({FREQ_5MIN: []}, delay=0.15)
        client.client = SlowConnectTdxApi(connect_delay=0.2)

        started = time.monotonic()
        verdict = ap._mirror_serves_bars(client, ("1.1.1.1", 7709))
        elapsed = time.monotonic() - started

        self.assertEqual(verdict, ap.MIRROR_UNKNOWN)
        self.assertLessEqual(elapsed, 0.4 + TEST_SLOW_SECONDS)

    def test_first_attempt_runs_even_when_a_retry_would_not_fit(self):
        """只有**重试**受「装得下」约束，首次尝试不受。

        预算快耗尽时试一次配置默认是 VEW-62 评审 ③ 的兜底意图；门槛若设成「一次完整
        尝试装得下」，预算一紧就一次都不试，兜底路径直接退化到备源。
        """
        self._shrink(timeout=0.2, budget=0.5)

        def _slow_empty():
            time.sleep(0.1)
            return _empty()

        client = PeriodScriptedClient({FREQ_5MIN: [_slow_empty, _df()]})

        verdict = ap._mirror_serves_bars(
            client, ("1.1.1.1", 7709), deadline=time.monotonic() + 0.25
        )

        # 首次尝试跑了（并测出慢失败）；重试要 0.1 + 0.2 > 0.25，装不下 → 收手
        self.assertEqual(client.frequency_calls(FREQ_5MIN), 1)
        self.assertEqual(verdict, ap.MIRROR_UNKNOWN)


class ReconnectFailureTests(MirrorJitterBase):
    """重连失败必须被如实上报，且不得被误判成「结构性不可用」（评审必修 ②）。

    tdxpy 的 ``BaseSocketClient.connect`` 在 ``raise_exception=False``（默认）下**返回
    False 而不抛异常**，所以只 ``try/except`` 的写法会恒返回「重连成功」。那会连锁出
    两个错：调用方以为连上了，继续在一条未连接的 socket 上取数；那次取数立刻抛
    ``OSError``，被 tdxpy 吞成 ``None``、被 mootdx 变成空 DataFrame —— 于是记成
    「快速空返回」，正好落进 ``_PERIOD_DEAD``，一次连接级失败就判死镜像、摘除 client、
    触发全量重扫，即本 issue 要打断的那条级联。
    """

    def test_reconnect_reports_connect_returning_false(self):
        client = PeriodScriptedClient({}, connect_ok=False)

        self.assertFalse(ap._reconnect_probe_client(client))
        self.assertEqual(client.client.reconnects, 1, "仍然要真的试一次建连")

    def test_reconnect_reports_success(self):
        client = PeriodScriptedClient({})

        self.assertTrue(ap._reconnect_probe_client(client))

    def test_reconnect_gives_up_when_the_budget_is_gone(self):
        """预算已尽就不建连：重连是在一轮探测中途发生的，不能把这一轮拖过 deadline。"""
        client = PeriodScriptedClient({})

        self.assertFalse(
            ap._reconnect_probe_client(client, deadline=time.monotonic() - 1)
        )
        self.assertEqual(client.client.reconnects, 0)

    def test_failed_reconnect_does_not_condemn_the_mirror(self):
        """重连失败 → 只判「未证实」，**不能**判结构性不可用。"""
        client = PeriodScriptedClient({}, connect_ok=False)

        verdict = ap._mirror_serves_bars(client, ("1.1.1.1", 7709))

        self.assertEqual(verdict, ap.MIRROR_UNKNOWN)
        self.assertNotEqual(verdict, ap.MIRROR_UNAVAILABLE)
        # 首次尝试失败后重连不成功，直接收手，不在死连接上空转满次数
        self.assertEqual(len(client.calls), 1)

    def test_fast_exception_is_not_a_structural_empty(self):
        """异常 ≠ 「服务器回了空」：快抛的异常同样只能判「未证实」。

        tdxpy 把异常吞成 ``None``、mootdx 再把它变成空 DataFrame，从 ``bars()`` 的
        返回值上无法区分 —— 但前者是「没测到」、后者是「测到了没有」，只有后者能
        支撑结构性不可用的结论。
        """

        def _boom():
            raise OSError("Transport endpoint is not connected")

        client = PeriodScriptedClient(
            {FREQ_5MIN: [_boom, _boom, _boom], FREQ_DAILY: [_df()]}
        )

        verdict = ap._mirror_serves_bars(client, ("1.1.1.1", 7709))

        self.assertEqual(verdict, ap.MIRROR_UNKNOWN)
        self.assertEqual(client.frequency_calls(FREQ_5MIN), ap._MOOTDX_PROBE_ATTEMPTS)


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

    def test_probe_runs_in_probe_mode_and_restores_fetch_mode(self):
        """源级探针必须把共享 client 切到探测模式，并在交回前恢复（评审必修 ③）。

        client 平时是取数模式（5s socket 超时 + tdxpy 默认 4 次重连重试），一次
        ``bars()`` 最坏 ≈ 25s —— 探针持着全局取数锁跑这么久，会把串行的
        ``run_all_probes()`` 连同排在后面的 eastmoney 探针一起拖住；而且 25s 远大于
        「慢失败」阈值，源状态永远只能记 deferred，`_PERIOD_DEAD` 那条分支永远走不到。
        """
        client = PeriodScriptedClient({FREQ_5MIN: [_df()], FREQ_DAILY: [_df()]})
        ap._mootdx_client = client
        seen: list[tuple] = []
        real_bars = client.bars

        def _spy(*args, **kwargs):
            seen.append(
                (
                    client.client.client.timeout,
                    client.client.auto_retry,
                    client.client.retry_strategy,
                )
            )
            return real_bars(*args, **kwargs)

        client.bars = _spy

        result = probe_mootdx()

        self.assertEqual(result.status, "up")
        self.assertTrue(seen, "探针期间必须真的取过数")
        for sock_timeout, auto_retry, strategy in seen:
            self.assertEqual(sock_timeout, ap._MOOTDX_PROBE_TIMEOUT)
            self.assertFalse(auto_retry)
            self.assertIs(strategy, ap._ProbeRetryStrategy)
        # 交回取数路径前必须恢复：漏掉恢复会让被采用的镜像永久停在探测配置上
        self.assertTrue(client.client.auto_retry)
        self.assertEqual(client.client.retry_strategy, "tdxpy-default")
        self.assertEqual(client.client.client.timeout, ap._MOOTDX_CONNECT_TIMEOUT)

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

    def _worst_candidate_seconds(self) -> float:
        """单候选最坏成本（秒）= 候选预算本身。

        成本按**成本**截断而不是按次数（评审必修 ①）：尝试只在「一次完整尝试
        （一个 _MOOTDX_PROBE_TIMEOUT）还装得下」时才起跑，所以候选不会越过
        _MOOTDX_PROBE_CANDIDATE_BUDGET。生产实测失败调用吃满整个超时（2.5s）、成功
        调用 0.02s 返回 —— 双峰成本正是这个设计的前提。
        """
        return ap._MOOTDX_PROBE_CANDIDATE_BUDGET

    def test_candidate_budget_covers_two_slow_attempts(self):
        """候选预算至少装得下**两次**慢失败尝试（含退避）。

        一次慢失败 = 一个 socket 超时；只装得下一次的话，丢首包这个抖动模式（VEW-70
        生产实测：失败是连接级的，重连才有新的一次抽签）就完全没有重试机会，等于退回
        到「一次失败即否决」。
        """
        # 判定是 `now + timeout > deadline`，要**严格**装得下就得连退避一起算进去，
        # 否则浮点误差会让第二次慢尝试时有时无。
        self.assertGreaterEqual(
            ap._MOOTDX_PROBE_CANDIDATE_BUDGET,
            2 * ap._MOOTDX_PROBE_TIMEOUT + 2 * ap._MOOTDX_PROBE_ATTEMPT_BACKOFF,
        )

    def test_candidate_budget_covers_the_full_fast_failure_path(self):
        """候选预算也要装得下**快速失败**路径的全部尝试（VEW-36 的 AND 意图）。

        结构性不可用（三次快速空返回）是这套裁决里唯一的「判死」依据，成本却几乎为
        零（实测快速空返回 ~0.02s）。若预算按最坏成本一刀切成「两次尝试」，快速路径
        也会被砍到两次，误判率从 p³ 退到 p²。所以这里钉住：快速路径跑得满
        _MOOTDX_PROBE_ATTEMPTS 次 —— 探测前的那次装不下检查按最坏成本（一个超时）
        估，所以要算上它。
        """
        self.assertGreaterEqual(
            ap._MOOTDX_PROBE_CANDIDATE_BUDGET,
            (ap._MOOTDX_PROBE_ATTEMPTS - 1) * ap._MOOTDX_PROBE_ATTEMPT_BACKOFF
            + ap._MOOTDX_PROBE_TIMEOUT,
        )

    def test_race_window_fits_a_worst_case_candidate(self):
        """**竞速窗口**必须 ≥ 单候选最坏成本 —— 这才是「重试次数」真正的上界。

        `_init_mootdx_client` 先跑一次并发 TCP 预筛（最多
        _MOOTDX_REACHABILITY_TIMEOUT），剩下的候选才交给
        `_probe_mirrors_concurrently`；竞速的 ``as_completed`` 超时是「预算 − 预筛」，
        不是整个预算。所以要断言的是**窗口**，不是总预算（评审必修 ①：只断言
        `worst < _MOOTDX_SCAN_BUDGET` 比 VEW-62 的原口径还弱，拦不住这个）。

        窗口小于候选最坏成本时：候选还没出结论竞速就整轮超时 → 返回 None →
        `_mootdx_init_failed_at` 落盘 → 30s 冷却内所有请求走备源。`MIRROR_DEGRADED`
        这种本来能采用的结论被整轮丢掉，正是本 issue 要消除的症状
        （实测：窗口 6.5s < 候选 7.9s → `并发探测 … 放弃本轮`）。
        """
        window = ap._MOOTDX_SCAN_BUDGET - ap._MOOTDX_REACHABILITY_TIMEOUT

        self.assertGreaterEqual(window, self._worst_candidate_seconds())

    def test_scan_round_fits_the_minute_chain_with_room_for_eastmoney(self):
        """整轮扫描 + 一次在跑的尝试 + 东财回退下限必须装得进分钟链路预算。

        整轮 = 扫描预算 + **一次不可打断的尝试**：deadline 只在两次尝试之间检查，正在
        跑的 ``bars()`` 拦不住，所以最坏会超出预算一个 _MOOTDX_PROBE_TIMEOUT。漏掉这
        一项会让「抬预算」看起来免费 —— 实测把预算抬到 10s 后整轮到了 12.25s，已经
        顶破 _MOOTDX_MINUTE_BUDGET(12s)，东财回退（熔断恢复主路径）被挤掉。

        分钟链路是**整条**的硬边界（< 前端 15s 超时），所以这条和上面那条是一对：
        上面那条定次数的上限，这条定预算的上限。
        """
        self.assertLessEqual(
            ap._MOOTDX_SCAN_BUDGET
            + ap._MOOTDX_PROBE_TIMEOUT
            + ap._EASTMONEY_FALLBACK_MIN_TIMEOUT,
            ap._MOOTDX_MINUTE_BUDGET,
        )

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

    def test_registry_only_observes_and_never_decides(self):
        """档案只做观测，不提供任何供裁决消费的跨轮查询（评审必修 ④）。

        裁决只看**本轮**样本。跨轮历史成功不适合当正面证据：一个刚刚结构性失效的
        镜像会因为几十秒前的成功记录被继续采用。区分「抖动」与「故障」靠的是本轮内的
        有界重连重试，不是历史窗口 —— 所以这里连 ``has_recent_success`` 都不该有。
        """
        registry = MirrorHealthRegistry()
        registry.record("1.1.1.1:7709", FREQ_5MIN, ok=True)

        self.assertFalse(hasattr(registry, "has_recent_success"))
        self.assertEqual(
            sorted(m for m in dir(registry) if not m.startswith("_")),
            ["record", "reset", "snapshot"],
        )

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
