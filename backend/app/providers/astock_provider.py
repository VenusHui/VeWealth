"""AStockDataProvider — implements MarketDataProvider via a-stock-data patterns.

Primary K-line source: mootdx (TCP, no IP block).
Fallback chain: Eastmoney HTTP → Tushare (daily only).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import pandas as pd

from app.core.config import settings
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
    ("110.41.147.114", 7709),  # 深圳双线主站1
    ("110.41.154.219", 7709),  # 深圳双线主站6
    ("124.70.176.52", 7709),  # 上海双线主站1
    ("47.100.236.28", 7709),  # 上海双线主站2
    ("121.36.54.217", 7709),  # 北京双线主站1
    ("124.71.85.110", 7709),  # 广州双线主站1
]

# 建连超时（秒）。选中的客户端沿用该超时用于后续取数，故定成常量便于调整。
_MOOTDX_CONNECT_TIMEOUT = 5

# 镜像探测/单次取数的墙钟预算（秒）。镜像池全挂时 _init_mootdx_client 会串行
# 探测 curated + 扫描候选 + 配置默认，每个镜像含建连 + 2 次探针取数（各自受
# _MOOTDX_CONNECT_TIMEOUT 兜底），最坏可把请求挂起数十秒、超过前端 15s 超时。
# 给探测与取数各设硬预算，超时即放弃，让分钟链路「快速返回空 K 线」、前端立即
# 显示降级提示（VEW-54）。
_MOOTDX_SCAN_BUDGET = 6.0
_MOOTDX_FETCH_BUDGET = 8.0
# 分钟链路整体预算（秒）：mootdx 探测/取数 + 东财回退合计计入，须小于前端 15s
# 超时。超时放弃本次取数，返回空而非让请求挂起（VEW-54）。
_MOOTDX_MINUTE_BUDGET = 12.0

# 探针校验的取数周期：深度图默认 5 分钟（frequency=0），日线（frequency=4）作
# 备用。两者都必须能取到才认为镜像可用 —— 只握手、部分周期空回来的镜像不能选。
_MOOTDX_PROBE_FREQUENCIES: tuple[int, ...] = (4, 0)


def _mootdx_probe_symbol() -> str:
    """Return the known-liquid symbol used to distinguish mirror vs symbol gaps."""

    return str(getattr(settings, "SOURCE_HEALTH_PROBE_SYMBOL", "000001")).zfill(6)


def _try_mootdx_server(
    Quotes, server: Optional[tuple[str, int]], deadline: Optional[float] = None
):
    """Build a client for one TDX mirror and confirm it returns K-lines.

    ``server`` of ``None`` means "let mootdx use its configured default" (a bare
    ``Quotes.factory``). We probe the two periods the depth chart actually uses —
    daily (frequency=4) and 5-minute (frequency=0) — and accept the mirror only if
    both return bars, otherwise ``None``. A mirror that handshakes but serves one
    period empty can still leave part of the UI blank (VEW-36).

    ``deadline`` is an absolute ``time.monotonic()`` timestamp bounding this probe;
    once reached the probe gives up (``None``) so a slow mirror cannot eat the whole
    request budget (VEW-54).
    """
    if deadline is not None and time.monotonic() >= deadline:
        return None
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

    for freq in _MOOTDX_PROBE_FREQUENCIES:
        if deadline is not None and time.monotonic() >= deadline:
            return None
        try:
            probe = client.bars(
                symbol=_mootdx_probe_symbol(), frequency=freq, start=0, offset=3
            )
        except Exception as e:  # pragma: no cover - 防御性
            logger.warning(
                f"mootdx 通过 {server or '配置默认'} 拉取 freq={freq} K线失败: {e}"
            )
            return None
        if probe is None or probe.empty:
            logger.warning(
                f"mootdx 镜像 {server or '配置默认'} freq={freq} 未返回有效K线, 跳过"
            )
            return None

    logger.info(f"mootdx 通过 {server or '配置默认'} 取得K线(日线+5分钟), 使用该镜像")
    return client


def _init_mootdx_client(deadline: Optional[float] = None):
    """Create a mootdx client connected to a mirror that returns real data.

    ``Quotes.factory(market="std")`` without ``bestip`` just reuses whatever mirror
    is recorded in the local mootdx config, and that mirror may handshake but serve
    no K-line body. Here we probe candidates in order and keep the first one that
    returns bars (VEW-36):

    1. ``settings.MOOTDX_SERVERS`` override, else the curated default list;
    2. the mirror discovered by the last public scan (fast reuse);
    3. a bounded scan over the mirror list bundled with mootdx when the curated
       set has gone stale (VEW-55);
    4. mootdx's configured default.

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

    # Fast candidates first: settings override / curated list, then the last
    # mirror a scan discovered.  Each is probed with a real K-line fetch.
    candidates: list[Optional[tuple[str, int]]] = _curated_mootdx_servers()
    if _mootdx_discovered_server and _mootdx_discovered_server not in candidates:
        candidates.append(_mootdx_discovered_server)

    for server in candidates:
        if time.monotonic() >= deadline:
            break
        client = _try_mootdx_server(Quotes, server, deadline=deadline)
        if client is not None:
            _mootdx_discovered_server = server
            return client

    # Curated set exhausted: run a bounded scan over the public HQ list. Gated by
    # a cooldown so a dead mirror pool isn't re-scanned on every request (VEW-55).
    if _mootdx_scan_due():
        for server in _mootdx_scan_candidates():
            if time.monotonic() >= deadline:
                break
            client = _try_mootdx_server(Quotes, server, deadline=deadline)
            if client is not None:
                _mootdx_discovered_server = server
                _mootdx_last_scan_at = time.monotonic()
                return client
        _mootdx_last_scan_at = time.monotonic()

    # Last resort: mootdx configured default.
    if time.monotonic() >= deadline:
        return None
    client = _try_mootdx_server(Quotes, None, deadline=deadline)
    if client is not None:
        _mootdx_discovered_server = None
        return client

    return None


