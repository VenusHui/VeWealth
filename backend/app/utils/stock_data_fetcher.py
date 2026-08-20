"""
DEPRECATED: 此模块已迁移到 app.providers 包。
新代码请使用:
    from app.providers import get_data_provider
    provider = get_data_provider()
"""

import warnings

from app.providers import get_data_provider  # noqa: F401

warnings.warn(
    "stock_data_fetcher 已废弃，请使用 app.providers.get_data_provider()",
    DeprecationWarning,
    stacklevel=2,
)

# 向后兼容：保留旧全局名称
stock_data_fetcher = get_data_provider()
