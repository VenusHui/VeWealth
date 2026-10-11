"""
磁盘余量观测与告警（VEW-67）。

生产 `/` 是单块 40 GB 盘，postgres + backend + frontend 都在上面 —— 写满是一起挂，
不是某个组件单独失败。VEW-68 给 docker 构建缓存加了 1 GB 封顶（「能削」），但封顶
失败时只有一行日志、没有人被通知（「削不动了」没人知道）。本模块补的是后一半：
把「盘还剩多少」变成可查询、可告警的观测量。

与 ``source_health`` 同形：进程内单例 + 有界事件环形缓冲 + 结构化日志 + 状态快照。
采样只有一次 ``statfs``、无副作用，故默认开启（``DISK_HEADROOM_ENABLED``）。

**投递方式（重要，别把它当成已实现的推送）**：本模块产出的是**观测与日志**，不是
通知。仓库内目前没有日志采集 / 告警系统，结构化日志落进容器 stdout 后不会主动到达
任何人 —— 只做这一半，等于把 VEW-67 抱怨的「只有一行日志」换个地方重演。真正会通知
到人的通道是 GitHub Actions（流水线注解与失败），由 ``.github/scripts/disk_headroom_check.sh``
在部署前后各判一次；两者共用同一组阈值（``DISK_HEADROOM_*``）：本模块是查询入口，
那个脚本是投递。

容器内视角：``/`` 在容器里是 overlay 挂载，``statfs`` 返回的仍是宿主 40 GB 盘的空闲
空间（overlay 的上层目录就在宿主的 ``/`` 上）；``/app/data``（持久卷）同在宿主 ``/``。
两者读数一致，故默认采样 ``/``。挂载点可用 ``DISK_HEADROOM_PATHS`` 覆盖。
"""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.core.config import settings
from app.core.logger import get_module_logger

logger = get_module_logger("capacity")

# 状态值
STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_CRITICAL = "critical"
STATUS_UNKNOWN = "unknown"

# 事件类型
EVENT_LOW = "low"  # 余量跌入 warning / critical
EVENT_RECOVERED = "recovered"  # 从 warning / critical 回到 ok
EVENT_UNREADABLE = "unreadable"  # 挂载点读不到（路径不存在 / 权限）

# 严重度排序：critical > warning > unknown > ok（unknown 是「没测到」，不是「没问题」）
_SEVERITY = {
    STATUS_CRITICAL: 3,
    STATUS_WARNING: 2,
    STATUS_UNKNOWN: 1,
    STATUS_OK: 0,
}

_GB = 1024**3