def _curated_mootdx_servers() -> list[tuple[str, int]]:
    """候选镜像列表：``settings.MOOTDX_SERVERS`` 优先，否则内置 curated 列表。

    运维可把失效镜像替换为逗号分隔的 ``ip:port``（裸 ip 默认 7709 端口），
    避免每次镜像失效都要改代码发版。非空配置会完整覆盖内置列表。
    """
    raw = getattr(settings, "MOOTDX_SERVERS", "")
    if raw:
        servers: list[tuple[str, int]] = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                ip, _, port = item.rpartition(":")
                try:
                    servers.append((ip.strip(), int(port)))
                except ValueError:
                    logger.warning(f"MOOTDX_SERVERS 非法端口项: {item!r}")
            else:
                servers.append((item, 7709))
        if servers:
            return servers
        logger.warning("MOOTDX_SERVERS 配置为空结果，回退内置 curated 列表")
    return list(_MOOTDX_SERVERS)


def _mootdx_scan_candidates(
    hosts: Optional[list[tuple]] = None,
) -> list[tuple[str, int]]:
    """返回有界扫描用的公开 TDX 镜像候选。

    ``hosts`` 缺省时从 ``mootdx.consts.HQ_HOSTS``（三元组 ``(name, ip, port)``）
    加载；测试可传入固定样例列表，无需依赖 mootdx 安装。扫描规模受
    ``settings.MOOTDX_SCAN_LIMIT`` 限制（0 表示禁用扫描），避免死镜像池拖慢 init。
    游标每次推进 limit，多轮扫描（间隔冷却期）能覆盖完整列表；真正的「能取到
    K 线」校验仍在 _try_mootdx_server 中完成。
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
    (VEW-54). ``deadline`` is passed through to the scan so it gives up once the
    budget is exhausted.
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

    try:
        client = _init_mootdx_client(deadline=deadline)
    finally:
        with _mootdx_client_lock:
            _mootdx_scan_in_progress = False

    with _mootdx_client_lock:
        if client is None:
            _mootdx_init_failed_at = time.monotonic()
            return None
        _mootdx_client = client
        _mootdx_init_failed_at = None
        return _mootdx_client


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
                      fetch (default: ``now + _MOOTDX_FETCH_BUDGET``). Checked
                      between pagination pages / the empty-mirror probe so a slow
                      mirror cannot hang the request beyond the frontend timeout
                      (VEW-54). Budget exhaustion returns whatever was collected
                      (or ``None``), it does NOT invalidate a healthy cached client.
        """
        if deadline is None:
            deadline = time.monotonic() + _MOOTDX_FETCH_BUDGET
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
            while collected < wanted:
                if time.monotonic() >= deadline:
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
                            if time.monotonic() >= deadline:
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
                df = df[df["datetime"] <= pd.Timestamp(end_date)]

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
                    self._fill_provenance(provenance, df, req_start, req_end, served)
                    return DailyDataResult(df=df, provenance=provenance)
            except Exception as e:
                logger.warning(
                    f"获取股票 {stock_code} 日线数据失败(第{attempt}次): {e}"
                )
            if attempt <= max_retries:
                time.sleep(_RETRY_SLEEP * attempt)
                continue
            logger.warning(f"股票 {stock_code} Eastmoney 日线重试耗尽，回退 Tushare")
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
