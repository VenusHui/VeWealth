"""策略V2契约定义（候选驱动）"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

REQUIRED_CANDIDATE_COLUMNS: set[str] = {"trade_date", "symbol"}


class BaseStrategyV2(ABC):
    """策略V2基类：仅负责生成候选，不负责组合执行决策。"""

    strategy_id: str = "base_v2"
    name: str = "Base V2"
    description: str = "策略V2基类"

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
