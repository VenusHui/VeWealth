"""
VeWealth A股股票分析平台 - 后端主入口
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.core.logger import get_module_logger
from app.routers import (
    stock_router,
    auth_router,
    watchlist_router,
    backtest_router,
    alert_router,
    screener_router,
)
from app.routers.scheduler import router as scheduler_router
from app.services.scheduler import app_scheduler
from app.services.backtest import backtest_job_manager

# 获取logger
logger = get_module_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    try:
        logger.info("正在初始化数据库...")
        init_db()
        logger.info("数据库初始化完成")

        recovered_count = backtest_job_manager.recover_stale_jobs_on_startup()
        logger.info(f"回测任务重启恢复完成，处理数量: {recovered_count}")

        logger.info("正在启动定时任务调度器...")
        app_scheduler.start()
    except Exception as e:
        logger.error(f"应用启动失败: {str(e)}", exc_info=True)
        raise

    yield

    # 关闭时执行
    try:
        logger.info("正在关闭定时任务调度器...")
        app_scheduler.shutdown()
        logger.info("应用已关闭")
    except Exception as e:
        logger.error(f"应用关闭时出错: {str(e)}", exc_info=True)


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
app.include_router(backtest_router, prefix=settings.API_PREFIX)
app.include_router(alert_router, prefix=settings.API_PREFIX)
app.include_router(scheduler_router, prefix=settings.API_PREFIX)
app.include_router(screener_router, prefix=settings.API_PREFIX)


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
