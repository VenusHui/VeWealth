"""
数据源级健康探针。

每个探针对单一数据源做一次轻量、只读、快速的可达性检查，并把结果写入
app.core.source_health.source_monitor，供健康检查接口与定时任务消费：

- eastmoney: 复用 astock_data.eastmoney_ping（探针自记语义结果，低层 record=False）
- tencent:   复用 astock_data.tencent_quote（探针自记语义结果，低层 _record=False）
- mootdx:    直接调用 mootdx TCP 客户端取 3 根日 K（探针内埋点）；与取数路径共用
             client，故同样持取数锁，但等锁有上限，超时本轮顺延（不改源状态）
- tushare:   配置缺失 / 依赖未装时标记为 skipped（不算故障）
- akshare:   依赖未装时标记为 skipped

真实请求路径的监控埋点在 astock_data（东财 / 腾讯）内，与探针计数互不重复。

run_all_probes() 串行执行全部探针并返回结果，供 APScheduler 定时调用。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.core.config import settings
from app.core.source_health import (
    source_monitor,
    STATUS_DOWN,
    STATUS_SKIPPED,
    STATUS_UNKNOWN,
    STATUS_UP,
)
from app.providers.astock_data import eastmoney_ping, tencent_quote

try:
    import tushare as ts
except Exception:  # pragma: no cover - 依赖可选
    ts = None

try:
    import akshare as ak
except Exception:  # pragma: no cover - 依赖可选
    ak = None

logger = logging.getLogger(__name__)

# 参与健康检查的全部数据源
REGISTERED_SOURCES = ["eastmoney", "tencent", "mootdx", "tushare", "akshare"]


@dataclass
class ProbeResult:
    """单个探针的执行结果。"""

    source: str
    status: str
    duration_ms: float
    detail: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 1),
            "detail": self.detail,
            "error": self.error,
        }


def _probe_symbol() -> str:
    return getattr(settings, "SOURCE_HEALTH_PROBE_SYMBOL", "000001")


def probe_eastmoney() -> ProbeResult:
    """东财：拉取最近 3 根日 K 线，记录语义级探针结果。"""
    start = time.monotonic()
    try:
        ok = eastmoney_ping(code=_probe_symbol())
        duration_ms = (time.monotonic() - start) * 1000
        source_monitor.record_attempt(
            "eastmoney",
            ok=ok,
            duration_ms=duration_ms,
            error=None if ok else "东财K线探针返回空",
            context="probe",
        )
        return ProbeResult(
            source="eastmoney",
            status=STATUS_UP if ok else STATUS_DOWN,
            duration_ms=duration_ms,
            detail="东财K线探针" if ok else "东财K线探针返回空",
        )
    except Exception as e:  # pragma: no cover - 防御性兜底
        duration_ms = (time.monotonic() - start) * 1000
        source_monitor.record_attempt(
            "eastmoney",
            ok=False,
            duration_ms=duration_ms,
            error=str(e),
            context="probe",
        )
        return ProbeResult(
            source="eastmoney",
            status=STATUS_DOWN,
            duration_ms=duration_ms,
            error=str(e),
        )


def probe_tencent() -> ProbeResult:
    """腾讯：批量行情单股查询，记录语义级探针结果。"""
    start = time.monotonic()
    try:
        quotes = tencent_quote([_probe_symbol()], _record=False)
        ok = bool(quotes)
        duration_ms = (time.monotonic() - start) * 1000
        source_monitor.record_attempt(
            "tencent",
            ok=ok,
            duration_ms=duration_ms,
            error=None if ok else "腾讯行情返回空或解析失败",
            context="probe",
        )
        return ProbeResult(
            source="tencent",
            status=STATUS_UP if ok else STATUS_DOWN,
            duration_ms=duration_ms,
            detail="腾讯行情探针" if ok else "腾讯行情返回空",
        )
    except Exception as e:  # pragma: no cover - 防御性兜底
        duration_ms = (time.monotonic() - start) * 1000
        source_monitor.record_attempt(
            "tencent",
            ok=False,
            duration_ms=duration_ms,
            error=str(e),
            context="probe",
        )
        return ProbeResult(
            source="tencent",
            status=STATUS_DOWN,
            duration_ms=duration_ms,
            error=str(e),
        )


def probe_mootdx() -> ProbeResult:
    """mootdx：TCP 直连取 3 根日 K。

    Uses the lazy accessor so a transient init failure at boot is retried on
    subsequent probes (VEW-36), instead of being permanently reported skipped.
    """
    try:
        from app.providers.astock_provider import (
            _get_mootdx_client,
            _invalidate_mootdx_client,
            _mootdx_fetch_guard,
            _MOOTDX_PROBE_LOCK_TIMEOUT,
        )
    except Exception:
        source_monitor.record_skipped("mootdx", detail="mootdx 依赖或客户端初始化失败")
        return ProbeResult(
            source="mootdx",
            status=STATUS_SKIPPED,
            duration_ms=0,
            detail="mootdx 依赖或客户端初始化失败",
        )

    client = _get_mootdx_client()
    if client is None:
        source_monitor.record_skipped("mootdx", detail="mootdx 客户端未初始化")
        return ProbeResult(
            source="mootdx",
            status=STATUS_SKIPPED,
            duration_ms=0,
            detail="mootdx 客户端未初始化",
        )

    # 探针与取数路径共用同一 client：TDX 一问一答，不持锁并发调用会让响应错位、
    # 静默返回空，进而误判镜像故障并摘除客户端（VEW-60）。等锁有上限：取数正在翻页
    # 时不等满全程，超时即顺延本轮 —— 既不算源故障，也不把串行的 run_all_probes()
    # 拖住，让排在后面的 eastmoney 探针（熔断恢复主路径）按时执行（VEW-60 评审 M3）。
    with _mootdx_fetch_guard(_MOOTDX_PROBE_LOCK_TIMEOUT) as acquired:
        if not acquired:
            logger.warning(
                "[source-probe] mootdx 等待取数锁超时(%.1fs)，本轮顺延",
                _MOOTDX_PROBE_LOCK_TIMEOUT,
            )
            # 顺延 ≠ skipped：本轮没测到不代表「未配置 / 依赖缺失」。用
            # record_deferred 只留痕、不改 status，否则繁忙扫描期间 mootdx 会间歇性
            # 显示为 skipped（正是本 issue 要消除的现象），还会被 overall_status 从
            # active 集合里剔除而误报 ok（VEW-60 评审）。
            source_monitor.record_deferred("mootdx", detail="取数进行中，探针等锁超时")
            return ProbeResult(
                source="mootdx",
                status=STATUS_UNKNOWN,
                duration_ms=0,
                detail="取数进行中，探针等锁超时（本轮顺延）",
            )

        # duration_ms 从拿到锁之后开始计：探针与取数共用 client，把等锁时间算进去会
        # 让 /api/health/sources 的源延迟随并发取数虚高（VEW-60 评审 M3）。
        start = time.monotonic()
        try:
            df = client.bars(symbol=_probe_symbol(), frequency=4, start=0, offset=3)
            ok = df is not None and not df.empty
            if not ok:
                _invalidate_mootdx_client(client)
            duration_ms = (time.monotonic() - start) * 1000
            source_monitor.record_attempt(
                "mootdx",
                ok=ok,
                duration_ms=duration_ms,
                error=None if ok else "mootdx 探针返回空",
                context="probe",
            )
            return ProbeResult(
                source="mootdx",
                status=STATUS_UP if ok else STATUS_DOWN,
                duration_ms=duration_ms,
                detail="mootdx K线探针" if ok else "mootdx K线探针返回空",
            )
        except Exception as e:
            _invalidate_mootdx_client(client)
            duration_ms = (time.monotonic() - start) * 1000
            source_monitor.record_attempt(
                "mootdx",
                ok=False,
                duration_ms=duration_ms,
                error=str(e),
                context="probe",
            )
            return ProbeResult(
                source="mootdx",
                status=STATUS_DOWN,
                duration_ms=duration_ms,
                error=str(e),
            )


def probe_tushare() -> ProbeResult:
    """Tushare：未启用 / 未配置 token / 依赖缺失时标记 skipped。"""
    if not settings.TUSHARE_ENABLED or not settings.TUSHARE_TOKEN or ts is None:
        source_monitor.record_skipped(
            "tushare", detail="Tushare 未启用 / 未配置 token / 依赖未安装"
        )
        return ProbeResult(
            source="tushare",
            status=STATUS_SKIPPED,
            duration_ms=0,
            detail="未启用或未配置 token",
        )

    start = time.monotonic()
    try:
        ts.set_token(settings.TUSHARE_TOKEN)
        df = ts.pro_bar(
            ts_code="000001.SZ",
            adj=None,
            start_date="20240101",
            end_date="20240110",
        )
        ok = df is not None and not df.empty
        duration_ms = (time.monotonic() - start) * 1000
        source_monitor.record_attempt(
            "tushare",
            ok=ok,
            duration_ms=duration_ms,
            error=None if ok else "tushare 探针返回空",
            context="probe",
        )
        return ProbeResult(
            source="tushare",
            status=STATUS_UP if ok else STATUS_DOWN,
            duration_ms=duration_ms,
            detail="tushare 日线探针" if ok else "tushare 日线探针返回空",
        )
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        source_monitor.record_attempt(
            "tushare",
            ok=False,
            duration_ms=duration_ms,
            error=str(e),
            context="probe",
        )
        return ProbeResult(
            source="tushare",
            status=STATUS_DOWN,
            duration_ms=duration_ms,
            error=str(e),
        )


def probe_akshare() -> ProbeResult:
    """AKShare：依赖未安装时标记 skipped。"""
    if ak is None:
        source_monitor.record_skipped("akshare", detail="akshare 依赖未安装")
        return ProbeResult(
            source="akshare",
            status=STATUS_SKIPPED,
            duration_ms=0,
            detail="akshare 依赖未安装",
        )

    start = time.monotonic()
    try:
        df = ak.stock_zh_a_hist(
            symbol=_probe_symbol(),
            period="daily",
            start_date="20240101",
            end_date="20240110",
            adjust="",
        )
        ok = df is not None and not df.empty
        duration_ms = (time.monotonic() - start) * 1000
        source_monitor.record_attempt(
            "akshare",
            ok=ok,
            duration_ms=duration_ms,
            error=None if ok else "akshare 探针返回空",
            context="probe",
        )
        return ProbeResult(
            source="akshare",
            status=STATUS_UP if ok else STATUS_DOWN,
            duration_ms=duration_ms,
            detail="akshare 日线探针" if ok else "akshare 日线探针返回空",
        )
    except Exception as e:
        duration_ms = (time.monotonic() - start) * 1000
        source_monitor.record_attempt(
            "akshare",
            ok=False,
            duration_ms=duration_ms,
            error=str(e),
            context="probe",
        )
        return ProbeResult(
            source="akshare",
            status=STATUS_DOWN,
            duration_ms=duration_ms,
            error=str(e),
        )


_PROBES: list[Callable[[], ProbeResult]] = [
    probe_eastmoney,
    probe_tencent,
    probe_mootdx,
    probe_tushare,
    probe_akshare,
]


def run_all_probes() -> list[dict[str, Any]]:
    """串行执行全部源级探针并返回结果列表（同步、阻塞至完成）。

    单个探针异常不会中断整轮检查；每轮结果写入 source_monitor 并记 INFO 日志。
    """
    results: list[ProbeResult] = []
    for probe in _PROBES:
        try:
            result = probe()
        except Exception as e:  # pragma: no cover - 探针自身兜底
            result = ProbeResult(
                source=probe.__name__.removeprefix("probe_"),
                status=STATUS_DOWN,
                duration_ms=0,
                error=str(e),
            )
        results.append(result)
        logger.info(
            "[source-probe] source=%s status=%s duration_ms=%.1f detail=%s",
            result.source,
            result.status,
            result.duration_ms,
            result.detail or result.error or "",
        )
    return [r.to_dict() for r in results]


# 应用启动时按配置初始化监控器并预注册数据源
source_monitor.configure(
    event_limit=settings.SOURCE_HEALTH_EVENT_LIMIT,
    fail_threshold=settings.SOURCE_HEALTH_FAIL_THRESHOLD,
    sources=REGISTERED_SOURCES,
)
