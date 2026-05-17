"""
应用配置 - 支持多环境配置
"""

import os
import logging
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings
import json

# 使用标准logging，因为logger模块依赖config
logger = logging.getLogger("vewealth.config")


def get_env_file() -> str:
    """
    根据 ENV 环境变量获取对应的配置文件路径

    Returns:
        str: 环境配置文件路径
    """
    env = os.getenv("ENV", "local")  # 默认使用 local 环境
    base_dir = Path(__file__).parent.parent.parent  # backend/
    env_file = base_dir / "settings" / f".{env}.env"

    if not env_file.exists():
        logger.warning(f"环境配置文件 {env_file} 不存在，将使用默认配置")
        return ""

    logger.info(f"加载环境配置: {env_file} (ENV={env})")
    return str(env_file)


class Settings(BaseSettings):
    """应用配置类 - 支持多环境"""

    # 环境标识
    ENV: str = "local"

    # 应用信息
    APP_NAME: str = "VeWealth A股股票平台API"
    APP_VERSION: str = "1.1.0"
    API_PREFIX: str = "/api"

    # CORS配置 (支持 JSON 字符串或列表)
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8001

    # 数据库配置
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/vewealth"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # JWT配置
    SECRET_KEY: str = "default_secret_key_please_change_in_production"
    MASTER_KEY: str = "default_master_key_please_change"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 1天

    # 微信公众号配置
    WECHAT_APP_ID: str = ""
    WECHAT_APP_SECRET: str = ""
    WECHAT_TOKEN: str = ""
    WECHAT_ENCODING_AES_KEY: str = ""

    # AKShare配置
    AKSHARE_TIMEOUT: int = 30
    AKSHARE_DATA_RETENTION_DAYS: int = 5
    MAX_SEARCH_RESULTS: int = 20

    # Tushare 备源配置
    TUSHARE_ENABLED: bool = True
    TUSHARE_TOKEN: str = ""
    TUSHARE_TIMEOUT: int = 30
    TUSHARE_RETRY_TIMES: int = 2

    # 数据查询限制（999999 表示不限制）
    MAX_MINUTE_QUERY_DAYS: int = 999999

    # 多线程配置
    MAX_WORKERS: int = 4

    # 定时任务配置
    SCHEDULER_ENABLED: bool = True
    DATA_COLLECT_CRON: str = "0 20 * * 1-5"  # 每周一到周五的20:00执行
    ALERT_CHECK_CRON: str = "*/5 9-15 * * 1-5"

    # 预警配置
    DEFAULT_ALERT_THRESHOLD: float = 0.7

    class Config:
        case_sensitive = True
        env_file = get_env_file()
        env_file_encoding = "utf-8"
        extra = "ignore"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 如果 CORS_ORIGINS 是 JSON 字符串，解析它
        if isinstance(self.CORS_ORIGINS, str):
            try:
                self.CORS_ORIGINS = json.loads(self.CORS_ORIGINS)
            except json.JSONDecodeError:
                logger.warning("CORS_ORIGINS 格式错误，使用默认值")
                self.CORS_ORIGINS = ["http://localhost:3000"]


# 创建全局配置实例
settings = Settings()
