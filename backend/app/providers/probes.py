"""
数据源级健康探针。

每个探针对单一数据源做一次轻量、只读、快速的可达性检查，并把结果写入
app.core.source_health.source_monitor，供健康检查接口与定时任务消费：

- eastmoney: 复用 astock_data.eastmoney_ping（探针自记语义结果，低层 record=False）
- tencent:   复用 astock_data.tencent_quote（探针自记语义结果，低层 _record=False）
- mootdx:    直接调用 mootdx TCP 客户端取 3 根日 K（探针内埋点）
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
    """mootdx：TCP 直连取 3 根日 K。"""
    start = time.monotonic()
    try:
        from app.providers.astock_provider import _mootdx_client
    except Exception:
        source_monitor.record_skipped("mootdx", detail="mootdx 依赖或客户端初始化失败")
        return ProbeResult(
            source="mootdx",
            status=STATUS_SKIPPED,
            duration_ms=0,
            detail="mootdx 依赖或客户端初始化失败",
        )

    if _mootdx_client is None:
        source_monitor.record_skipped("mootdx", detail="mootdx 客户端未初始化")
        return ProbeResult(
            source="mootdx",
            status=STATUS_SKIPPED,
            duration_ms=0,
            detail="mootdx 客户端未初始化",
        )

    try:
        df = _mootdx_client.bars(symbol=_probe_symbol(), frequency=4, start=0, offset=3)
        ok = df is not None and not df.empty
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
