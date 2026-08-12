"""API路由模块"""

from .stock import router as stock_router
from .auth import router as auth_router
from .watchlist import router as watchlist_router
from .backtest import router as backtest_router
from .alert import router as alert_router
from .screener import router as screener_router
from .health import router as health_router

__all__ = [
    "stock_router",
    "auth_router",
    "watchlist_router",
    "backtest_router",
    "alert_router",
    "screener_router",
    "health_router",
]
