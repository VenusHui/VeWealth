"""策略注册表"""

from app.services.backtest.strategies.ma_cross_v1 import MACrossV1Strategy
from app.services.backtest.strategies.volume_shrink_drop_v1 import (
    VolumeShrinkDropV1Strategy,
)
from app.services.backtest.validators.strategy_validator import validate_strategy_class

STRATEGY_REGISTRY = {
    MACrossV1Strategy.strategy_id: MACrossV1Strategy,
    VolumeShrinkDropV1Strategy.strategy_id: VolumeShrinkDropV1Strategy,
}


def get_strategy_validation(strategy_id: str) -> dict:
    strategy_cls = STRATEGY_REGISTRY.get(strategy_id)
    if not strategy_cls:
        return {
            "strategy_id": strategy_id,
            "usable": False,
            "unusable_reasons": [f"未知策略: {strategy_id}"],
            "policy_profile": None,
        }
    return validate_strategy_class(strategy_cls).to_dict()


def list_strategies(include_unusable: bool = True) -> list[dict]:
    items = []
    for strategy_id, strategy_cls in STRATEGY_REGISTRY.items():
        validation = validate_strategy_class(strategy_cls).to_dict()
        if not include_unusable and not validation["usable"]:
            continue
        items.append(
            {
                "strategy_id": strategy_id,
                "name": strategy_cls.name,
                "description": strategy_cls.description,
                "param_schema": strategy_cls.param_schema(),
                "usable": validation["usable"],
                "unusable_reasons": validation["unusable_reasons"],
                "policy_profile": validation["policy_profile"],
            }
        )
    return items


def get_strategy(strategy_id: str, require_usable: bool = False):
    strategy_cls = STRATEGY_REGISTRY.get(strategy_id)
    if not strategy_cls:
        raise ValueError(f"未知策略: {strategy_id}")

    validation = validate_strategy_class(strategy_cls)
    if require_usable and not validation.usable:
        reason = "; ".join(validation.unusable_reasons) or "策略不可用"
        raise ValueError(f"策略不可用({strategy_id}): {reason}")

    return strategy_cls()
