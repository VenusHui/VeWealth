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

    # mootdx 镜像候选机制（VEW-55）
    # 逗号分隔的 "ip:port"（或裸 ip，默认 7709 端口）。非空时覆盖内置 curated 列表，
    # 便于运维在镜像失效时无需改代码即可换源。内置列表失效时会触发有界的
    # 公开镜像扫描（见 astock_provider._mootdx_scan_candidates）。
    MOOTDX_SERVERS: str = ""
    # 每次公开镜像扫描最多探测的镜像数；0 表示禁用扫描。
    # 扫描在 init 热路径内同步执行（每个镜像约 2 个周期 + 5s 建连超时），
    # 默认限 5 个以控制最坏延迟；镜像恢复后 curated/discovered 会优先命中。
    MOOTDX_SCAN_LIMIT: int = 5
    # 两次公开镜像扫描之间的最小间隔（秒），避免镜像全挂时每次请求都做全量扫描。
    MOOTDX_SCAN_COOLDOWN: int = 1800

    # 分钟级回测 P0：本地分钟库 + 每日增量采集（VEW-64）
    # 分钟库根目录。容器内 /app/data 是持久卷（vewealth-backend-data），重部署不丢；
    # 本地开发相对 backend/ 解析。
    MINUTE_LIBRARY_DIR: str = "data/minute_bars"
    # 默认开启：分钟库是「现在不采、以后补不回来」的数据，每个交易日漏采即永久缺失，
    # 所以交付物必须默认生效。首轮请有人看日志确认 elapsed_sec（夜间窗口余量）与磁盘
    # 余量（≈13 GB/年/周期）；写入幂等可重入、断点续采、source_probe / failed /
    # elapsed_sec 均已进日志。若要改成「首次上线先关着」，置 False 并指定谁在何时打开。
    MINUTE_COLLECT_ENABLED: bool = True
    # 收盘后采集：晚于日线采集（20:00）与 universe 快照（20:40），
    # 保证当日股票池快照已落盘，采集按点状态选池。
    MINUTE_COLLECT_CRON: str = "0 21 * * 1-5"
    # 采集周期（分钟，逗号分隔），如 "1" 或 "1,5"。全市场 1min 单日 ≈ 1.4M 根。
    MINUTE_COLLECT_PERIODS: str = "1"
    # 并发取数线程数。共享 mootdx client 的取数由取数锁串行化（VEW-60），并发主要
    # 摊薄建连与解析开销，不改变网络串行事实。**不要调高**：线程只是在锁上排队，
    # 而源级探针（source_health）等锁的超时是 5s（_MOOTDX_PROBE_LOCK_TIMEOUT）——
    # 20 个线程排队时探针平均要等 ~12s，会在整个采集窗口内一直拿不到锁，
    # 使镜像熔断/恢复（VEW-62）失去健康信号。4 个线程的排队期望 ~2.4s，探针可用。
    MINUTE_COLLECT_WORKERS: int = 4
    # 单标的取数墙钟预算（秒）。批量任务无前端 15s 约束，比分钟链路的 12s 宽松，
    # 给镜像慢但可用的标的留余量。
    MINUTE_COLLECT_FETCH_BUDGET: float = 20.0
    # 每采集 N 个标的落盘一次并推进断点（限制内存峰值与重跑代价）。
    MINUTE_COLLECT_FLUSH_EVERY: int = 500
    # 单轮采集里「空结果」占全市场的比例上限。超过即认为源只坏了一部分（探针恰好
    # 落在好的那部分），本轮空结果改记可重试失败。正常日空结果只有个位数百分比
    # （停牌 / 退市），50% 不会误触发。
    MINUTE_COLLECT_EMPTY_RATIO_LIMIT: float = 0.5

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
