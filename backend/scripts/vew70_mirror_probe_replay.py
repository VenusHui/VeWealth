"""VEW-70 复现实测：镜像端点的抖动率、重试/裁决后的最终判定、以及是否仍触发全量重扫。

背景（VEW-69 第六节，VEW-70 生产复测）：唯一可用镜像 ``115.238.90.165`` 对新连接
**丢首包** —— 新建连接的首个请求经常失败，而同一条连接再试一次往往就能取到；连接热了
之后 100% 成功。原来的校验一次空返回即否决整个镜像，判死又摘除缓存 client，之后每个
取数请求都要重走镜像扫描。

因此本脚本的每个样本都**新建连接**：同一条连接上连打 N 次会得到 100%，测不出丢首包。
脚本对**真实镜像**做四件事，输出可直接贴进 issue：

1. **选镜像**：TCP 可达 + 建连成功；
2. **冷连接原始成功率**：每个样本新建连接、单发探测（不重试）—— 抖动有多频繁的基线；
3. **裁决**：每个样本新建连接后走真实校验路径（探测模式 + 有界重试 + 三态裁决），
   给出最终判定分布、每周期尝试次数与耗时；
4. **真实扫描路径**：直接调 ``_init_mootdx_client``，把**预筛 + 并发竞速 + 兜底**整条
   路径的墙钟与结果测出来 —— 第 3 步只测裁决本身，不经过预筛和竞速，单看它会漏掉
   「预算装不下预筛 + 单候选」这类问题（VEW-70 评审必修 ①：竞速窗口被预筛吃掉 1.5s
   后整轮超时放弃，裁决层再对也没用）；
5. **级联检查**：把被采用的 client 放进缓存，跑一次真实取数路径，看 client 是否被摘除
   （摘除即意味着下一个请求会触发全量重扫）。

用法::

    cd backend && python scripts/vew70_mirror_probe_replay.py \
        --server 115.238.90.165:7709 --samples 4

不传 ``--server`` 时逐个试 curated 列表，取第一个建连成功的镜像。脚本只读不写，不改
任何生产状态（``_mootdx_client`` 等模块级缓存用完即还原）。
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import astock_provider as ap  # noqa: E402
from app.providers.mirror_health import mirror_health, period_label  # noqa: E402


def _parse_server(raw: str) -> tuple[str, int]:
    ip, _, port = raw.rpartition(":")
    return (ip or raw), int(port or 7709)


def _quotes():
    from mootdx.quotes import Quotes

    return Quotes


def _build(server: tuple[str, int]):
    return ap._build_mootdx_client(_quotes(), server, timeout=ap._MOOTDX_PROBE_TIMEOUT)


def _pick_server(explicit: str | None):
    """返回第一个建连成功的镜像（只建连，不判 K 线）。"""
    candidates = [_parse_server(explicit)] if explicit else ap._curated_mootdx_servers()
    for server in candidates:
        client = _build(server)
        if client is not None:
            ap._close_mootdx_client(client)
            return server
        print(f"  建连失败: {server[0]}:{server[1]}")
    return None


def measure_cold_raw(server: tuple[str, int], freq: int, samples: int) -> None:
    """冷连接单发成功率：每个样本都新建连接，只打一发、不重试。"""
    oks, latencies = 0, []
    for i in range(samples):
        client = _build(server)
        if client is None:
            print(f"    #{i + 1}: 建连失败")
            continue
        saved = ap._apply_probe_tuning(client)
        try:
            kind, elapsed = ap._probe_period_once(
                client, freq, ap._mirror_label(client, server)
            )
        finally:
            ap._restore_client_tuning(client, saved)
            ap._close_mootdx_client(client)
        ok = kind == ap._ATTEMPT_OK
        oks += 1 if ok else 0
        latencies.append(elapsed)
        print(f"    #{i + 1}: {'OK ' if ok else '空 '} {elapsed:6.2f}s")
    if not latencies:
        print(f"  {period_label(freq)}: 一个样本都没建成连接")
        return
    print(
        f"  {period_label(freq)}: 冷连接单发成功率 {oks}/{len(latencies)} "
        f"({oks / len(latencies):.0%}), 平均 {sum(latencies) / len(latencies):.2f}s, "
        f"最坏 {max(latencies):.2f}s"
    )


def adjudicate(server: tuple[str, int], samples: int) -> None:
    """每个样本新建连接后走真实校验路径，给出判定分布与每周期尝试次数。

    这里是 ``_try_mootdx_server`` 去掉「采用/丢弃」外壳后的主体，保留 ``verdict``
    以便直接观察裁决结果（``_try_mootdx_server`` 只返回 client 或 None）。
    """
    verdicts: Counter = Counter()
    accepted_client = None
    for i in range(samples):
        mirror_health.reset()
        client = _build(server)
        if client is None:
            print(f"    #{i + 1}: 建连失败")
            verdicts["build_failed"] += 1
            continue
        saved = ap._apply_probe_tuning(client)
        started = time.monotonic()
        try:
            verdict = ap._mirror_serves_bars(
                client, server, time.monotonic() + ap._MOOTDX_SCAN_BUDGET
            )
        finally:
            ap._restore_client_tuning(client, saved)
        elapsed = time.monotonic() - started
        cells = mirror_health.snapshot()["mirrors"].get(
            ap._mirror_label(client, server), {}
        )
        detail = " ".join(
            f"{label} 尝试 {cell['total']}/成功 {cell['success']}"
            for label, cell in sorted(cells.items())
        )
        adopted = verdict in ap._MIRROR_ACCEPTED
        print(
            f"    #{i + 1}: {verdict} ({'采用' if adopted else '不采用'}), "
            f"{elapsed:.2f}s | {detail}"
        )
        verdicts[verdict] += 1
        if adopted:
            if accepted_client is not None:
                ap._close_mootdx_client(accepted_client)
            accepted_client = client
        else:
            ap._close_mootdx_client(client)
    print(f"  判定分布: {dict(verdicts)}")
    if accepted_client is not None:
        check_cascade(accepted_client)
        ap._close_mootdx_client(accepted_client)
    else:
        print("  没有样本被采用，跳过级联检查")


def check_cascade(client) -> None:
    """取数路径遇到空返回后是否摘除 client（摘除 → 下一个请求触发全量重扫）。"""
    previous = ap._mootdx_client
    ap._mootdx_client = client
    ap._mootdx_init_failed_at = None
    calls: list = []

    def _record_init(*args, **kwargs):
        calls.append(1)
        return None

    provider = object.__new__(ap.AStockDataProvider)
    try:
        with mock.patch.object(ap, "_init_mootdx_client", _record_init):
            provider._fetch_kline_mootdx("000001", "5", "", "")
        kept = ap._mootdx_client is client
    finally:
        ap._mootdx_client = previous
    print(
        f"  取数后 client {'保留（不重扫）' if kept else '被摘除（会触发全量重扫）'}；"
        f"本轮 _init_mootdx_client 调用次数 = {len(calls)}"
    )


def measure_scan_path(label: str, pool: list[tuple[str, int]]) -> None:
    """走**真实**扫描路径（预筛 + 并发竞速 + 兜底），报告墙钟与结果。

    第 3 步只测 ``_mirror_serves_bars`` 本身，不经过预筛与竞速；而生产走的是
    ``_init_mootdx_client``：先并发 TCP 预筛（最多 ``_MOOTDX_REACHABILITY_TIMEOUT``），
    剩下的候选再交给 ``_probe_mirrors_concurrently`` 竞速，竞速窗口是「预算 − 预筛」。
    只测第 3 步会漏掉「窗口装不下单候选最坏成本 → 整轮放弃」这类问题 —— 裁决层再对
    也没用（VEW-70 评审必修 ①）。所以这里直接把整条路径的墙钟测出来，与预算对照。

    ``pool`` 被固定成调用方给的候选（并关掉扫描窗口），让每轮测的是同一批候选，
    结果可复现；生产上池子由 curated + 扫描窗口组成。
    """
    previous_client, previous_failed = ap._mootdx_client, ap._mootdx_init_failed_at
    reachable_counts: list[int] = []
    real_filter = ap._filter_reachable_servers

    def _counting_filter(candidates, timeout):
        survivors = real_filter(candidates, timeout)
        reachable_counts.append(len(survivors))
        return survivors

    ap._mootdx_client = None
    ap._mootdx_init_failed_at = None
    started = time.monotonic()
    try:
        with mock.patch.object(
            ap, "_curated_mootdx_servers", lambda: list(pool)
        ), mock.patch.object(
            ap, "_mootdx_scan_due", return_value=False
        ), mock.patch.object(
            ap, "_filter_reachable_servers", _counting_filter
        ):
            client = ap._init_mootdx_client(
                deadline=time.monotonic() + ap._MOOTDX_SCAN_BUDGET
            )
        elapsed = time.monotonic() - started
    finally:
        if client is not None:
            ap._close_mootdx_client(client)
        ap._mootdx_client = previous_client
        ap._mootdx_init_failed_at = previous_failed

    survivors = reachable_counts[0] if reachable_counts else 0
    over = elapsed - ap._MOOTDX_SCAN_BUDGET
    print(
        f"  {label}: 候选 {len(pool)} 个 → 预筛后 {survivors} 个 → "
        f"{'拿到 client' if client is not None else '整轮放弃（走备源）'} "
        f"用时 {elapsed:.2f}s（预算 {ap._MOOTDX_SCAN_BUDGET:.1f}s，"
        f"{'超' if over > 0 else '余'} {abs(over):.2f}s）"
    )
    if client is None:
        print(
            "    ↑ 整轮放弃会写 _mootdx_init_failed_at，随后 "
            f"{ap._MOOTDX_RETRY_COOLDOWN:.0f}s 内所有请求直接走备源"
        )


def report_race_fit() -> None:
    """把「竞速窗口装不装得下单候选」的三条不等式按**实测常数**打出来（必修 ①）。

    这一节不碰网络，只做算术：评审要的是「候选能不能在窗口内跑完重试」这个结论，
    而它完全由四个常数决定。真实镜像上跑出来的墙钟（第 4 节）与之对照即可。
    """
    window = ap._MOOTDX_SCAN_BUDGET - ap._MOOTDX_REACHABILITY_TIMEOUT
    worst = ap._MOOTDX_PROBE_CANDIDATE_BUDGET
    round_worst = ap._MOOTDX_SCAN_BUDGET + ap._MOOTDX_PROBE_TIMEOUT
    print(
        f"  竞速窗口 = 预算 − 预筛 = {ap._MOOTDX_SCAN_BUDGET} − "
        f"{ap._MOOTDX_REACHABILITY_TIMEOUT} = {window:.1f}s"
    )
    print(
        f"  单候选最坏成本 = {worst:.1f}s（{ap._MOOTDX_PROBE_TIMEOUT}s × 2 次慢尝试 + "
        f"退避），{'装得下' if window >= worst else '装不下'}"
    )
    print(
        f"  整轮最坏 = 预算 + 一次不可打断的尝试 = {round_worst:.1f}s ≤ "
        f"分钟链路 {ap._MOOTDX_MINUTE_BUDGET}s"
    )
    print(
        f"  东财回退仍拿得到 {ap._MOOTDX_MINUTE_BUDGET - round_worst:.1f}s ≥ "
        f"下限 {ap._EASTMONEY_FALLBACK_MIN_TIMEOUT}s"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server", default=None, help="镜像 ip:port，缺省逐个试 curated"
    )
    parser.add_argument("--samples", type=int, default=4, help="冷连接样本数")
    args = parser.parse_args()

    print("== 1. 选择镜像 ==")
    server = _pick_server(args.server)
    if server is None:
        print("没有可用镜像，无法实测")
        return 1
    print(f"  采用 {server[0]}:{server[1]}")

    print("\n== 2. 冷连接原始成功率（每个样本新建连接、单发、不重试）==")
    for freq in ap._MOOTDX_PROBE_FREQUENCIES:
        measure_cold_raw(server, freq, args.samples)

    print("\n== 3. 裁决（每个样本新建连接，走真实校验路径）==")
    adjudicate(server, args.samples)

    print("\n== 4. 竞速窗口 vs 单候选最坏成本（必修 ① 的三条不等式）==")
    report_race_fit()

    print("\n== 5. 真实扫描路径（预筛 + 并发竞速 + 兜底，_init_mootdx_client）==")
    # 5a 只留目标镜像：预筛后剩 1 个候选，走 _probe_candidate（无竞速截断）
    measure_scan_path("单候选（目标镜像）", [server])
    # 5b 目标镜像 + 池里其余可达候选：走竞速分支，窗口 = 预算 − 预筛
    pool = [server] + [s for s in ap._curated_mootdx_servers() if s != server][:4]
    measure_scan_path("多候选（含竞速截断风险）", pool)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
