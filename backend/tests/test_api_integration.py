"""VEW-30 API 级集成测试（HTTP 层）。

覆盖此前缺失的 API 层回归：
- 鉴权：未带 token 的受保护接口固定返回 401；
- 运行时校验：非法策略参数通过 HTTP 返回 422（与所选策略契约一致）；
- 资源归属：不存在的 run 返回 404；
- 只读端点冒烟（策略列表、健康检查、根路径）。

所有用例不依赖真实数据库/行情网络，使用 TestClient + 依赖覆盖，确定性可复现。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base

AUTH = {"Authorization": "Bearer x"}


def _sqlite_db_override():
    """get_db 覆盖：内存 SQLite，仅用于需要查询 DB 的路径。

    使用 StaticPool 让内存库跨连接共享，并允许跨线程（TestClient 在线程池执行请求）。
    否则内存库按连接隔离，请求线程里的查询会落到空库。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    db = maker()
    try:
        yield db
    finally:
        db.close()


def test_health_returns_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_root_returns_api_info(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "version" in resp.json()


def test_strategies_requires_auth(make_client):
    client = make_client(authenticated=False)
    resp = client.get("/api/backtest/strategies")
    assert resp.status_code == 401


def test_strategies_returns_200_with_auth(client):
    resp = client.get("/api/backtest/strategies")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    strategies = payload["data"]
    assert isinstance(strategies, list)
    assert len(strategies) > 0
    for item in strategies:
        assert "strategy_id" in item
        assert "usable" in item


def test_scan_list_requires_auth(make_client):
    client = make_client(authenticated=False)
    resp = client.get("/api/screener/scans")
    assert resp.status_code == 401


def test_run_rejects_cross_field_violation_422(client):
    # short_window >= long_window 触发提交前运行时校验 → 422
    body = {
        "name": "invalid",
        "strategy_id": "ma_cross_v1",
        "strategy_params": {"short_window": 31, "long_window": 30},
        "mode": "strategy_select",
        "start_date": "2025-01-01",
        "end_date": "2025-02-01",
        "initial_cash": 100000,
    }
    resp = client.post("/api/backtest/run", json=body, headers=AUTH)
    assert resp.status_code == 422


def test_run_rejects_missing_required_field_422(client):
    body = {"strategy_id": "ma_cross_v1"}
    resp = client.post("/api/backtest/run", json=body, headers=AUTH)
    assert resp.status_code == 422


def test_run_unknown_strategy_422(client):
    body = {
        "name": "nope",
        "strategy_id": "does_not_exist",
        "strategy_params": {},
        "mode": "strategy_select",
        "start_date": "2025-01-01",
        "end_date": "2025-02-01",
        "initial_cash": 100000,
    }
    resp = client.post("/api/backtest/run", json=body, headers=AUTH)
    assert resp.status_code == 422


def test_get_nonexistent_run_404(make_client):
    client = make_client(db_override=_sqlite_db_override)
    resp = client.get("/api/backtest/runs/999999", headers=AUTH)
    assert resp.status_code == 404
