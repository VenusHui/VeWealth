"""AStockDataProvider — implements MarketDataProvider via a-stock-data patterns.

Primary K-line source: mootdx (TCP, no IP block).
Fallback chain: Eastmoney HTTP → Tushare (daily only).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
    as_completed,
)
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import pandas as pd

from app.core.config import settings
from app.core.source_health import source_monitor
from app.providers.base import MarketDataProvider
from app.providers.provenance import DailyDataResult, DataProvenance
from app.providers.astock_data import (
    eastmoney_all_stocks,
    eastmoney_cyq,
    eastmoney_kline,
    eastmoney_trends2,
    fqt_code,
)

try:
    import tushare as ts
except Exception:
    ts = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# mootdx client (lazy, self-healing)
#
# The client is initialized on first use rather than at import time. A transient
# init failure at process start (e.g. TDX mirror handshake dropped mid-connect)
# used to leave ``_mootdx_client = None`` for the whole process lifetime, silently
# disabling the primary K-line source until the container was restarted (VEW-36).
# A lock guards concurrent first-use, and a cooldown prevents hammering the
# mirrors when the handshake keeps failing.
# ---------------------------------------------------------------------------

_mootdx_client: Optional["Quotes"] = None
_mootdx_client_lock = threading.Lock()
# 取数串行化锁：TDX 协议是「一条 TCP 连接上一次一问一答」，共享 client 被多线程
# 并发调用时响应会互相错位，取数**静默**返回空/None（调用方会当成「该标的无数据」
# 跳过，不抛异常）。实测 20 标的 × 250 根、20 线程下共享 client 仅 2/20 完整返回；
# 加锁后 20/20，且因省掉每线程建连开销反而快于每线程独立 client（12.0s vs 23.8s）
# —— 这不是性能与正确性的取舍（VEW-60）。所有共享 client 的 bars() 调用都必须持锁。
_mootdx_fetch_lock = threading.Lock()


@contextmanager
def _mootdx_fetch_guard(timeout: Optional[float] = None) -> Iterator[bool]:
    """有界获取取数锁的上下文管理器，``yield`` 出是否拿到锁（VEW-60 评审 M3/M4）。

    取数锁是全局串行点：探针、日线、分钟线共用同一把锁。无界等待会让「等锁」把
    调用方自己的墙钟预算架空 —— 分钟链路等满锁再取数，合计仍可能超过前端 15s
    超时；探针等满锁则会把串行的 ``run_all_probes()`` 整轮拖住，排在后面的
    eastmoney 探针（熔断恢复的主路径）随之延后。

    ``timeout`` 为 ``None`` 时无限期等待（日线 / CYQ 路径的既有取舍，它们没有
    墙钟预算，也不允许静默截断分页）；给定秒数时最多等这么久，超时后
    ``yield False`` 且**不持锁**，调用方须放弃本次取数、交给备源。
    """
    if timeout is None:
        acquired = _mootdx_fetch_lock.acquire()
    else:
        acquired = _mootdx_fetch_lock.acquire(timeout=max(float(timeout), 0.0))
    try:
        yield acquired
    finally:
        if acquired:
            _mootdx_fetch_lock.release()


_mootdx_init_failed_at: Optional[float] = None
# 是否有请求正在执行镜像扫描（_init_mootdx_client）。并发请求在扫描期间直接快速
# 返回 None 走备源，而不是排队阻塞在锁上等完整扫描（VEW-54）。
_mootdx_scan_in_progress = False
# Cooldown between re-init attempts after a failed handshake (seconds).
_MOOTDX_RETRY_COOLDOWN = 30.0

# 上次公开镜像扫描发现的可用镜像（ip, port）。curated 列表失效时由
# _init_mootdx_client 在扫描后写入，后续 init 优先复用它（VEW-55）。
_mootdx_discovered_server: Optional[tuple[str, int]] = None
# 上次公开镜像扫描的时间（monotonic），用于冷却判定。
_mootdx_last_scan_at: Optional[float] = None
# 公开镜像扫描的游标：每次扫描只探测 MOOTDX_SCAN_LIMIT 个镜像，游标推进使多轮
# 扫描（间隔冷却期）能覆盖完整镜像列表，而不是每次只扫开头固定窗口。
_mootdx_scan_cursor = 0

# Curated public TDX HQ mirrors. Connectivity alone is not enough: some mirrors
# accept the TCP handshake but return no K-line body, so the primary source would
# silently serve nothing (VEW-36). We keep a known-good set and validate each on
# init with a probe fetch, picking the first mirror that actually returns bars.
# The curated set goes stale over time (public mirrors churn); when all of them
# return empty, _init_mootdx_client falls back to a bounded scan over the full
# mirror list bundled with mootdx (VEW-55).
_MOOTDX_SERVERS: list[tuple[str, int]] = [
    # 实测可用的外部镜像（VEW-59/VEW-62 逐个探测确认可返回 K 线）。公开镜像池会
    # 整体漂移 —— VEW-60 复测时上面这批「主站」全部失效，而该镜像不在 mootdx
    # 内置的 38 个候选里，扫描永远找不到它，只能硬编码进 curated（VEW-62）。
    ("115.238.90.165", 7709),  # 外部镜像（VEW-59 实测可用）
    ("110.41.147.114", 7709),  # 深圳双线主站1
    ("110.41.154.219", 7709),  # 深圳双线主站6
    ("124.70.176.52", 7709),  # 上海双线主站1
    ("47.100.236.28", 7709),  # 上海双线主站2
    ("121.36.54.217", 7709),  # 北京双线主站1
    ("124.71.85.110", 7709),  # 广州双线主站1
]

# 建连超时（秒）。选中的客户端沿用该超时用于后续取数，故定成常量便于调整。
_MOOTDX_CONNECT_TIMEOUT = 5

# 探测前的 TCP 可达性预筛（VEW-62）。死镜像的成本几乎全在建连等待上（黑洞 IP 要
# 等满 socket 超时），串行探测下 _MOOTDX_SCAN_BUDGET=6s 只够覆盖约 1 个候选，公开
# 镜像扫描因此永远走不完；同时 curated 列表会先把预算吃光，扫描阶段一次都进不去。
# 预筛把所有候选并发连一遍（只做 TCP 建连，不做 TDX 协议交互），不可达的直接剪掉，
# 可达的才交给 _probe_mirrors_concurrently 做完整的 K 线校验 —— 「连着通不算数、必须
# 真能取到 K 线」的判定没有被放宽。TCP 连不上则 TDX 一定连不上，故剪枝不会误杀可用
# 镜像。
_MOOTDX_REACHABILITY_TIMEOUT = 1.5
# 预筛并发度。取足够大以便一轮覆盖整个内置池（38 个），避免多批排队把超时叠加
# 成数秒；预筛本身受冷却期约束，不会每次请求都触发。
_MOOTDX_REACHABILITY_WORKERS = 64

# 镜像探测墙钟预算（秒）。镜像池全挂时 _init_mootdx_client 会并发竞速可达候选，再
# 兜底试一次配置默认，每个镜像含建连 + 2 次探针取数（探测期受 _MOOTDX_PROBE_TIMEOUT
# 与单次重试兜底），最坏可把请求挂起数十秒、超过前端 15s 超时。竞速与兜底路径都按
# 该预算放弃（VEW-54）；调用方给了整体 deadline 时扫描仍不超过该预算（评审 F3）。
#
# 定 8s 而不是原值 6s（VEW-62 实测）：单个候选最坏成本是「一次 socket 超时 + 一次
# 重连重试」= 2.5+0.2+2.5 ≈ 5.2s，而预筛已经先花掉 1.5s，6s 预算只剩 4.5s 窗口 ——
# 恰好比重试所需的 5.2s 短，可用镜像只要丢了首包就必定被切掉（实测端到端 init
# 8 次只成功 6 次，两次失败都卡在 6.0s 预算上）。8s 留出 6.5s 窗口，重试跑得完。
_MOOTDX_SCAN_BUDGET = 8.0
# 分钟链路整体预算（秒）：mootdx 探测/取数 + 东财回退合计计入，须小于前端 15s
# 超时。超时放弃本次取数，返回空而非让请求挂起（VEW-54）。
_MOOTDX_MINUTE_BUDGET = 12.0

# 源级探针等待取数锁的上限（秒）。探针与取数共用同一 client 与同一把锁：取数正在
# 翻页时探针若无限期等待，会把串行的 run_all_probes() 整轮拖住，排在后面的
# eastmoney 探针（熔断恢复的主路径）随之延后。超时即顺延本轮：不写源状态（只记一条
# deferred 事件），也不阻塞后续探针（VEW-60 评审 M3）。
_MOOTDX_PROBE_LOCK_TIMEOUT = 5.0

# 探针校验的取数周期：深度图默认 5 分钟（frequency=0），日线（frequency=4）作
# 备用。两者都必须能取到才认为镜像可用 —— 只握手、部分周期空回来的镜像不能选。
_MOOTDX_PROBE_FREQUENCIES: tuple[int, ...] = (4, 0)

# 探测期的 socket 超时（秒），只作用于探测，镜像被采用后会恢复
# _MOOTDX_CONNECT_TIMEOUT。公开镜像里「TCP 连得上但从不回包」的占多数（VEW-62 实测
# 15 个可达候选中 14 个如此），每个候选的成本就是一次 socket 超时；收紧到 2.5s 后
# 单个死镜像实测 5.3s（2.5 超时 + 0.2 退避 + 2.5 重试），一轮并发探测装得进预算。
# 试过收到 1.5s，反而更差：可用镜像在重连后要约 2s 才回包，1.5s 会把重试那次也切掉
# （成功率 8/12 vs 2.5s 下的 11/12），所以超时不能再压。
_MOOTDX_PROBE_TIMEOUT = 2.5
# 探测期的重试退避（秒）。tdxpy 默认策略 [0.1,0.5,1,2] 会在不回包的镜像上重连重试
# 4 次，单个候选实测 52.3s，必须换掉；但完全不重试又会误杀可用镜像 —— 实测
# 115.238.90.165 每次新建连接约半数丢首个请求，一次重连重试能把成功率拉回 17/20。
# 再加一次退避会把死镜像成本推到 8.3s，超出 _MOOTDX_SCAN_BUDGET，故只保留一次。
_MOOTDX_PROBE_BACKOFFS: tuple[float, ...] = (0.2,)
# 并发探测宽度：一轮扫描同时探测的候选数上限。串行探测下 6s 预算只够覆盖 1 个候选
# （VEW-62 实测），并发后整池可达候选在一个探测窗口内出结果，可用的那个 0.1s 即返回。
_MOOTDX_PROBE_WORKERS = 16


class _ProbeRetryStrategy:
    """探测期重试策略：按 ``_MOOTDX_PROBE_BACKOFFS`` 重连重试（见其取舍说明）。

    只替换 ``retry_strategy``，仍复用 tdxpy 的重连重试机制 —— 正是它能把「丢首包」
    的可用镜像救回来，所以探测期不能简单地把 ``auto_retry`` 关掉。
    """

    @classmethod
    def generate(cls):
        yield from _MOOTDX_PROBE_BACKOFFS


def _mootdx_probe_symbol() -> str:
    """Return the known-liquid symbol used to distinguish mirror vs symbol gaps."""

    return str(getattr(settings, "SOURCE_HEALTH_PROBE_SYMBOL", "000001")).zfill(6)


def _disable_tdx_setup_handshake(
    client: Any, server: Optional[tuple[str, int]]
) -> None:
    """关闭 pytdx/tdxpy 客户端的 setup 握手包，并重连使其生效（VEW-60 / VEW-62）。

    底层 ``TdxHq_API`` 默认 ``need_setup=True``：``connect()`` 里先发 3 个 setup 包
    （取服务器信息），之后才发首个业务请求。公开通达信镜像普遍不实现该握手，其响应
    与后续 ``get_security_bars`` 的响应**错位**，导致每次取数都解析失败、恒返回
    ``None``，于是镜像被逐个判定不可用——主源其实可用，只是被库的握手挡在门外。

    **必须重连才生效**：``need_setup`` 只在 ``connect()`` 内部被读取，而
    ``StdQuotes.__init__`` 在构造时就调用了 ``connect()``；等 ``Quotes.factory``
    返回时 setup 包已经发出去、响应流已经错位，此时再置标志位对这条连接没有任何
    补救作用（VEW-62 实测，同一镜像 ``115.238.90.165``，同一 ``Quotes.factory``）：

    ==============================  ==================
    置位时机                        取数结果
    ==============================  ==================
    ``connect()`` 之前（理想）       OK，3 根日线
    ``factory`` 返回后（仅置位）     空，恒 None
    ``factory`` 返回后 + 重连        见 test_tdx_setup_handshake_*
    ==============================  ==================

    因此置位后重连一次，让会话从干净状态开始。建连本就失败的 client（``closed``
    仍为 True）跳过重连，避免死镜像白白多付一次建连超时。

    属性不存在时静默跳过，不因 mootdx/tdxpy 版本差异中断初始化。
    """
    api = getattr(client, "client", None)
    if api is None or not hasattr(api, "need_setup"):
        return
    try:
        api.need_setup = False
    except Exception as e:  # pragma: no cover - 防御性
        logger.warning(f"mootdx 关闭 setup 握手失败({server or '配置默认'}): {e}")
        return
    if getattr(api, "closed", True):
        # 建连就没成功，没有需要重建的会话；重连只会再等一次建连超时。
        return
    ip, port = getattr(api, "ip", None), getattr(api, "port", None)
    if not ip or not port:
        return
    try:
        api.disconnect()
        api.connect(ip, int(port), time_out=_MOOTDX_CONNECT_TIMEOUT)
    except Exception as e:  # pragma: no cover - 防御性
        logger.warning(f"mootdx 关闭握手后重连失败({server or '配置默认'}): {e}")


def _tcp_reachable(server: tuple[str, int], timeout: float) -> bool:
    """TCP 建连探测：能建连的镜像才可能取到 K 线（VEW-62）。"""
    try:
        with socket.create_connection(
            (server[0], int(server[1])), timeout=max(float(timeout), 0.05)
        ):
            return True
    except Exception:
        return False


def _filter_reachable_servers(
    servers: list[tuple[str, int]],
    timeout: float,
    workers: int = _MOOTDX_REACHABILITY_WORKERS,
) -> list[tuple[str, int]]:
    """并发 TCP 预筛，按入参顺序返回可达的候选（VEW-62）。

    死镜像的成本几乎全在建连等待上，串行探测会把扫描预算耗在少数几个候选上。
    并发预筛把「整池可达性」压缩到一个超时窗口内，让一轮扫描能覆盖整个镜像池；
    TDX 协议交互与 K 线校验仍由 ``_try_mootdx_server`` 逐个完成。
    """
    if not servers:
        return []
    if len(servers) == 1:
        return [servers[0]] if _tcp_reachable(servers[0], timeout) else []
    reachable: set[tuple[str, int]] = set()
    pool_size = max(min(len(servers), int(workers)), 1)
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futures = {pool.submit(_tcp_reachable, s, timeout): s for s in servers}
        for future in as_completed(futures):
            try:
                if future.result():
                    reachable.add(futures[future])
            except Exception:  # pragma: no cover - 防御性
                continue
    return [s for s in servers if s in reachable]


def _apply_probe_tuning(client: Any) -> dict:
    """把 client 切到「探测模式」，返回原值供 :func:`_restore_client_tuning` 恢复。

    探测模式 = 短 socket 超时（``_MOOTDX_PROBE_TIMEOUT``）+ 只重试一次
    （``_ProbeRetryStrategy``）。镜像被采用后必须恢复，取数路径要保留原来的 5s
    超时与 tdxpy 默认的 4 次重连重试（VEW-62）。

    短超时同时要盖到**重连新建的 socket** 上：tdxpy 的 ``last_ack_time`` 重试时会
    ``disconnect()`` + ``connect(ip, port)``，而这次 connect 用的是 ``CONNECT_TIMEOUT``
    默认值，新建的 socket 于是又变回 5s —— 实测死镜像因此要 7.8s 而不是 5.2s。故把
    ``api.connect`` 包一层，重连后重新套上探测超时。
    """
    saved: dict = {}
    api = getattr(client, "client", None)
    if api is None:
        return saved

    def _set_probe_timeout() -> bool:
        sock = getattr(api, "client", None)
        if sock is None or not hasattr(sock, "settimeout"):
            return False
        try:
            sock.settimeout(_MOOTDX_PROBE_TIMEOUT)
            return True
        except Exception:  # pragma: no cover - 防御性
            return False

    sock = getattr(api, "client", None)
    if sock is not None and hasattr(sock, "gettimeout"):
        try:
            saved["timeout"] = sock.gettimeout()
        except Exception:  # pragma: no cover - 防御性
            pass
    _set_probe_timeout()
    if hasattr(api, "connect"):
        try:
            original_connect = api.connect

            def _probe_connect(
                ip: Any = None,
                port: int = 7709,
                time_out: float = _MOOTDX_PROBE_TIMEOUT,
                **kw,
            ):
                result = original_connect(ip, port, time_out=time_out, **kw)
                _set_probe_timeout()
                return result

            saved["connect"] = original_connect
            api.connect = _probe_connect
        except Exception:  # pragma: no cover - 防御性
            saved.pop("connect", None)
    if hasattr(api, "retry_strategy"):
        try:
            saved["retry_strategy"] = api.retry_strategy
            api.retry_strategy = _ProbeRetryStrategy
        except Exception:  # pragma: no cover - 防御性
            saved.pop("retry_strategy", None)
    if hasattr(api, "auto_retry"):
        try:
            saved["auto_retry"] = api.auto_retry
            api.auto_retry = True
        except Exception:  # pragma: no cover - 防御性
            saved.pop("auto_retry", None)
    return saved


def _restore_client_tuning(client: Any, saved: dict) -> None:
    """把探测模式改动的 client 设置恢复成取数模式的原值。"""
    api = getattr(client, "client", None)
    if api is None or not saved:
        return
    sock = getattr(api, "client", None)
    if saved.get("timeout") is not None and sock is not None:
        try:
            sock.settimeout(saved["timeout"])
        except Exception:  # pragma: no cover - 防御性
            pass
    for attr in ("connect", "retry_strategy", "auto_retry"):
        if attr in saved:
            try:
                setattr(api, attr, saved[attr])
            except Exception:  # pragma: no cover - 防御性
                pass


def _build_mootdx_client(Quotes, server: Optional[tuple[str, int]]) -> Optional[Any]:
    """建一个连上 ``server`` 的 mootdx client 并修好 setup 握手（VEW-62）。

    只负责「建连 + 修握手」，不做 K 线校验 —— 校验由
    :func:`_mirror_serves_bars` 单独完成，以便并发探测时把建连串行、取数并行。
    """
    try:
        if server is None:
            client = Quotes.factory(market="std")
        else:
            client = Quotes.factory(
                market="std", server=server, timeout=_MOOTDX_CONNECT_TIMEOUT
            )
    except Exception as e:
        logger.warning(f"mootdx 连接 {server or '配置默认'} 失败: {e}")
        return None
    _disable_tdx_setup_handshake(client, server)
    return client


def _mirror_serves_bars(
    client: Any, server: Optional[tuple[str, int]], deadline: Optional[float] = None
) -> bool:
    """校验 client 的两个周期都能取到 K 线；探测期设置由调用方负责。

    只握手、部分周期空回来的镜像不能选：深度图默认取 5 分钟线（frequency=0），
    日线（frequency=4）是备用周期，任一为空都会让对应视图留白（VEW-36）。
    """
    for freq in _MOOTDX_PROBE_FREQUENCIES:
        if deadline is not None and time.monotonic() >= deadline:
            return False
        try:
            probe = client.bars(
                symbol=_mootdx_probe_symbol(), frequency=freq, start=0, offset=3
            )
        except Exception as e:  # pragma: no cover - 防御性
            logger.warning(
                f"mootdx 通过 {server or '配置默认'} 拉取 freq={freq} K线失败: {e}"
            )
            return False
        if probe is None or probe.empty:
            logger.warning(
                f"mootdx 镜像 {server or '配置默认'} freq={freq} 未返回有效K线, 跳过"
            )
            return False
    return True


def _close_mootdx_client(client: Any) -> None:
    """关闭探测失败的 client，避免并发探测时攒下没人回收的连接。"""
    try:
        client.close()
    except Exception:  # pragma: no cover - 防御性
        pass


def _try_mootdx_server(
    Quotes, server: Optional[tuple[str, int]], deadline: Optional[float] = None
):
    """Build a client for one TDX mirror and confirm it returns K-lines.

    ``server`` of ``None`` means "let mootdx use its configured default" (a bare
    ``Quotes.factory``). The mirror is accepted only if both probe periods return
    bars (see :func:`_mirror_serves_bars`).

    ``deadline`` is an absolute ``time.monotonic()`` timestamp bounding this probe;
    once reached the probe gives up (``None``) so a slow mirror cannot eat the whole
    request budget (VEW-54).

    The probe runs in "probe mode" (short socket timeout, single retry — VEW-62) and
    the client is restored to fetch mode before being handed back, so the long-lived
    fetch client keeps the original 5s timeout and tdxpy's default retry behaviour.
    """
    if deadline is not None and time.monotonic() >= deadline:
        return None
    client = _build_mootdx_client(Quotes, server)
    if client is None:
        return None
    saved = _apply_probe_tuning(client)
    try:
        if not _mirror_serves_bars(client, server, deadline):
            _close_mootdx_client(client)
            return None
    finally:
        _restore_client_tuning(client, saved)
    logger.info(f"mootdx 通过 {server or '配置默认'} 取得K线(日线+5分钟), 使用该镜像")
    return client


def _probe_candidate(
    Quotes,
    server: tuple[str, int],
    deadline: Optional[float],
    build_lock: threading.Lock,
) -> Optional[tuple[tuple[str, int], Any]]:
    """并发探测一个候选：建连串行（见 :func:`_probe_mirrors_concurrently`），取数并行。"""
    with build_lock:
        client = _build_mootdx_client(Quotes, server)
    if client is None:
        return None
    saved = _apply_probe_tuning(client)
    try:
        if not _mirror_serves_bars(client, server, deadline):
            _close_mootdx_client(client)
            return None
    finally:
        _restore_client_tuning(client, saved)
    return server, client


def _probe_mirrors_concurrently(
    Quotes, servers: list[tuple[str, int]], deadline: Optional[float] = None
) -> Optional[tuple[tuple[str, int], Any]]:
    """并发探测候选镜像，返回第一个能取到 K 线的 ``(server, client)``；全失败返回 ``None``。

    为什么必须并发（VEW-62 实测）：公开镜像池里「TCP 连得上但从不回包」的占多数，
    单个候选的探测成本就是一次 socket 超时（39 个候选里 24 个拒绝连接、15 个接受
    连接、只有 1 个真能取到 K 线）。串行探测下 6s 预算只够覆盖 1 个候选，扫描永远
    走不完；并发后整池可达候选在一个探测窗口内出结果，可用的那个 0.03s 就返回。

    建 client 仍然串行：mootdx 的 config 是**模块级单例**，``StdQuotes.__init__``
    会先 ``config.set('BESTIP', {'HQ': server})`` 再读回来决定连哪个 IP，并发构造会
    让客户端连到别人的镜像。取数校验则并发安全：每个 client 是独立 TCP 连接，
    ``bars()`` 之间没有共享状态。

    ``deadline`` 是硬边界：等待用 ``as_completed(timeout=剩余预算)`` 截断，整池全挂时
    也在预算内返回 ``None``，而不是让 15 个候选各跑满自己的 socket 超时（实测未截断
    时会超出 6s 预算 ~4s）。

    拿到第一个可用 client 后立即返回，未完成的探测线程由 ``shutdown(wait=False)``
    放行，不阻塞调用方。落选 / 超时仍在跑的候选，其 client 由 :func:`_discard_losers`
    兜底回收。
    """
    if not servers:
        return None
    if len(servers) == 1:
        return _probe_candidate(Quotes, servers[0], deadline, threading.Lock())
    build_lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=min(len(servers), _MOOTDX_PROBE_WORKERS))
    try:
        futures = [
            pool.submit(_probe_candidate, Quotes, s, deadline, build_lock)
            for s in servers
        ]
        timeout = None
        if deadline is not None:
            timeout = max(0.0, deadline - time.monotonic())
        try:
            for future in as_completed(futures, timeout=timeout):
                try:
                    winner = future.result()
                except Exception:  # pragma: no cover - 防御性
                    continue
                if winner is not None:
                    _discard_losers(futures, future)
                    logger.info(
                        f"mootdx 并发探测 {len(servers)} 个可达镜像, "
                        f"选中 {winner[0][0]}:{winner[0][1]}"
                    )
                    return winner
        except FuturesTimeoutError:
            logger.warning(
                f"mootdx 并发探测 {len(servers)} 个镜像超出探测预算, 放弃本轮"
            )
            _discard_losers(futures, None)
            return None
        return None
    finally:
        # 拿到结果即返回，不等落选候选把 socket 超时跑完。
        pool.shutdown(wait=False, cancel_futures=True)


def _discard_losers(futures: list, winner: Any) -> None:
    """回收竞速落选者的 client：已完成的直接关，还在跑的挂回调等它跑完再关。

    ``winner`` 传 ``None`` 表示没有胜出者（整轮超预算放弃），此时所有候选都按落选
    处理。

    不加这一步会漏连接：一个候选探测成功后返回的 client 没人接手，而它的 TCP 连接
    会一直挂到进程退出。挂回调而不是等待，是为了不把落选者剩下的 socket 超时算进
    请求延迟。
    """

    def _close_if_won(fut: Any) -> None:
        try:
            result = fut.result()
        except Exception:  # pragma: no cover - 防御性
            return
        if result is not None:
            _close_mootdx_client(result[1])

    for future in futures:
        if future is winner:
            continue
        if future.done():
            _close_if_won(future)
        else:
            future.add_done_callback(_close_if_won)


def _init_mootdx_client(deadline: Optional[float] = None):
    """Create a mootdx client connected to a mirror that returns real data.

    ``Quotes.factory(market="std")`` without ``bestip`` just reuses whatever mirror
    is recorded in the local mootdx config, and that mirror may handshake but serve
    no K-line body. Here we probe candidates in order and keep the first one that
    returns bars (VEW-36):

    1. ``settings.MOOTDX_SERVERS`` override, else ``MOOTDX_EXTRA_SERVERS`` + the
       curated default list;
    2. the mirror discovered by the last public scan (fast reuse);
    3. a bounded scan over the mirror list bundled with mootdx when the curated
       set has gone stale (VEW-55);
    4. mootdx's configured default.

    All candidates are first passed through a concurrent TCP reachability filter
    (VEW-62). Dead mirrors cost a full connect timeout each, so probing them
    serially let the curated list exhaust ``_MOOTDX_SCAN_BUDGET`` before the scan
    phase was ever reached — the scan effectively never ran. The filter prunes
    unreachable candidates in one parallel pass, and the survivors are then raced
    in parallel (:func:`_probe_mirrors_concurrently`); only reachable ones get the
    full K-line validation, so the scan covers the whole pool inside one request.

    Because the race takes the first mirror to answer, the curated order is a
    preference, not a guarantee: when several mirrors work, the quickest wins.

    Returns ``None`` only if no mirror yields data. Isolated into its own function
    so tests can inject a failing/succeeding factory without reaching the live TDX
    mirrors.

    ``deadline`` is an absolute ``time.monotonic()`` timestamp bounding the whole
    scan (default: ``now + _MOOTDX_SCAN_BUDGET``). It is checked between mirror
    attempts so a fully-dead mirror pool cannot hang the request for tens of
    seconds; the scan gives up and returns ``None`` once the budget is exhausted
    (VEW-54).
    """
    try:
        from mootdx.quotes import Quotes
    except Exception as e:  # pragma: no cover - 依赖缺失
        logger.warning(f"mootdx 依赖不可用: {e}")
        return None

    global _mootdx_discovered_server, _mootdx_last_scan_at

    if deadline is None:
        deadline = time.monotonic() + _MOOTDX_SCAN_BUDGET

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None

    # Fast candidates first: settings override / curated list, then the last
    # mirror a scan discovered, then this round's scan window. Each is probed
    # with a real K-line fetch.
    candidates: list[tuple[str, int]] = _curated_mootdx_servers()
    if _mootdx_discovered_server and _mootdx_discovered_server not in candidates:
        candidates.append(_mootdx_discovered_server)

    # Bounded scan over the public HQ list, gated by a cooldown so a dead mirror
    # pool isn't re-scanned on every request (VEW-55). The window is appended to
    # the candidate list rather than probed in a second pass, so the reachability
    # filter below covers curated and scan candidates in the same parallel pass.
    scan_window: list[tuple[str, int]] = []
    if _mootdx_scan_due():
        scan_window = _mootdx_scan_candidates()
        for server in scan_window:
            if server not in candidates:
                candidates.append(server)

    # 并发 TCP 预筛：剪掉黑洞/拒绝的候选，剩下的才值得花一次完整 K 线校验。
    # 预筛自身也受剩余预算约束，不会把整体预算吃光。
    reachable = _filter_reachable_servers(
        candidates, min(_MOOTDX_REACHABILITY_TIMEOUT, remaining)
    )

    # 并发竞速探测可达候选，第一个返回 K 线的镜像胜出（VEW-62）。串行探测在 6s
    # 预算内只够覆盖 1 个候选，扫描窗口永远轮不到；并发后整池可达候选在同一个探测
    # 窗口内出结果，可用的那个通常零点几秒就返回。
    if reachable and time.monotonic() < deadline:
        winner = _probe_mirrors_concurrently(Quotes, reachable, deadline=deadline)
        if winner is not None:
            server, client = winner
            _mootdx_discovered_server = server
            if scan_window:
                _mootdx_last_scan_at = time.monotonic()
            return client

    if scan_window:
        _mootdx_last_scan_at = time.monotonic()

    # Last resort: mootdx configured default.
    if time.monotonic() >= deadline:
        return None
    client = _try_mootdx_server(Quotes, None, deadline=deadline)
    if client is not None:
        _mootdx_discovered_server = None
        return client

    return None


def _parse_mootdx_server_list(raw: str, source: str) -> list[tuple[str, int]]:
    """解析逗号分隔的 ``ip:port`` 配置（裸 ip 默认 7709 端口）。

    非法端口项跳过并告警，不因一条脏配置丢掉整张列表。
    """
    servers: list[tuple[str, int]] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            ip, _, port = item.rpartition(":")
            try:
                servers.append((ip.strip(), int(port)))
            except ValueError:
                logger.warning(f"{source} 非法端口项: {item!r}")
        else:
            servers.append((item, 7709))
    return servers


def _curated_mootdx_servers() -> list[tuple[str, int]]:
    """候选镜像列表，按优先级排列（VEW-55 / VEW-62）。

    - ``settings.MOOTDX_SERVERS`` 非空时**完整覆盖**内置列表：应急止血用，代价是
      把主源收敛到配置的这几个镜像（单点），镜像再失效就没有备选。
    - 否则返回 ``MOOTDX_EXTRA_SERVERS`` + 内置 curated 列表：**追加**语义，运维可
      把实测可用的外部镜像补进池子，同时保留 curated 作为回退。公开镜像池会整体
      漂移（VEW-60 复测时 6 个 curated 全挂），追加路径让池子能持续扩充而不必
      收敛到单点。
    """
    raw = getattr(settings, "MOOTDX_SERVERS", "")
    if raw:
        servers = _parse_mootdx_server_list(raw, source="MOOTDX_SERVERS")
        if servers:
            return servers
        logger.warning("MOOTDX_SERVERS 配置为空结果，回退内置 curated 列表")
    servers = _parse_mootdx_server_list(
        getattr(settings, "MOOTDX_EXTRA_SERVERS", ""),
        source="MOOTDX_EXTRA_SERVERS",
    )
    servers.extend(_MOOTDX_SERVERS)
    return servers


def _mootdx_scan_candidates(
    hosts: Optional[list[tuple]] = None,
) -> list[tuple[str, int]]:
    """返回有界扫描用的公开 TDX 镜像候选。

    ``hosts`` 缺省时从 ``mootdx.consts.HQ_HOSTS``（三元组 ``(name, ip, port)``）
    加载；测试可传入固定样例列表，无需依赖 mootdx 安装。扫描规模受
    ``settings.MOOTDX_SCAN_LIMIT`` 限制（0 表示禁用扫描）。默认值已放开到覆盖
    整个内置池（VEW-62）：候选先经并发 TCP 可达性预筛，死镜像不再逐个吃满建连
    超时，一轮扫描的成本已降到秒级，游标轮转只是兜底。真正的「能取到 K 线」
    校验仍在 _try_mootdx_server 中完成。
    """
    global _mootdx_scan_cursor
    limit = int(getattr(settings, "MOOTDX_SCAN_LIMIT", 10))
    if limit <= 0:
        return []
    if hosts is None:
        try:
            from mootdx.consts import HQ_HOSTS  # 延迟导入，避免拖慢 import
        except Exception:  # pragma: no cover - 依赖缺失
            return []
        hosts = HQ_HOSTS
    # 去重（部分镜像名不同但 ip 相同）
    deduped: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for entry in hosts:
        if len(entry) >= 3:
            _name, ip, port = entry[0], entry[1], entry[2]
        elif len(entry) == 2:
            ip, port = entry[0], entry[1]
        else:
            continue
        h = (ip, int(port))
        if h not in seen:
            seen.add(h)
            deduped.append(h)
    if not deduped:
        return []
    start = _mootdx_scan_cursor % len(deduped)
    window = (deduped[start:] + deduped[:start])[:limit]
    _mootdx_scan_cursor = (start + limit) % len(deduped)
    return window


def _mootdx_scan_due() -> bool:
    """公开镜像扫描是否到期（上次扫描时间或冷却期已过）。"""
    if _mootdx_last_scan_at is None:
        return True
    cooldown = float(getattr(settings, "MOOTDX_SCAN_COOLDOWN", 1800))
    return time.monotonic() - _mootdx_last_scan_at >= cooldown


def _get_mootdx_client(deadline: Optional[float] = None):
    """Return the mootdx client, lazily (re)initializing it if needed.

    Returns ``None`` only if the handshake failed and the cooldown hasn't elapsed;
    a subsequent call after the cooldown retries. Never permanently wedges the
    primary source the way the old import-time init did.

    The mirror scan runs OUTSIDE the lock: holding ``_mootdx_client_lock`` across a
    scan (worst case many mirrors × seconds each) would block every concurrent
    request on that same lock, turning a slow scan into a 40s+ hang for all of them.
    Instead only the state check/flip happens under the lock, the scan runs
    lock-free, and a ``_mootdx_scan_in_progress`` guard makes concurrent callers
    fast-fail to ``None`` (secondary source) instead of queueing on the lock
    (VEW-54). The scan phase is separately capped at ``_MOOTDX_SCAN_BUDGET`` even
    when the caller passes a wider overall deadline, so a fully-dead mirror pool
    never eats the whole chain budget (评审 F3).
    """
    global _mootdx_client, _mootdx_init_failed_at, _mootdx_scan_in_progress
    if _mootdx_client is not None:
        return _mootdx_client

    now = time.monotonic()
    if _mootdx_init_failed_at is not None and (
        now - _mootdx_init_failed_at < _MOOTDX_RETRY_COOLDOWN
    ):
        return None

    with _mootdx_client_lock:
        if _mootdx_client is not None:
            return _mootdx_client
        # Re-check cooldown inside the lock so a failed attempt isn't retried
        # immediately by two requests racing on the first call.
        now = time.monotonic()
        if _mootdx_init_failed_at is not None and (
            now - _mootdx_init_failed_at < _MOOTDX_RETRY_COOLDOWN
        ):
            return None
        if _mootdx_scan_in_progress:
            # 已有请求在扫描镜像；不再排队等它扫完，本次直接快速返回 None 走备源。
            return None
        _mootdx_scan_in_progress = True

    # 扫描阶段单独兜底：整体 deadline 再宽，镜像探测自身也不超过 6s。
    scan_deadline = time.monotonic() + _MOOTDX_SCAN_BUDGET
    if deadline is not None:
        scan_deadline = min(scan_deadline, deadline)

    client = None
    try:
        client = _init_mootdx_client(deadline=scan_deadline)
    finally:
        # 单次持锁完成「清扫描标志 + 记录结果」：避免两段锁区间之间并发线程在
        # 标志已清、冷却未记录时再触发一次冗余扫描（评审 F2）。
        with _mootdx_client_lock:
            _mootdx_scan_in_progress = False
            if client is None:
                _mootdx_init_failed_at = time.monotonic()
            else:
                _mootdx_client = client
                _mootdx_init_failed_at = None
    return client


def _invalidate_mootdx_client(client: Any) -> bool:
    """Discard a cached client that failed after it had been initialized.

    The lazy initializer only repairs startup failures.  A public TDX mirror can
    become stale later and keep returning empty responses forever; without
    clearing the cached object every request continues using that dead mirror.
    Identity-checking under the same lock prevents an older failing request from
    discarding a client another thread has already replaced.

    Returns ``True`` when ``client`` was still current and was invalidated.
    A cooldown is recorded so concurrent requests fall through to the secondary
    source instead of all starting an expensive mirror scan at once.
    """

    global _mootdx_client, _mootdx_init_failed_at
    with _mootdx_client_lock:
        if _mootdx_client is not client:
            return False
        _mootdx_client = None
        _mootdx_init_failed_at = time.monotonic()
        return True


# Per-attempt sleep multiplier
_RETRY_SLEEP = 0.6


def _norm_request_date(value: str | None) -> str | None:
    """把请求日期统一归一化为 YYYY-MM-DD（入参可能是 YYYYMMDD 或 YYYY-MM-DD）。"""
    if not value:
        return None
    try:
        return str(pd.Timestamp(str(value)).normalize())[:10]
    except Exception:
        return str(value)


def _coverage_gap(
    req_start: str | None,
    req_end: str | None,
    actual_start: str | None,
    actual_end: str | None,
) -> bool:
    """判断实际范围是否覆盖请求范围：起点滞后或终点提前即为覆盖缺口。"""
    if not actual_start or not actual_end:
        return True
    gap = False
    if req_start:
        if pd.Timestamp(actual_start).normalize() > pd.Timestamp(req_start).normalize():
            gap = True
    if req_end:
        if pd.Timestamp(actual_end).normalize() < pd.Timestamp(req_end).normalize():
            gap = True
    return gap


# mootdx frequency mapping: API period str → mootdx frequency int
# From mootdx.consts: KLINE_1MIN=8, KLINE_5MIN=0, KLINE_15MIN=1,
# KLINE_30MIN=2, KLINE_1HOUR=3, KLINE_DAILY=4
_FREQ_MAP = {
    "1": 8,  # KLINE_1MIN
    "5": 0,  # KLINE_5MIN
    "15": 1,  # KLINE_15MIN
    "30": 2,  # KLINE_30MIN
    "60": 3,  # KLINE_1HOUR
    "101": 4,  # KLINE_DAILY
}


class AStockDataProvider(MarketDataProvider):
    """Data provider using mootdx TCP (primary) with Eastmoney/Tushare fallbacks.

    K-line data comes from mootdx → 通达信 servers via TCP, avoiding
    IP-level blocks that affect Eastmoney HTTP endpoints.
    """

    # ------------------------------------------------------------------
    # mootdx K-line (primary source)
    # ------------------------------------------------------------------

    def _fetch_kline_mootdx(
        self,
        stock_code: str,
        period: str,
        start_date: str,
        end_date: str,
        count: int = 500,
        start_offset: int = 0,
        deadline: Optional[float] = None,
    ) -> Optional[pd.DataFrame]:
        """Fetch K-line data via mootdx TCP (通达信).

        Args:
            count: Number of bars to fetch (max 800 per request, capped).
            start_offset: Skip the first N most-recent bars. Used by the
                          frontend for dynamic scroll-based loading.
            deadline: Absolute ``time.monotonic()`` timestamp bounding the whole
                      fetch; ``None`` (default) means no budget on the data path
                      (per-op socket timeout still applies). Only the minute
                      chain passes one explicitly (``_MOOTDX_MINUTE_BUDGET``) so a
                      slow mirror cannot hang it beyond the frontend timeout;
                      the daily/CYQ paths stay unbounded and are never silently
                      truncated mid-pagination (VEW-54, 评审 F1). Budget
                      exhaustion returns whatever was collected (or ``None``), it
                      does NOT invalidate a healthy cached client. Waiting for the
                      shared-client fetch lock also counts against the budget
                      (VEW-60 评审 M4).
        """
        client = _get_mootdx_client(deadline=deadline)
        if client is None:
            return None

        freq = _FREQ_MAP.get(period)
        if freq is None:
            return None

        try:
            cols = ["open", "close", "high", "low", "volume", "amount"]
            # mootdx 单次最多 800；count>800 时按 start_offset 逐页向前翻，避免长区间静默截断。
            wanted = max(int(count or 500), 1)
            page_size = min(wanted, 800)
            if page_size <= 0:
                return None

            frames: list[pd.DataFrame] = []
            collected = 0
            initial_offset = int(start_offset or 0)
            offset = initial_offset
            # 共享 client 的整段翻页都持取数锁：TDX 一问一答，并发调用会让响应错位、
            # 静默返回空（见 _mootdx_fetch_lock 注释）。锁只覆盖网络取数，随后的
            # 本地清洗/排序在锁外进行。
            # 等锁按调用方剩余预算设上限（VEW-60 评审 M4）：分钟链路给了 deadline，
            # 若在这里无界等待，「等锁 + 取数」合计会把 _MOOTDX_MINUTE_BUDGET 架空，
            # 请求仍可能挂到前端 15s 超时。等不到锁即放弃本次取数、交给备源。
            # 无 deadline 的日线 / CYQ 路径保持无限期等待（VEW-54 的既有取舍）。
            lock_timeout = (
                None if deadline is None else max(deadline - time.monotonic(), 0.0)
            )
            with _mootdx_fetch_guard(lock_timeout) as acquired:
                if not acquired:
                    logger.warning(
                        f"mootdx K线取数 {stock_code} 等待取数锁超预算, 放弃本次取数"
                    )
                    return None
                while collected < wanted:
                    if deadline is not None and time.monotonic() >= deadline:
                        logger.warning(
                            f"mootdx K线取数 {stock_code} 超过取数预算, 提前返回"
                        )
                        break
                    klines = client.bars(
                        symbol=stock_code,
                        frequency=freq,
                        start=offset,
                        offset=page_size,
                    )
                    if klines is None or klines.empty:
                        # An empty first page at offset=0 can mean either a dead mirror
                        # or a symbol with no data.  Confirm with the known-liquid probe
                        # symbol before invalidating; pagination exhaustion and
                        # post-fetch date filtering remain normal empty results.
                        if collected == 0 and initial_offset == 0:
                            probe_symbol = _mootdx_probe_symbol()
                            mirror_empty = str(stock_code).zfill(6) == probe_symbol
                            if not mirror_empty:
                                if (
                                    deadline is not None
                                    and time.monotonic() >= deadline
                                ):
                                    # 预算耗尽无法确认是否镜像空：按普通空结果处理，
                                    # 不摘除缓存客户端。
                                    break
                                probe = client.bars(
                                    symbol=probe_symbol,
                                    frequency=freq,
                                    start=0,
                                    offset=3,
                                )
                                mirror_empty = probe is None or probe.empty
                            if mirror_empty:
                                logger.warning(
                                    "mootdx 镜像 freq=%s 原始K线返回空，摘除缓存客户端",
                                    freq,
                                )
                                _invalidate_mootdx_client(client)
                        break
                    frames.append(klines)
                    n = len(klines)
                    collected += n
                    offset += n
                    if n < page_size:
                        break

            if not frames:
                return None

            df = pd.concat(frames, ignore_index=True)
            if "datetime" in df.columns:
                df = df.reset_index(drop=True)
            else:
                df = df.reset_index()

            if "index" in df.columns and "datetime" not in df.columns:
                df = df.rename(columns={"index": "datetime"})

            if "datetime" not in df.columns:
                logger.warning(f"mootdx 缺少datetime列 {stock_code}")
                return None

            # 分页交界可能重叠，按 datetime 去重后再排序
            df = df.drop_duplicates(subset=["datetime"], keep="last")
            df = df.sort_values("datetime")
            df["datetime"] = pd.to_datetime(df["datetime"])

            if start_date:
                df = df[df["datetime"] >= pd.Timestamp(start_date)]
            if end_date:
                # 上界是**裸日期**（无时间分量）时放宽到当日结束再比较：日线 bar 的
                # 时间戳是当日 15:00:00，直接用 ``<= 当日 00:00`` 会把结束日整根截掉，
                # 既少一根 bar，也让 _coverage_gap 把「其实覆盖到了」误判成 gap=True
                # （VEW-60）。带时间的上界（分钟链路传 "YYYY-MM-DD 16:00:00"）保持
                # 精确比较 —— 不做对称 normalize，否则会把分钟路径的时间边界悄悄放宽。
                upper = pd.Timestamp(end_date)
                if upper == upper.normalize():
                    # 用 ``Timedelta(1, unit="D")`` 而非 ``Timedelta(days=1)``：后者在
                    # numpy>=2 下走裸整数转换，会触发 'generic' unit 弃用告警。
                    df = df[df["datetime"] < upper + pd.Timedelta(1, unit="D")]
                else:
                    df = df[df["datetime"] <= upper]

            if df.empty:
                return None

            df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
            available = ["datetime"] + [c for c in cols if c in df.columns]
            out = df[available]
            # mootdx（pytdx get_security_bars）返回非复权原始行情，如实标注。
            # 调用方请求 qfq/hfq 时据此判定降级（VEW-55）。
            out.attrs["adjust_served"] = ""
            out.attrs["adjust_degraded"] = False
            return out
        except Exception as e:
            logger.warning(f"mootdx K线请求失败 {stock_code}: {e}")
            _invalidate_mootdx_client(client)
            return None

    # ------------------------------------------------------------------
    # Daily data
    # ------------------------------------------------------------------

    def fetch_daily_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        max_retries: int = 2,
        count: int = 500,
        start_offset: int = 0,
    ) -> Optional[pd.DataFrame]:
        # 保持旧签名兼容；带 provenance 的实现见 fetch_daily_data_with_meta
        return self.fetch_daily_data_with_meta(
            stock_code,
            start_date,
            end_date,
            adjust=adjust,
            max_retries=max_retries,
            count=count,
            start_offset=start_offset,
        ).df

    def fetch_daily_data_with_meta(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        max_retries: int = 2,
        count: int = 500,
        start_offset: int = 0,
    ) -> DailyDataResult:
        """获取日线数据并返回结构化 provenance（source / 复权 / 请求实际范围 / gap / 失败原因）。

        0. 请求入参可能是 YYYYMMDD（provider 层约定），这里统一归一化为 YYYY-MM-DD。
        1. 先试 mootdx（TCP，无 IP 封锁），count>800 时分页取全。
        2. 回退 Eastmoney HTTP。
        3. 回退 Tushare（仅日线）。
        全部失败时 df=None 并给出 failure_reason。
        """
        req_start = _norm_request_date(start_date)
        req_end = _norm_request_date(end_date)
        provenance = DataProvenance(
            source=None,
            adjustment=adjust,
            requested_start=req_start,
            requested_end=req_end,
        )

        # 1. Try mootdx first (TCP, no IP block)
        df = self._fetch_kline_mootdx(
            stock_code,
            period="101",
            start_date=req_start,
            end_date=req_end,
            count=count,
            start_offset=start_offset,
        )
        if df is not None and not df.empty:
            logger.info(f"股票 {stock_code} 日线由 mootdx 返回")
            provenance.source = "mootdx"
            # mootdx 只服务非复权原始行情：请求 qfq/hfq 即视为降级（VEW-55）
            served = str(df.attrs.get("adjust_served", ""))
            provenance.adjustment = served
            provenance.degraded = bool(adjust) and served != adjust
            self._fill_provenance(provenance, df, req_start, req_end, served)
            return DailyDataResult(df=df, provenance=provenance)

        # 2. Fallback: Eastmoney → Tushare
        #
        # 东财失败快速熔断（VEW-60）：source_monitor 的探针已经能把东财判为 down，
        # 但取数链此前不消费该信号，逐标的仍撞 max_retries+1 次重试（退避
        # _RETRY_SLEEP×attempt ≈ 1.8s 纯等待），实测单标的 2.96s 里约 2/3 耗在注定
        # 失败的重试上。已知 down 时直接跳过东财、落到 Tushare。恢复由源级探针负责
        # （每轮真实请求一次，成功即翻回 up），不是永久摘除。
        if source_monitor.is_down("eastmoney"):
            logger.info(
                f"股票 {stock_code} 东财已知不可用(source_monitor=down), 跳过重试"
            )
        else:
            fqt = fqt_code(adjust)
            for attempt in range(1, max_retries + 2):
                try:
                    df = eastmoney_kline(
                        code=stock_code,
                        klt="101",
                        beg=start_date or "",
                        end=end_date or "",
                        fqt=fqt,
                    )
                    if df is not None and not df.empty:
                        provenance.source = "eastmoney"
                        served = str(df.attrs.get("adjust_served", adjust))
                        provenance.adjustment = served
                        provenance.degraded = bool(adjust) and served != adjust
                        self._fill_provenance(
                            provenance, df, req_start, req_end, served
                        )
                        return DailyDataResult(df=df, provenance=provenance)
                except Exception as e:
                    logger.warning(
                        f"获取股票 {stock_code} 日线数据失败(第{attempt}次): {e}"
                    )
                if attempt <= max_retries:
                    time.sleep(_RETRY_SLEEP * attempt)
                    continue
                logger.warning(
                    f"股票 {stock_code} Eastmoney 日线重试耗尽，回退 Tushare"
                )
                break

        # 3. Tushare
        df = self._fetch_daily_tushare(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            max_retries=settings.TUSHARE_RETRY_TIMES,
        )
        if df is not None and not df.empty:
            provenance.source = "tushare"
            served = str(df.attrs.get("adjust_served", adjust))
            provenance.adjustment = served
            provenance.degraded = bool(df.attrs.get("adjust_degraded", False))
            provenance.adjust_factor_date = (
                str(df.attrs.get("adjust_factor_date", "")) or None
            )
            self._fill_provenance(provenance, df, req_start, req_end, served)
            return DailyDataResult(df=df, provenance=provenance)

        provenance.failure_reason = "全部数据源无数据或失败"
        return DailyDataResult(df=None, provenance=provenance)

    @staticmethod
    def _fill_provenance(
        provenance: DataProvenance,
        df: pd.DataFrame,
        req_start: Optional[str],
        req_end: Optional[str],
        adjust: str,
    ) -> None:
        """从实际返回的 df 填充 provenance 的范围、bar 数、覆盖缺口。"""
        if df is None or df.empty or "datetime" not in df.columns:
            provenance.bar_count = 0
            provenance.gap = True
            return
        dates = pd.to_datetime(df["datetime"])
        actual_start = str(dates.min())[:10]
        actual_end = str(dates.max())[:10]
        provenance.actual_start = actual_start
        provenance.actual_end = actual_end
        provenance.bar_count = int(len(df))
        provenance.last_bar = actual_end
        provenance.adjustment = adjust
        provenance.gap = _coverage_gap(req_start, req_end, actual_start, actual_end)

    # ------------------------------------------------------------------
    # Tushare fallback (carried over from AKShareProvider)
    # ------------------------------------------------------------------

    @staticmethod
    def _to_tushare_code(stock_code: str) -> str:
        code = str(stock_code).zfill(6)
        if code.startswith(("5", "6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    def _fetch_daily_tushare(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str,
        max_retries: int,
    ) -> Optional[pd.DataFrame]:
        if not settings.TUSHARE_ENABLED:
            return None
        if not settings.TUSHARE_TOKEN:
            logger.warning("Tushare 未配置 token，跳过备源")
            return None
        if ts is None:
            logger.warning("tushare 依赖未安装，跳过备源")
            return None

        ts_code = self._to_tushare_code(stock_code)
        adj = adjust if adjust in {"qfq", "hfq"} else None

        for attempt in range(1, max_retries + 2):
            try:
                ts.set_token(settings.TUSHARE_TOKEN)
                # 始终先取非复权：pro_bar(adj=...) 对 qfq/hfq 会内部调用 adj_factor，
                # 而该接口配额极低（VEW-55）。复权在本地用缓存的 adj_factor 完成，
                # 配额耗尽时降级为非复权而不是让整次请求失败。
                df = ts.pro_bar(
                    ts_code=ts_code,
                    adj=None,
                    start_date=start_date,
                    end_date=end_date,
                )
                if df is None or df.empty:
                    return None

                actual_adjust = ""
                degraded = False
                factor_date = None
                if adj is not None:
                    adjusted = self._apply_tushare_adjust(ts_code, df, adj)
                    if adjusted is not None:
                        df = adjusted
                        actual_adjust = adj
                        factor_date = (
                            str(adjusted.attrs.get("adjust_factor_date", "")) or None
                        )
                    else:
                        degraded = True
                        logger.warning(
                            f"Tushare adj_factor 不可用，{stock_code} 降级为非复权"
                        )

                normalized = pd.DataFrame(
                    {
                        "日期": pd.to_datetime(df["trade_date"], format="%Y%m%d"),
                        "开盘": df["open"],
                        "收盘": df["close"],
                        "最高": df["high"],
                        "最低": df["low"],
                        "成交量": df["vol"],
                        "成交额": df.get("amount"),
                    }
                )
                normalized = normalized.sort_values("日期").reset_index(drop=True)
                normalized["日期"] = normalized["日期"].dt.strftime("%Y-%m-%d 00:00:00")
                # 记录实际复权口径与降级标记，供 fetch_daily_data_with_meta 写入 provenance
                normalized.attrs["adjust_served"] = actual_adjust
                normalized.attrs["adjust_degraded"] = degraded
                normalized.attrs["adjust_factor_date"] = factor_date
                logger.info(f"股票 {stock_code} 日线数据由 Tushare 备源返回")
                return normalized.rename(
                    columns={
                        "日期": "datetime",
                        "开盘": "open",
                        "收盘": "close",
                        "最高": "high",
                        "最低": "low",
                        "成交量": "volume",
                        "成交额": "amount",
                    }
                )
            except Exception as e:
                if attempt <= max_retries:
                    logger.warning(
                        f"Tushare 获取股票 {stock_code} 日线失败(第{attempt}次重试): {e}"
                    )
                    time.sleep(0.8 * attempt)
                    continue
                logger.error(f"Tushare 获取股票 {stock_code} 日线失败: {e}")
                return None
        return None

    def _apply_tushare_adjust(
        self, ts_code: str, df: pd.DataFrame, adjust: str
    ) -> Optional[pd.DataFrame]:
        """用缓存的 adj_factor 对非复权日线做 qfq/hfq 复权。

        adj_factor 由 tushare_adj.get_adj_factor 统一管理（缓存 + 配额守卫 +
        新鲜度刷新）；不可用（配额耗尽 / 未缓存 / 拉取失败）时返回 None，由调用方
        降级为非复权。返回的 df 携带 adjust_factor_date 供调用方透传缓存日期。
        """
        from app.providers.tushare_adj import (
            adj_factor_store,
            apply_adjust,
            get_adj_factor,
        )

        adj = get_adj_factor(ts_code)
        if adj is None or adj.empty:
            return None
        out = apply_adjust(df, adj, adjust)
        if out is not None:
            out.attrs["adjust_factor_date"] = adj_factor_store.cache_date(ts_code)
        return out

    # ------------------------------------------------------------------
    # Minute data
    # ------------------------------------------------------------------

    def fetch_minute_data(
        self,
        stock_code: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "1",
        adjust: str = "",
        max_retries: int = 2,
        count: int = 500,
        start_offset: int = 0,
        deadline: Optional[float] = None,
    ) -> Optional[pd.DataFrame]:
        # 分钟链路整体墙钟预算：mootdx 探测/取数 + 东财回退合计计入，须小于前端
        # 15s 超时。超时即放弃本次取数、返回空 K 线（前端立即显示降级提示），而不
        # 是让请求在服务端挂起 40s+ 直到前端超时（VEW-54）。
        if deadline is None:
            deadline = time.monotonic() + _MOOTDX_MINUTE_BUDGET
        if time.monotonic() >= deadline:
            logger.warning(f"分钟数据请求 {stock_code} 已超预算, 快速返回空")
            return None

        # 1. Try mootdx first (TCP, supports all periods including 1min)
        df = self._fetch_kline_mootdx(
            stock_code,
            period=period,
            start_date=start_datetime,
            end_date=end_datetime,
            count=count,
            start_offset=start_offset,
            deadline=deadline,
        )
        if df is not None and not df.empty:
            logger.info(
                f"股票 {stock_code} period={period} 由 mootdx 返回 (共{len(df)}行)"
            )
            return df

        # 2. Fallback: Eastmoney HTTP
        for attempt in range(1, max_retries + 2):
            if time.monotonic() >= deadline:
                logger.warning(
                    f"分钟数据请求 {stock_code} 在回退阶段超预算, 放弃本次取数"
                )
                return None
            try:
                if period == "1":
                    df = eastmoney_trends2(code=stock_code)
                else:
                    fqt = fqt_code(adjust)
                    df = eastmoney_kline(
                        code=stock_code,
                        klt=period,
                        beg="",
                        end="20500101",
                        fqt=fqt,
                    )

                if df is not None and not df.empty:
                    # 东财分钟线按 fqt 复权（1 分钟走 trends2 接口，无复权参数）。
                    served = adjust if period != "1" else ""
                    df.attrs["adjust_served"] = served
                    df.attrs["adjust_degraded"] = bool(adjust) and served != adjust
                    # Filter to requested datetime range
                    if "datetime" in df.columns:
                        df["datetime"] = pd.to_datetime(df["datetime"])
                        mask = (df["datetime"] >= pd.Timestamp(start_datetime)) & (
                            df["datetime"] <= pd.Timestamp(end_datetime)
                        )
                        df = df[mask]
                        df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")

                    if not df.empty:
                        return df

            except Exception as e:
                logger.warning(
                    f"获取股票 {stock_code} 分钟数据失败(第{attempt}次): {e}"
                )

            if attempt <= max_retries:
                time.sleep(_RETRY_SLEEP * attempt)
                continue
            logger.warning(
                f"股票 {stock_code} 在 {start_datetime}-{end_datetime} 期间无分钟数据"
            )
            return None

    # ------------------------------------------------------------------
    # Real-time data
    # ------------------------------------------------------------------

    def fetch_realtime_data(self) -> Optional[pd.DataFrame]:
        try:
            df = eastmoney_all_stocks()
            if df is None or df.empty:
                logger.warning("获取实时行情数据为空")
                return None
            return df
        except Exception as e:
            logger.error(f"获取实时行情数据失败: {e}")
            return None

    # ------------------------------------------------------------------
    # CYQ data
    # ------------------------------------------------------------------

    def _compute_cyq_locally(self, stock_code: str) -> Optional[dict[str, Any]]:
        """Compute chip distribution from mootdx daily klines.

        Uses volume-weighted price distribution with exponential decay
        (newer bars weighted more heavily) to approximate where current
        shareholders acquired their shares.
        """
        import numpy as np

        df = self._fetch_kline_mootdx(
            stock_code,
            period="101",
            start_date="",
            end_date="",
            count=210,
        )
        if df is None or df.empty:
            return None

        df = df.sort_values("datetime")
        prices = (df["high"] + df["low"] + df["close"]) / 3.0
        volumes = df["volume"].values
        n = len(df)

        if n < 10 or volumes.sum() <= 0:
            return None

        # Build cost distribution: 200 price bins
        price_min = df["low"].min()
        price_max = df["high"].max()
        if price_max <= price_min:
            price_max = price_min + 0.01
        bins = 200
        bin_size = (price_max - price_min) / bins
        dist = np.zeros(bins)

        for i in range(n):
            vol = volumes[i]
            if vol <= 0:
                continue
            # Exponential decay: older bars have less weight
            decay = np.exp(-0.5 * (n - 1 - i) / max(n - 1, 1))
            weight = vol * decay
            low = df.iloc[i]["low"]
            high_val = df.iloc[i]["high"]
            bar_range = high_val - low
            if bar_range <= 0:
                b = int((low - price_min) / bin_size)
                b = max(0, min(b, bins - 1))
                dist[b] += weight
            else:
                vol_per_unit = weight / bar_range
                low_b = int((low - price_min) / bin_size)
                high_b = int((high_val - price_min) / bin_size)
                low_b = max(0, min(low_b, bins - 1))
                high_b = max(0, min(high_b, bins - 1))
                for b in range(low_b, high_b + 1):
                    bin_low = price_min + b * bin_size
                    bin_high = bin_low + bin_size
                    overlap = max(0.0, min(high_val, bin_high) - max(low, bin_low))
                    dist[b] += vol_per_unit * overlap

        total = dist.sum()
        if total <= 0:
            return None

        # Average cost
        bin_centers = np.array([price_min + (i + 0.5) * bin_size for i in range(bins)])
        avg_cost = float(np.sum(bin_centers * dist) / total)

        # Current price (last close)
        current_price = float(df.iloc[-1]["close"])

        # Profit ratio: percentage of distribution below current price
        below = dist[bin_centers <= current_price].sum()
        profit_ratio = float(below / total)

        # 90% and 70% cost ranges via cumulative distribution
        cumsum = 0.0
        # Sort bin centers by distance from distribution peak
        peak_idx = int(np.argmax(dist))
        low_idx = peak_idx
        high_idx = peak_idx
        cumsum = dist[peak_idx]

        target_90 = total * 0.90
        while cumsum < target_90:
            can_low = low_idx > 0
            can_high = high_idx < bins - 1
            if not can_low and not can_high:
                break
            if can_low and can_high:
                if dist[low_idx - 1] >= dist[high_idx + 1]:
                    low_idx -= 1
                else:
                    high_idx += 1
            elif can_low:
                low_idx -= 1
            else:
                high_idx += 1
            cumsum += dist[low_idx if can_low else high_idx]
        cost_90_low = float(bin_centers[low_idx])
        cost_90_high = float(bin_centers[high_idx])
        concentration_90 = (
            float((cost_90_high - cost_90_low) / avg_cost) if avg_cost > 0 else 0
        )

        # 70% range
        low_idx = peak_idx
        high_idx = peak_idx
        cumsum = dist[peak_idx]
        target_70 = total * 0.70
        while cumsum < target_70:
            can_low = low_idx > 0
            can_high = high_idx < bins - 1
            if not can_low and not can_high:
                break
            if can_low and can_high:
                if dist[low_idx - 1] >= dist[high_idx + 1]:
                    low_idx -= 1
                else:
                    high_idx += 1
            elif can_low:
                low_idx -= 1
            else:
                high_idx += 1
            cumsum += dist[low_idx if can_low else high_idx]
        cost_70_low = float(bin_centers[low_idx])
        cost_70_high = float(bin_centers[high_idx])
        concentration_70 = (
            float((cost_70_high - cost_70_low) / avg_cost) if avg_cost > 0 else 0
        )

        return {
            "date": str(df.iloc[-1]["datetime"]),
            "profit_ratio": profit_ratio,
            "avg_cost": round(avg_cost, 2),
            "cost_90_low": round(cost_90_low, 2),
            "cost_90_high": round(cost_90_high, 2),
            "concentration_90": round(concentration_90, 4),
            "cost_70_low": round(cost_70_low, 2),
            "cost_70_high": round(cost_70_high, 2),
            "concentration_70": round(concentration_70, 4),
        }

    def fetch_cyq_data(
        self, stock_code: str, adjust: str = ""
    ) -> Optional[pd.DataFrame]:
        fqt = fqt_code(adjust)
        try:
            df = eastmoney_cyq(code=stock_code, fqt=fqt)
            if df is None or df.empty:
                raise Exception("Eastmoney CYQ returned empty")
            return df
        except Exception as e:
            logger.info(f"Eastmoney CYQ不可用，回退mootdx本地计算 {stock_code}: {e}")

        # Local computation via mootdx
        local = self._compute_cyq_locally(stock_code)
        if local is None:
            return None
        df = pd.DataFrame([local])
        # Add required column names for normalize_cyq_data compatibility
        df["获利比例"] = df["profit_ratio"]
        df["平均成本"] = df["avg_cost"]
        df["90成本-低"] = df["cost_90_low"]
        df["90成本-高"] = df["cost_90_high"]
        df["90集中度"] = df["concentration_90"]
        df["70成本-低"] = df["cost_70_low"]
        df["70成本-高"] = df["cost_70_high"]
        df["70集中度"] = df["concentration_70"]
        return df

    def normalize_cyq_data(self, df: pd.DataFrame) -> dict[str, Any]:
        latest = df.iloc[-1]
        return {
            "date": str(latest.get("日期", latest.get("date", ""))),
            "profit_ratio": float(
                latest.get("获利比例", latest.get("profit_ratio", 0))
            ),
            "avg_cost": float(latest.get("平均成本", latest.get("avg_cost", 0))),
            "cost_90_low": float(latest.get("90成本-低", latest.get("cost_90_low", 0))),
            "cost_90_high": float(
                latest.get("90成本-高", latest.get("cost_90_high", 0))
            ),
            "concentration_90": float(
                latest.get("90集中度", latest.get("concentration_90", 0))
            ),
            "cost_70_low": float(latest.get("70成本-低", latest.get("cost_70_low", 0))),
            "cost_70_high": float(
                latest.get("70成本-高", latest.get("cost_70_high", 0))
            ),
            "concentration_70": float(
                latest.get("70集中度", latest.get("concentration_70", 0))
            ),
        }
