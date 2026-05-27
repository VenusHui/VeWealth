"""策略静态可用性校验器"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from app.services.backtest.policies.registry import get_profile, get_policy
from app.services.backtest.strategies.contracts import BaseStrategyV2

REQUIRED_V2_CLASS_METHODS = [
    "param_schema",
    "required_columns",
    "default_policy_profile",
]


@dataclass
class StrategyValidationResult:
    strategy_id: str
    usable: bool
    unusable_reasons: list[str]
    policy_profile: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check_policy_profile(profile_id: str, reasons: list[str]):
    try:
        profile = get_profile(profile_id)
    except Exception as e:
        reasons.append(f"policy_profile 不可用: {e}")
        return

    checks = {
        "ranking_policy": profile.ranking_policy,
        "selection_policy": profile.selection_policy,
        "allocation_policy": profile.allocation_policy,
        "risk_policy": profile.risk_policy,
        "execution_policy": profile.execution_policy,
    }
    for field, policy_id in checks.items():
        try:
            policy = get_policy(policy_id)
            if field == "selection_policy" and not getattr(
                policy, "allow_same_day_multi", False
            ):
                reasons.append("selection_policy 缺少 allow_same_day_multi 声明")
        except Exception as e:
            reasons.append(f"{field} 不可用({policy_id}): {e}")


def validate_strategy_class(strategy_cls: type) -> StrategyValidationResult:
    strategy_id = str(getattr(strategy_cls, "strategy_id", "<unknown>"))
    reasons: list[str] = []
    policy_profile: str | None = None

    if not getattr(strategy_cls, "name", None):
        reasons.append("缺少 name")
    if not getattr(strategy_cls, "description", None):
        reasons.append("缺少 description")

    is_v2 = issubclass(strategy_cls, BaseStrategyV2)
    if not is_v2:
        reasons.append("未实现 BaseStrategyV2 契约")
    else:
        for m in REQUIRED_V2_CLASS_METHODS:
            if not callable(getattr(strategy_cls, m, None)):
                reasons.append(f"缺少方法: {m}")

        try:
            schema = strategy_cls.param_schema()  # type: ignore[misc]
            if not isinstance(schema, list):
                reasons.append("param_schema 必须是 list")
        except Exception as e:
            reasons.append(f"param_schema 非法: {e}")

        try:
            required_cols = strategy_cls.required_columns()  # type: ignore[misc]
            if not isinstance(required_cols, set):
                reasons.append("required_columns 必须是 set")
            elif not required_cols:
                reasons.append("required_columns 不能为空")
        except Exception as e:
            reasons.append(f"required_columns 非法: {e}")

        try:
            policy_profile = strategy_cls.default_policy_profile()  # type: ignore[misc]
            if not policy_profile:
                reasons.append("default_policy_profile 不能为空")
            else:
                _check_policy_profile(policy_profile, reasons)
        except Exception as e:
            reasons.append(f"default_policy_profile 非法: {e}")

    return StrategyValidationResult(
        strategy_id=strategy_id,
        usable=len(reasons) == 0,
        unusable_reasons=reasons,
        policy_profile=policy_profile,
    )
