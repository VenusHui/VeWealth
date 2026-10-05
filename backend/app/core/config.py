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

    # 分钟级回测 P0：本地分钟库 + 每日增量采集（VEW-64）
    # 分钟库根目录。容器内 /app/data 是持久卷（vewealth-backend-data），重部署不丢；
    # 本地开发相对 backend/ 解析。
    MINUTE_LIBRARY_DIR: str = "data/minute_bars"
    # 默认关闭（VEW-64 裁定）：生产磁盘余量不足，首次上线不得自动开跑。
    # 实测（2026-10-04，ssh tencent-ollama）：/ 为单块 40 GB 盘、仅剩 5.6 GB，而分钟库
    # ≈13 GB/年/周期 —— 开跑后约 107 天写满 /，写满的是系统盘，postgres + backend +
    # frontend 会一起挂，不只是分钟库单独失败。且 PR base 为 dev/**，合并即触发生产部署，
    # 带着 True 合进去当晚 21:00 就会开跑。
    #
    # 打开条件（可机械判定）：生产 / 可用空间 ≥ 20 GB（≈13 GB/年 + ~7 GB 余量）。
    # 谁在何时打开：由队长 Venus 在容量满足后打开（扩盘 / 挂数据盘，或按裁定先回收
    # docker build cache、npm cache 等腾出空间），并**手工 force=True 跑首轮**（不走 cron）、
    # 亲自盯日志确认 elapsed_sec（夜间窗口余量）与盘余量，确认无误后再交由
    # MINUTE_COLLECT_CRON 自动接管。
    #
    # 不要「临时先关着」当默认：本系统没有自动重跑机制、调度只针对 date.today()，
    # 漏采的交易日不可补回，长期关闭等于 P0 交付物不生效 —— 必须由上述责任人显式打开。
    MINUTE_COLLECT_ENABLED: bool = False
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
