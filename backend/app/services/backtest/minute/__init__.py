"""分钟级回测子系统（VEW-66 P2）。

与日线回测链（``backtest.engine`` / ``backtest.service``）同构、契约一致，差别只在
bar 粒度与数据入口：日线走本地/缓存链路，分钟走**纯网络按需取数**。

模块划分：

- ``depth``：各源滚动窗口登记与「超深度」裁决（strict / truncate）。
- ``session``：A 股交易时段、集合竞价、停牌判定。
- ``data``：按需取数 + 结构化 provenance（取数时间、实际返回区间、深度截断）。
- ``alignment``：基准与因子对齐到分钟 bar（显式区分有无前视）。
- ``engine``：逐分钟 bar 的组合撮合（滑点/涨跌停/停牌/集合竞价/T+1/止损止盈）。
- ``service``：编排入口 :func:`run_minute_backtest`。
"""

from app.services.backtest.minute.depth import (
    SUPPORTED_PERIODS,
    MinuteDepthExceededError,
    depth_report,
    effective_depth_days,
    normalize_period,
    resolve_request_window,
    source_depth_days,
)
from app.services.backtest.minute.engine import (
    MinuteExecutionConfig,
    run_minute_portfolio,
    session_eod_curve,
)
from app.services.backtest.minute.service import (
    align_factor_to_minute,
    estimate_fetch_bars,
    run_minute_backtest,
)

__all__ = [
    "SUPPORTED_PERIODS",
    "MinuteDepthExceededError",
    "MinuteExecutionConfig",
    "align_factor_to_minute",
    "depth_report",
    "effective_depth_days",
    "estimate_fetch_bars",
    "normalize_period",
    "resolve_request_window",
    "run_minute_backtest",
    "run_minute_portfolio",
    "session_eod_curve",
    "source_depth_days",
]
