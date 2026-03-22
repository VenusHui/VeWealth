"""Policy Profile 定义"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyProfile:
    profile_id: str
    ranking_policy: str
    selection_policy: str
    allocation_policy: str
    risk_policy: str
    execution_policy: str


POLICY_PROFILES: dict[str, PolicyProfile] = {
    "vsd_v1_default": PolicyProfile(
        profile_id="vsd_v1_default",
        ranking_policy="signal_then_liquidity_v1",
        selection_policy="top_k_v1",
        allocation_policy="equal_weight_v1",
        risk_policy="cn_a_basic_risk_v1",
        execution_policy="cn_a_t1_open_fill_v1",
    )
}
