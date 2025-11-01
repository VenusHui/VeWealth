"""
VeWealth A股股票分析平台 - 后端主入口
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.routers import stock_router, auth_router, watchlist_router
from app.services.scheduler import app_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    print("[应用] 正在初始化数据库...")
    init_db()
    print("[应用] 数据库初始化完成")

    print("[应用] 正在启动定时任务调度器...")
    app_scheduler.start()

    yield

    # 关闭时执行
    print("[应用] 正在关闭定时任务调度器...")
    app_scheduler.shutdown()


# 创建FastAPI应用实例
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="实时A股股票数据查询与分析平台API，支持监控列表、价格预警和微信通知",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# 配置CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(stock_router, prefix=settings.API_PREFIX)
app.include_router(auth_router, prefix=settings.API_PREFIX)
app.include_router(watchlist_router, prefix=settings.API_PREFIX)


@app.get("/")
async def root():
    """根路径，返回API信息"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_level="info")