def _now_iso() -> str:
    """当前 UTC 时间 ISO8601 字符串（进程内观测时间）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_paths(raw: str) -> list[str]:
    """把逗号分隔的挂载点配置解析为列表（去空、保序、去重）。"""
    seen: list[str] = []
    for item in str(raw or "").split(","):
        path = item.strip()
        if path and path not in seen:
            seen.append(path)
    return seen or ["/"]


@dataclass
class DiskUsage:
    """单个挂载点的一次采样结果。"""

    path: str
    status: str
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    free_gb: float = 0.0
    used_percent: float = 0.0
    checked_at: str = ""
    status_since: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "total_gb": round(self.total_bytes / _GB, 2),
            "used_gb": round(self.used_bytes / _GB, 2),
            "free_gb": round(self.free_gb, 2),
            "used_percent": round(self.used_percent, 1),
            "checked_at": self.checked_at,
            "status_since": self.status_since,
            "error": self.error,
        }


@dataclass
class CapacityEvent:
    """一次可观测的容量事件（状态跃迁，不是每次采样）。"""

    ts: str
    path: str
    event_type: str
    level: str
    message: str
    free_gb: Optional[float] = None
    detail: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "path": self.path,
            "event_type": self.event_type,
            "level": self.level,
            "message": self.message,
            "free_gb": self.free_gb,
            "detail": self.detail,
        }


class CapacityMonitor:
    """磁盘余量监控器（进程内单例）。

    只在**状态跃迁**时产出事件与告警日志：采样周期是分钟级，若每次低于阈值都写一行，
    日志会被自己淹没，反而看不出「什么时候跌下去的」。快照始终反映最新读数，所以
    「当前状态」查 ``snapshot()`` / ``sample()``，「何时变的」查 ``events_recent()``。

    线程安全：采样与状态更新在锁内完成，调度任务与 HTTP 读路径可并发调用。

    阈值（``warn_gb`` / ``critical_gb``）**必填**：这里刻意不留默认值 —— 阈值是策略
    数字，唯一权威的默认值在 ``Settings.DISK_HEADROOM_*``（本模块的单例从那里取），
    另一份副本在部署闸门 ``.github/scripts/disk_headroom_check.sh``（shell 侧无法共用
    Python 配置），两者由 ``tests/test_capacity_vew67.py`` 钉住一致。类里再放一份就等于
    第三个副本：裸构造会按一套没人记得的旧政策行事，而且漂了不会有任何症状 —— 正是
    VEW-67 要消灭的那种失效（VEW-67 评审 M2）。
    """

    def __init__(
        self,
        paths: Optional[list[str]] = None,
        *,
        warn_gb: float,
        critical_gb: float,
        event_limit: int = 100,
        usage_fn: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._paths: list[str] = list(paths) if paths else ["/"]
        self._warn_gb = float(warn_gb)
        self._critical_gb = float(critical_gb)
        self._event_limit = max(int(event_limit), 1)
        self._events: list[CapacityEvent] = []
        # 可注入的采样函数（默认 shutil.disk_usage），测试用固定读数替换
        self._usage_fn = usage_fn or shutil.disk_usage
        self._last: dict[str, DiskUsage] = {}

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def configure(
        self,
        paths: Optional[list[str]] = None,
        warn_gb: Optional[float] = None,
        critical_gb: Optional[float] = None,
        event_limit: Optional[int] = None,
    ) -> None:
        """运行时覆盖构造时传入的阈值 / 挂载点 / 缓冲上限（应用启动时按配置调用）。"""
        with self._lock:
            if paths:
                self._paths = list(paths)
            if warn_gb is not None:
                self._warn_gb = float(warn_gb)
            if critical_gb is not None:
                self._critical_gb = float(critical_gb)
            if event_limit is not None:
                self._event_limit = max(int(event_limit), 1)
                self._events = self._events[-self._event_limit :]

    @property
    def warn_gb(self) -> float:
        return self._warn_gb

    @property
    def critical_gb(self) -> float:
        return self._critical_gb

    # ------------------------------------------------------------------
    # 采样
    # ------------------------------------------------------------------

    def _classify(self, free_gb: float) -> str:
        if free_gb < self._critical_gb:
            return STATUS_CRITICAL
        if free_gb < self._warn_gb:
            return STATUS_WARNING
        return STATUS_OK

    def _sample_one(self, path: str) -> DiskUsage:
        """采样单个挂载点（纯读，不写状态、不发事件）。"""
        checked_at = _now_iso()
        try:
            total, used, free = self._usage_fn(path)
        except OSError as e:
            return DiskUsage(
                path=path,
                status=STATUS_UNKNOWN,
                checked_at=checked_at,
                status_since=checked_at,
                error=f"{type(e).__name__}: {e}",
            )
        free_gb = free / _GB
        return DiskUsage(
            path=path,
            status=self._classify(free_gb),
            total_bytes=int(total),
            used_bytes=int(used),
            free_bytes=int(free),
            free_gb=free_gb,
            used_percent=(used / total * 100) if total else 0.0,
            checked_at=checked_at,
            status_since=checked_at,
        )

    def sample(self) -> dict[str, Any]:
        """实时采样并返回快照（纯读，无副作用）。

        HTTP 读路径用它 —— GET 不该写事件流；状态跃迁由 ``check()``（调度任务）记录。
        """
        with self._lock:
            paths = list(self._paths)
        disks = [self._sample_one(p) for p in paths]
        return {
            "status": self._overall(disks),
            "checked_at": _now_iso(),
            "thresholds": {
                "warn_gb": self._warn_gb,
                "critical_gb": self._critical_gb,
            },
            "disks": [d.to_dict() for d in disks],
        }

    # ------------------------------------------------------------------
    # 记录（调度任务）
    # ------------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        """采样 + 记录状态跃迁，返回快照。供 APScheduler 定时调用。"""
        with self._lock:
            paths = list(self._paths)
            previous = dict(self._last)

        disks = [self._sample_one(p) for p in paths]
        for disk in disks:
            prev = previous.get(disk.path)
            if prev is not None and prev.status == disk.status:
                # 状态未变：沿用上次的跃迁时间，不重复告警
                disk.status_since = prev.status_since
            else:
                self._emit_transition(prev, disk)

        with self._lock:
            self._last = {d.path: d for d in disks}

        return {
            "status": self._overall(disks),
            "checked_at": _now_iso(),
            "thresholds": {
                "warn_gb": self._warn_gb,
                "critical_gb": self._critical_gb,
            },
            "disks": [d.to_dict() for d in disks],
        }

    def snapshot(self) -> dict[str, Any]:
        """最近一次 ``check()`` 的快照（尚未检查时 disks 为空）。

        ``checked_at`` 取**最新那次采样**的时刻，不是本方法的调用时刻 —— 否则调用方
        会读成「这份读数刚取的」。尚未检查时为空串，与「刚取过」区分得开（VEW-67 评审）。
        """
        with self._lock:
            disks = [self._last[p] for p in self._paths if p in self._last]
        return {
            "status": self._overall(disks),
            "checked_at": max((d.checked_at for d in disks), default=""),
            "thresholds": {
                "warn_gb": self._warn_gb,
                "critical_gb": self._critical_gb,
            },
            "disks": [d.to_dict() for d in disks],
        }

    def _emit_transition(self, prev: Optional[DiskUsage], cur: DiskUsage) -> None:
        """状态跃迁 → 一条事件 + 一行结构化日志。"""
        if cur.status == STATUS_UNKNOWN:
            self._emit(
                CapacityEvent(
                    ts=cur.checked_at,
                    path=cur.path,
                    event_type=EVENT_UNREADABLE,
                    level="WARNING",
                    message=f"磁盘余量读不到: {cur.path}",
                    detail=cur.error,
                )
            )
            return

        if cur.status == STATUS_OK:
            if prev is not None and prev.status in (STATUS_WARNING, STATUS_CRITICAL):
                self._emit(
                    CapacityEvent(
                        ts=cur.checked_at,
                        path=cur.path,
                        event_type=EVENT_RECOVERED,
                        level="INFO",
                        message=(f"磁盘余量恢复: {cur.path} 可用 {cur.free_gb:.1f} GB"),
                        free_gb=cur.free_gb,
                    )
                )
            return

        level = "ERROR" if cur.status == STATUS_CRITICAL else "WARNING"
        self._emit(
            CapacityEvent(
                ts=cur.checked_at,
                path=cur.path,
                event_type=EVENT_LOW,
                level=level,
                message=(
                    f"磁盘余量不足: {cur.path} 可用 {cur.free_gb:.1f} GB"
                    f"（{cur.status}，阈值 警告<{self._warn_gb:g}GB / "
                    f"临界<{self._critical_gb:g}GB）"
                ),
                free_gb=cur.free_gb,
                detail=prev.status if prev is not None else None,
            )
        )

    def _emit(self, event: CapacityEvent) -> None:
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._event_limit:
                self._events = self._events[-self._event_limit :]
        # 结构化日志：供日志采集 / 告警系统消费（当前无采集器，见模块 docstring）
        log = getattr(logger, event.level.lower(), logger.info)
        log(
            "[capacity] %s path=%s free_gb=%s message=%s detail=%s",
            event.event_type,
            event.path,
            "" if event.free_gb is None else f"{event.free_gb:.2f}",
            event.message,
            event.detail or "",
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def _overall(self, disks: list[DiskUsage]) -> str:
        """取最严重的一档（无挂载点时 unknown）。"""
        if not disks:
            return STATUS_UNKNOWN
        return max((d.status for d in disks), key=lambda s: _SEVERITY.get(s, 0))

    def overall_status(self) -> str:
        with self._lock:
            disks = [self._last[p] for p in self._paths if p in self._last]
        return self._overall(disks)

    def events_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近的容量事件（新 → 旧）。"""
        with self._lock:
            events = list(self._events)
        return [e.to_dict() for e in reversed(events)][:limit]

    def reset(self) -> None:
        """清空全部状态与事件（测试用）。"""
        with self._lock:
            self._last.clear()
            self._events.clear()


# 全局监控器实例（默认参数；按配置初始化）
capacity_monitor = CapacityMonitor(
    paths=parse_paths(settings.DISK_HEADROOM_PATHS),
    warn_gb=settings.DISK_HEADROOM_WARN_GB,
    critical_gb=settings.DISK_HEADROOM_CRITICAL_GB,
    event_limit=settings.DISK_HEADROOM_EVENT_LIMIT,
)
