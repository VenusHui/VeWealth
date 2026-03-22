"""Policy 五件套抽象接口"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class PolicyContext:
    """策略执行上下文（MVP 可按需扩展）。"""

    strategy_id: str
    params: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


class RankingPolicy(ABC):
    policy_id: str = "base_ranking"

    @abstractmethod
    def rank(self, candidates_df: pd.DataFrame, context: PolicyContext) -> pd.DataFrame:
        raise NotImplementedError


class SelectionPolicy(ABC):
    """必须定义同日多标的冲突解决规则（如 Top-K/阈值）。"""

    policy_id: str = "base_selection"

    @abstractmethod
    def select(
        self,
        ranked_df: pd.DataFrame,
        portfolio_state: dict[str, Any],
        context: PolicyContext,
    ) -> pd.DataFrame:
        raise NotImplementedError


class AllocationPolicy(ABC):
    policy_id: str = "base_allocation"

    @abstractmethod
    def allocate(
        self,
        selected_df: pd.DataFrame,
        equity: float,
        risk_state: dict[str, Any],
        context: PolicyContext,
    ) -> pd.DataFrame:
        raise NotImplementedError


class RiskPolicy(ABC):
    policy_id: str = "base_risk"

    @abstractmethod
    def check_pre_trade(
        self,
        orders_df: pd.DataFrame,
        portfolio_state: dict[str, Any],
        context: PolicyContext,
    ) -> pd.DataFrame:
        raise NotImplementedError


class ExecutionPolicy(ABC):
    policy_id: str = "base_execution"

    @abstractmethod
    def simulate_fill(
        self,
        orders_df: pd.DataFrame,
        bar_df: pd.DataFrame,
        cost_model: Any,
        context: PolicyContext,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError
