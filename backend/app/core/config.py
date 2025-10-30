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
    PORT: int = 8001
    
    # 数据库配置
    DATABASE_URL: str = "postgresql://postgres:Hh20011207_@124.221.239.27:5432/vewealth"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    
    # JWT配置
    SECRET_KEY: str = "5o1seto5kWhHD+GLSeyUeSQM/jzwelkPEP/FmJ9oJjk="  # 生产环境请修改
    MASTER_KEY: str = "abcdefg"  # 主密钥，用于生成用户token
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200  # 30天
    
    # 微信公众号配置
    WECHAT_APP_ID: str = "wx449dbfbd2292060c"  # 微信公众号 AppID
    WECHAT_APP_SECRET: str = "422799b769b2be16a96ce811ee32ecdf"  # 微信公众号 AppSecret
    WECHAT_TOKEN: str = ""  # 微信公众号 Token
    WECHAT_ENCODING_AES_KEY: str = "DYsrDlUHbJ5mZUbI4H2JDkG9PINyRuIAGYy1Iunurb0"  # 微信公众号 EncodingAESKey
    
    # AKShare配置
    AKSHARE_TIMEOUT: int = 30  # 请求超时时间（秒）
    MAX_SEARCH_RESULTS: int = 20  # 搜索结果最大返回数量
    AKSHARE_DATA_RETENTION_DAYS: int = 5  # akshare分时数据保留天数限制
    
    # 数据查询限制（1分钟数据）
    MAX_MINUTE_QUERY_DAYS: int = 999999  # 无限制
    
    # 多线程配置
    MAX_WORKERS: int = 4  # 数据处理最大线程数
    
    # 定时任务配置
    SCHEDULER_ENABLED: bool = True  # 是否启用定时任务
    DATA_COLLECT_CRON: str = "0 15 * * 1-5"  # 每个交易日15:00采集数据（周一到周五）
    ALERT_CHECK_CRON: str = "*/5 9-15 * * 1-5"  # 交易时间每5分钟检查一次预警
    
    # 预警配置
    DEFAULT_ALERT_THRESHOLD: float = 0.7  # 默认预警阈值（70%）
    
    class Config:
        case_sensitive = True
        env_file = ".env"


# 创建全局配置实例
settings = Settings()

