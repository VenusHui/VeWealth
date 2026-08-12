"""
数据源健康检查 / 监控指标 / 降级事件可观测入口。

- GET /api/health         总体健康状态摘要
- GET /api/health/sources 各数据源探针与运行状态快照（?refresh=true 触发实时探针）
- GET /api/health/metrics 按数据源聚合的监控指标
- GET /api/health/events  最近的降级事件（失败 / 回退 / 恢复）
"""

from fastapi import APIRouter, Query

from app.core.source_health import source_monitor
from app.providers.probes import run_all_probes

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def overall_health(
    refresh: bool = Query(False, description="为 true 时先执行一轮实时探针"),
):
    """总体健康状态摘要（ok / degraded / unhealthy / unknown）。"""
    if refresh:
        run_all_probes()
    return {
        "status": source_monitor.overall_status(),
        "snapshot": source_monitor.snapshot(),
    }


@router.get("/sources")
async def source_health(
    refresh: bool = Query(False, description="为 true 时先执行一轮实时探针"),
):
    """各数据源健康快照（状态、连续失败、成功率、耗时等）。"""
    if refresh:
        run_all_probes()
    return source_monitor.snapshot()


@router.get("/metrics")
async def health_metrics():
    """按数据源聚合的监控指标（请求量 / 成功率 / 平均耗时）。"""
    return source_monitor.metrics()


@router.get("/events")
async def health_events(
    limit: int = Query(50, ge=1, le=200, description="返回的最大事件条数"),
):
    """最近的降级事件（新 → 旧）。"""
    return {"events": source_monitor.events_recent(limit)}
