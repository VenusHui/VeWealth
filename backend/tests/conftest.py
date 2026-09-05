"""pytest 共享 fixtures 与标记注册（VEW-30 质量门禁）。

为 API 级集成测试提供 TestClient 与鉴权/数据库依赖覆盖；为性能回归标记注册
`perf` 标记。仅在需要使用 FastAPI 应用的测试里才惰性导入 `main`，避免影响纯
单元测试的启动开销。
"""

from __future__ import annotations

import pytest

from app.core.database import get_db
from app.models.user import User


def pytest_configure(config):
    # 注册自定义标记，避免 pytest 对未知标记告警
    config.addinivalue_line(
        "markers", "perf: 性能回归测试（有界运行时间，CI 质量门禁）"
    )
    config.addinivalue_line("markers", "api: API 级 HTTP 集成测试")
    config.addinivalue_line("markers", "e2e: 端到端流程测试")


def _authenticated_user() -> User:
    """鉴权覆盖使用的固定用户。"""
    return User(
        id=1,
        username="alice",
        hashed_password="x",
        is_active=True,
        alert_threshold=0.7,
    )


@pytest.fixture
def make_client():
    """返回一个工厂，用于构建 FastAPI TestClient 实例。

    默认覆盖鉴权依赖（get_current_user / get_current_active_user）返回固定用户；
    传入 ``authenticated=False`` 则跳过鉴权覆盖，用于测 401。
    传入 ``db_override`` 则覆盖 get_db 依赖（例如 sqlite 会话），用于涉及 DB 查询的路径。
    测试结束后清理全部 dependency_overrides。
    """
    from fastapi.testclient import TestClient

    import main as app_main
    from app.core import deps as app_deps

    created: list[tuple] = []

    def _make(*, authenticated: bool = True, db_override=None) -> TestClient:
        app = app_main.app
        app.dependency_overrides.clear()

        if authenticated:
            app.dependency_overrides[app_deps.get_current_user] = _authenticated_user
            app.dependency_overrides[app_deps.get_current_active_user] = (
                _authenticated_user
            )
        if db_override is not None:
            app.dependency_overrides[get_db] = db_override

        client = TestClient(app, raise_server_exceptions=False)
        created.append(app)
        return client

    yield _make

    for app in created:
        app.dependency_overrides.clear()


@pytest.fixture
def client(make_client):
    """默认带鉴权覆盖的 TestClient。"""
    return make_client()
