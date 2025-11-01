"""API路由模块"""

from .stock import router as stock_router
from .auth import router as auth_router
from .watchlist import router as watchlist_router

__all__ = ["stock_router", "auth_router", "watchlist_router"]

