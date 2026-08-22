"""
数据源健康度与降级观测核心模块。

多数据源（mootdx / Tushare / AKShare / 腾讯 / 东财）目前靠 try/except 静默降级，
缺少统一健康状态、监控指标与降级事件记录。本模块提供一个进程内的
SourceHealthMonitor 单例：

- 维护每个数据源的运行状态（up / down / skipped / unknown）与连续失败次数
- 累积请求量、成功率、耗时等监控指标
- 将降级事件（失败 / 回退 / 恢复）写入有界环形缓冲，供可观测入口查询
- 通过结构化日志输出降级事件，供日志采集 / 告警系统消费

本模块仅依赖标准库与 app.core.logger，可被 providers / services / routers
任意引用，不引入循环依赖。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logger import get_module_logger

logger = get_module_logger("source_health")

# 事件类型
EVENT_FAILURE = "failure"  # 单次请求失败
EVENT_FALLBACK = "fallback"  # 主动降级到备源
EVENT_RECOVERY = "recovery"  # 数据源恢复
EVENT_SKIPPED = "skipped"  # 未配置 / 依赖缺失，未纳入健康判断

# 状态值
STATUS_UP = "up"
STATUS_DOWN = "down"
STATUS_SKIPPED = "skipped"
STATUS_UNKNOWN = "unknown"

# 总体健康状态值
OVERALL_OK = "ok"
OVERALL_DEGRADED = "degraded"
OVERALL_UNHEALTHY = "unhealthy"


def _now_iso() -> str:
    """当前 UTC 时间 ISO8601 字符串（进程内观测时间）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SourceState:
    """单个数据源的运行状态与聚合指标。"""

    name: str
    status: str = STATUS_UNKNOWN
    consecutive_failures: int = 0
    total_requests: int = 0
    total_success: int = 0
    total_failure: int = 0
    total_skipped: int = 0
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_error: Optional[str] = None
    last_latency_ms: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    last_checked_at: Optional[str] = None

    @property
    def success_rate(self) -> Optional[float]:
        if self.total_requests == 0:
            return None
        return round(self.total_success / self.total_requests, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "consecutive_failures": self.consecutive_failures,
            "total_requests": self.total_requests,
            "total_success": self.total_success,
            "total_failure": self.total_failure,
            "total_skipped": self.total_skipped,
            "success_rate": self.success_rate,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error": self.last_error,
            "last_latency_ms": self.last_latency_ms,
            "avg_latency_ms": self.avg_latency_ms,
            "last_checked_at": self.last_checked_at,
        }


@dataclass
class DegradationEvent:
    """一次可观测的降级事件。"""

    ts: str
    source: str
    event_type: str
    level: str
    message: str
    detail: Optional[str] = None
    duration_ms: Optional[float] = None
    context: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "source": self.source,
            "event_type": self.event_type,
            "level": self.level,
            "message": self.message,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "context": self.context,
        }


