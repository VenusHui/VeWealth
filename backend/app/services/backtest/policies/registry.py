"""Policy 与 Profile 注册表（P0骨架）"""

from __future__ import annotations

from typing import Any

from app.services.backtest.policies.base import (
    AllocationPolicy,
    ExecutionPolicy,
    RankingPolicy,
    RiskPolicy,
    SelectionPolicy,
)
from app.services.backtest.policies.profiles import POLICY_PROFILES, PolicyProfile


class _PlaceholderRanking(RankingPolicy):
    policy_id = "signal_then_liquidity_v1"

    def rank(self, candidates_df, context):
        return candidates_df


class _PlaceholderSelection(SelectionPolicy):
    policy_id = "top_k_v1"

    def select(self, ranked_df, portfolio_state, context):
        return ranked_df


class _PlaceholderAllocation(AllocationPolicy):
    policy_id = "equal_weight_v1"

    def allocate(self, selected_df, equity, risk_state, context):
        return selected_df


class _PlaceholderRisk(RiskPolicy):
    policy_id = "cn_a_basic_risk_v1"

    def check_pre_trade(self, orders_df, portfolio_state, context):
        return orders_df


class _PlaceholderExecution(ExecutionPolicy):
    policy_id = "cn_a_t1_open_fill_v1"

    def simulate_fill(self, orders_df, bar_df, cost_model, context):
        return []


POLICY_REGISTRY: dict[str, type] = {
    _PlaceholderRanking.policy_id: _PlaceholderRanking,
    _PlaceholderSelection.policy_id: _PlaceholderSelection,
    _PlaceholderAllocation.policy_id: _PlaceholderAllocation,
    _PlaceholderRisk.policy_id: _PlaceholderRisk,
    _PlaceholderExecution.policy_id: _PlaceholderExecution,
}


def get_policy(policy_id: str) -> Any:
    cls = POLICY_REGISTRY.get(policy_id)
    if not cls:
        raise ValueError(f"未知 policy: {policy_id}")
    return cls()


def get_profile(profile_id: str) -> PolicyProfile:
    profile = POLICY_PROFILES.get(profile_id)
    if not profile:
        raise ValueError(f"未知 policy profile: {profile_id}")
    return profile


def resolve_profile(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    return {
        "profile": profile,
        "ranking": get_policy(profile.ranking_policy),
        "selection": get_policy(profile.selection_policy),
        "allocation": get_policy(profile.allocation_policy),
        "risk": get_policy(profile.risk_policy),
        "execution": get_policy(profile.execution_policy),
    }
