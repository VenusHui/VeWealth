"""数据源提供者注册与工厂"""

from __future__ import annotations

from typing import Dict, Type

from app.core.config import settings
from app.providers.base import MarketDataProvider

_provider_registry: Dict[str, Type[MarketDataProvider]] = {}
_provider_instances: Dict[str, MarketDataProvider] = {}


def register_provider(name: str, provider_cls: Type[MarketDataProvider]) -> None:
    _provider_registry[name] = provider_cls


def get_data_provider(name: str | None = None) -> MarketDataProvider:
    key = name or settings.DATA_PROVIDER
    if key not in _provider_instances:
        if key not in _provider_registry:
            raise ValueError(f"未知数据源: {key}，已注册: {list(_provider_registry)}")
        _provider_instances[key] = _provider_registry[key]()
    return _provider_instances[key]


# 自动注册内置数据源
from app.providers.astock_provider import AStockDataProvider  # noqa: E402

register_provider("astock", AStockDataProvider)
# 兼容旧配置
register_provider("akshare", AStockDataProvider)