class SourceHealthMonitor:
    """数据源健康度监控器（进程内单例）。

    线程安全：所有状态更新在锁内完成，probe 定时任务与请求路径可并发写入。
    """

    def __init__(
        self,
        event_limit: int = 200,
        fail_threshold: int = 3,
        sources: Optional[list[str]] = None,
    ) -> None:
        self._event_limit = max(event_limit, 1)
        self._fail_threshold = max(fail_threshold, 1)
        self._lock = threading.Lock()
        self._states: dict[str, SourceState] = {}
        self._events: deque[DegradationEvent] = deque(maxlen=self._event_limit)
        for name in sources or []:
            self.register_source(name)

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def configure(
        self,
        event_limit: Optional[int] = None,
        fail_threshold: Optional[int] = None,
        sources: Optional[list[str]] = None,
    ) -> None:
        """应用启动时用配置覆盖默认参数并预注册数据源。"""
        with self._lock:
            if event_limit is not None:
                self._event_limit = max(int(event_limit), 1)
                self._events = deque(self._events, maxlen=self._event_limit)
            if fail_threshold is not None:
                self._fail_threshold = max(int(fail_threshold), 1)
            for name in sources or []:
                if name not in self._states:
                    self._states[name] = SourceState(name=name)

    def register_source(self, name: str) -> None:
        """预注册一个数据源（幂等）。"""
        with self._lock:
            if name not in self._states:
                self._states[name] = SourceState(name=name)

    # ------------------------------------------------------------------
    # 事件写入
    # ------------------------------------------------------------------

    def _emit(self, event: DegradationEvent) -> None:
        self._events.appendleft(event)
        # 结构化日志：供日志采集 / 告警系统消费
        log = getattr(logger, event.level.lower(), logger.info)
        log(
            "[data-source] %s source=%s message=%s detail=%s context=%s",
            event.event_type,
            event.source,
            event.message,
            event.detail or "",
            event.context or "",
        )

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------

    def record_attempt(
        self,
        source: str,
        ok: bool,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
        context: Optional[str] = None,
    ) -> None:
        """记录一次对某数据源的实际请求 / 探针结果。"""
        self.register_source(source)
        now = _now_iso()
        with self._lock:
            st = self._states[source]
            st.total_requests += 1
            st.last_checked_at = now
            st.last_latency_ms = duration_ms
            if duration_ms is not None:
                if st.avg_latency_ms is None:
                    st.avg_latency_ms = round(duration_ms, 2)
                else:
                    # 简单指数移动平均
                    st.avg_latency_ms = round(
                        0.8 * st.avg_latency_ms + 0.2 * duration_ms, 2
                    )

            if ok:
                was_down = st.status == STATUS_DOWN
                st.total_success += 1
                st.consecutive_failures = 0
                st.status = STATUS_UP
                st.last_success_at = now
                st.last_error = None
                if was_down:
                    self._emit(
                        DegradationEvent(
                            ts=now,
                            source=source,
                            event_type=EVENT_RECOVERY,
                            level="INFO",
                            message=f"数据源 {source} 恢复可用",
                            duration_ms=duration_ms,
                            context=context,
                        )
                    )
            else:
                st.total_failure += 1
                st.consecutive_failures += 1
                st.status = STATUS_DOWN
                st.last_failure_at = now
                st.last_error = error or "unknown error"
                level = (
                    "ERROR"
                    if st.consecutive_failures >= self._fail_threshold
                    else "WARNING"
                )
                self._emit(
                    DegradationEvent(
                        ts=now,
                        source=source,
                        event_type=EVENT_FAILURE,
                        level=level,
                        message=(
                            f"数据源 {source} 请求失败 "
                            f"(连续 {st.consecutive_failures} 次)"
                        ),
                        detail=error,
                        duration_ms=duration_ms,
                        context=context,
                    )
                )

    def record_fallback(
        self,
        source: str,
        fallback_to: str,
        reason: Optional[str] = None,
        context: Optional[str] = None,
    ) -> None:
        """记录一次从 source 降级到 fallback_to 的回退事件。"""
        self._emit(
            DegradationEvent(
                ts=_now_iso(),
                source=source,
                event_type=EVENT_FALLBACK,
                level="WARNING",
                message=f"数据源 {source} 降级到 {fallback_to}",
                detail=reason,
                context=context,
            )
        )

    def record_skipped(
        self,
        source: str,
        detail: Optional[str] = None,
    ) -> None:
        """记录数据源因未配置 / 依赖缺失被跳过（不算故障）。"""
        self.register_source(source)
        now = _now_iso()
        with self._lock:
            st = self._states[source]
            st.total_skipped += 1
            st.status = STATUS_SKIPPED
            st.last_checked_at = now
        self._emit(
            DegradationEvent(
                ts=now,
                source=source,
                event_type=EVENT_SKIPPED,
                level="INFO",
                message=f"数据源 {source} 未参与健康检查",
                detail=detail,
            )
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """返回全部数据源健康快照（含总体状态）。"""
        with self._lock:
            sources = {name: st.to_dict() for name, st in sorted(self._states.items())}
        return {
            "overall_status": self.overall_status(),
            "checked_at": _now_iso(),
            "sources": sources,
        }

    def overall_status(self) -> str:
        """聚合总体健康状态。

        - unhealthy: 参与健康检查的数据源全部不可用
        - degraded:  至少一个数据源不可用，其余可用
        - ok:        无不可用数据源
        - unknown:   尚未执行任何探针 / 请求
        """
        with self._lock:
            active = [st for st in self._states.values() if st.status != STATUS_SKIPPED]
            if not active:
                return STATUS_UNKNOWN
            down = [st for st in active if st.status == STATUS_DOWN]
            if not down:
                return OVERALL_OK
            if len(down) == len(active):
                return OVERALL_UNHEALTHY
            return OVERALL_DEGRADED

    def metrics(self) -> dict[str, Any]:
        """返回按数据源聚合的监控指标。"""
        with self._lock:
            sources = {
                name: {
                    "status": st.status,
                    "total_requests": st.total_requests,
                    "total_success": st.total_success,
                    "total_failure": st.total_failure,
                    "total_skipped": st.total_skipped,
                    "success_rate": st.success_rate,
                    "consecutive_failures": st.consecutive_failures,
                    "avg_latency_ms": st.avg_latency_ms,
                }
                for name, st in sorted(self._states.items())
            }
        return {
            "overall_status": self.overall_status(),
            "generated_at": _now_iso(),
            "sources": sources,
        }

    def events_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """返回最近的降级事件（新 → 旧）。"""
        with self._lock:
            return [e.to_dict() for e in list(self._events)[:limit]]

    def reset(self) -> None:
        """清空全部状态与事件（测试用）。"""
        with self._lock:
            self._states.clear()
            self._events.clear()


# 全局监控器实例（默认参数；应用启动时由 probes 模块按配置覆盖）
source_monitor = SourceHealthMonitor()
