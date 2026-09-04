"""策略V2契约定义（候选驱动）"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

REQUIRED_CANDIDATE_COLUMNS: set[str] = {"trade_date", "symbol"}

# 回测支持的运行模式
SUPPORTED_BACKTEST_MODES: set[str] = {"manual_symbols", "strategy_select"}


class BaseStrategyV2(ABC):
    """策略V2基类：仅负责生成候选，不负责组合执行决策。"""

    strategy_id: str = "base_v2"
    name: str = "Base V2"
    description: str = "策略V2基类"

    # ------------------------------------------------------------------
    # 策略能力契约：声明策略真实可跑的模式、历史精度、信号语义，
    # 供运行时校验、前端禁用与取数 warmup 使用。
    # ------------------------------------------------------------------

    #: 该策略支持的回测模式集合（SUPPORTED_BACKTEST_MODES 子集）
    supported_modes: set[str] = SUPPORTED_BACKTEST_MODES

    #: 生成首个信号所需的最小历史 bar 数（不含额外 warmup 缓冲）。
    #: 取数层会在此之上追加 warmup，确保长回看配置（如 MA 240、GMM 250）吃到足量 bar。
    min_history_bars: int = 0

    #: 信号指向的时间语义：next_open / close / open。用于前端与文档理解触发时点。
    signal_timestamp: str = "next_open"

    #: signal_strength 的计算口径说明（human-readable）。
    score_definition: str = "signal_strength"

    #: signal_strength 的理论取值范围 (min, max)，用于把原始强度归一化为 [0,1] 策略评分。
    #: hi <= lo（如恒定 1.0 的二元命中）表示无强度差异，normalize 时视为满档。
    score_range: tuple[float, float] | None = None

    #: 平仓/退出规则说明（human-readable），区分手动卖出信号与固定持有。
    exit_rule: str = "hold_days"

    @classmethod
    @abstractmethod
    def param_schema(cls) -> list[dict]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def required_columns(cls) -> set[str]:
        """回测运行前必须存在的行情列。"""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def default_policy_profile(cls) -> str:
        """默认绑定的 policy profile id。"""
        raise NotImplementedError

    @abstractmethod
    def generate_candidates(
        self, market_df: pd.DataFrame, params: dict
    ) -> pd.DataFrame:
        """
        返回候选DataFrame。至少包含 REQUIRED_CANDIDATE_COLUMNS。
        推荐列：signal_strength, reason, meta。
        """
        raise NotImplementedError
