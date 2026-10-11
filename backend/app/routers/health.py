"""
数据源健康检查 / 监控指标 / 降级事件可观测入口。

- GET /api/health         总体健康状态摘要
- GET /api/health/sources 各数据源探针与运行状态快照（?refresh=true 触发实时探针）
                          另含 mootdx 的**分周期**健康档案（mirror_periods，VEW-70）
- GET /api/health/metrics 按数据源聚合的监控指标
- GET /api/health/events  最近的降级事件（失败 / 回退 / 恢复）
- GET /api/health/capacity 磁盘余量快照与容量事件（VEW-67）
"""

from fastapi import APIRouter, Query

from app.core.capacity import capacity_monitor
from app.core.source_health import source_monitor
from app.providers.mirror_health import mirror_health
from app.providers.probes import run_all_probes

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def overall_health(
    refresh: bool = Query(False, description="为 true 时先执行一轮实时探针"),
):
    """总体健康状态摘要（ok / degraded / unhealthy / unknown）。

    ``status`` 仍是**数据源**口径；磁盘余量以 ``capacity`` 键并列返回，不并入
    ``status`` —— 「源不可用」与「盘要满了」是两种不同的处境、不同的处置，混成
    一个枚举会让两边都读不准（VEW-67）。
    """
    if refresh:
        run_all_probes()
    return {
        "status": source_monitor.overall_status(),
        "snapshot": source_monitor.snapshot(),
        "capacity": capacity_monitor.sample(),
    }


@router.get("/sources")
def source_health(
    refresh: bool = Query(False, description="为 true 时先执行一轮实时探针"),
):
    """各数据源健康快照（状态、连续失败、成功率、耗时等）。

    ``mirror_periods`` 是 mootdx 的**分周期**健康档案：源级状态只有一个，
    但「分钟可用、日线抖动」与「整源不可用」是两种完全不同的处境，靠它区分
    （VEW-70）。
    """
    if refresh:
        run_all_probes()
    snapshot = source_monitor.snapshot()
    snapshot["mirror_periods"] = mirror_health.snapshot()
    return snapshot


@router.get("/metrics")
def health_metrics():
    """按数据源聚合的监控指标（请求量 / 成功率 / 平均耗时）。"""
    return source_monitor.metrics()


@router.get("/events")
def health_events(
    limit: int = Query(50, ge=1, le=200, description="返回的最大事件条数"),
):
    """最近的降级事件（新 → 旧）。"""
    return {"events": source_monitor.events_recent(limit)}


@router.get("/capacity")
def health_capacity(
    events: int = Query(20, ge=0, le=100, description="同时返回的容量事件条数"),
):
    """磁盘余量快照（VEW-67）。

    每次请求都**实时采样**（一次 ``statfs``，开销可忽略），不返回缓存读数 —— 查盘
    余量的人要的是「现在还剩多少」，陈旧数字比没有更糟。采样是纯读：它不写事件流、
    不发告警日志（状态跃迁由调度任务 ``check_disk_headroom`` 记录），所以这个 GET
    没有副作用。``events`` 返回最近的状态跃迁（新 → 旧），``events=0`` 则只要快照。
    """
    snapshot = capacity_monitor.sample()
    snapshot["events"] = capacity_monitor.events_recent(events) if events else []
    return snapshot
