"""
应用配置
"""
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置类"""
    
    # 应用信息
    APP_NAME: str = "VeWealth A股股票平台API"
    APP_VERSION: str = "1.1.0"
    API_PREFIX: str = "/api"
    
    # CORS配置
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # AKShare配置
    AKSHARE_TIMEOUT: int = 30  # 请求超时时间（秒）
    MAX_SEARCH_RESULTS: int = 20  # 搜索结果最大返回数量
    
    # 数据查询限制（1分钟数据）
    MAX_MINUTE_QUERY_DAYS: int = 999999  # 无限制
    
    # 多线程配置
    MAX_WORKERS: int = 4  # 数据处理最大线程数
    
    class Config:
        case_sensitive = True


# 创建全局配置实例
settings = Settings()

