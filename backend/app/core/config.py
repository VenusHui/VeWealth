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
    APP_VERSION: str = "1.2.0"
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

    # 数据源配置
    DATA_PROVIDER: str = "astock"

    # 搜索配置
    MAX_SEARCH_RESULTS: int = 20

    # Tushare 备源配置
    TUSHARE_ENABLED: bool = True
    TUSHARE_TOKEN: str = ""
    TUSHARE_TIMEOUT: int = 30
    TUSHARE_RETRY_TIMES: int = 2
    # adj_factor 接口配额极低（5次/天 + 1次/分钟 + 1次/小时），qfq/hfq 日线依赖它。
    # 通过缓存 + 配额管理避免常规请求撞限；耗尽时降级为非复权（VEW-55）。
    TUSHARE_ADJ_FACTOR_DAILY_QUOTA: int = 5
    TUSHARE_ADJ_FACTOR_MIN_INTERVAL: int = 60  # 两次拉取的最小间隔（秒）
    TUSHARE_ADJ_FACTOR_CACHE_DIR: str = "data/tushare_adj"
    # 复权因子缓存新鲜度：超过该天数视为陈旧，配额允许时在请求路径上顺手刷新；
    # 配额耗尽时仍返回陈旧缓存，但通过 adjust_factor_date 暴露缓存日期（VEW-55）。
    TUSHARE_ADJ_FACTOR_CACHE_TTL_DAYS: int = 7

    # 数据查询限制（999999 表示不限制）
    MAX_MINUTE_QUERY_DAYS: int = 999999

    # 多线程配置
    MAX_WORKERS: int = 4

    # 后台任务并发上限（选股与回测共享的有界 worker 池）
    MAX_TASK_WORKERS: int = 4

    # 定时任务配置
    SCHEDULER_ENABLED: bool = True
    DATA_COLLECT_CRON: str = "0 20 * * 1-5"  # 每周一到周五的20:00执行
    ALERT_CHECK_CRON: str = "*/5 9-15 * * 1-5"
    # 每个交易日收盘后落一次 point-in-time universe 快照（累积历史 ST/退市信息）
    UNIVERSE_SNAPSHOT_CRON: str = "40 20 * * 1-5"

    # 数据源健康检查 / 监控配置
    SOURCE_HEALTH_PROBE_CRON: str = "*/5 * * * *"  # 源级探针运行周期（每5分钟）
    SOURCE_HEALTH_EVENT_LIMIT: int = 200  # 降级事件环形缓冲上限
    SOURCE_HEALTH_FAIL_THRESHOLD: int = 3  # 连续失败升级为 ERROR 告警的阈值
    SOURCE_HEALTH_PROBE_SYMBOL: str = "000001"  # 探针使用的样本股票代码

    # mootdx 镜像候选机制（VEW-55 / VEW-62）
    # 逗号分隔的 "ip:port"（或裸 ip，默认 7709 端口）。非空时**完整覆盖**内置
    # curated 列表：用于镜像池整体失效时的应急止血，代价是把主源收敛到配置的
    # 这几个镜像（单点）。要在保留内置列表的前提下追加镜像，用
    # MOOTDX_EXTRA_SERVERS。内置列表失效时会触发有界的公开镜像扫描
    # （见 astock_provider._mootdx_scan_candidates）。
    MOOTDX_SERVERS: str = ""
    # 逗号分隔的 "ip:port"，**追加**在内置 curated 列表之前（不覆盖）。用于把实测
    # 可用的外部镜像纳入候选池，同时保留 curated 作为回退（VEW-62）。镜像池是
    # 公开资源、会整体漂移，这条配置让运维能扩充池子而不必收敛到单点。
    MOOTDX_EXTRA_SERVERS: str = ""
    # 每次公开镜像扫描最多探测的镜像数；0 表示禁用扫描。
    # 候选先经一次并发 TCP 可达性预筛（VEW-62），死镜像在预筛阶段被剪掉，不再
    # 逐个吃满建连超时；因此默认放开到覆盖整个内置池（38 个），一轮扫描即可扫完
    # 全部候选，而不是像以前那样每轮只推进 5 个。
    MOOTDX_SCAN_LIMIT: int = 40
    # 两次公开镜像扫描之间的最小间隔（秒），避免镜像全挂时每次请求都做全量扫描。
    # 配合上面的快速失败，一轮扫描成本已从「数十秒」降到秒级，故由 1800s 收紧到
    # 300s，使镜像池的变化能在分钟级被感知。
    MOOTDX_SCAN_COOLDOWN: int = 300

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
