"""策略注册表"""

from pathlib import Path

from app.services.backtest.strategies.ma_cross_v1 import MACrossV1Strategy
from app.services.backtest.strategies.volume_shrink_drop_v1 import (
    VolumeShrinkDropV1Strategy,
)
from app.services.backtest.strategies.gmm_volume_v1 import GMMVolumeV1Strategy
from app.services.backtest.validators.strategy_validator import (
    StrategyValidationError,
    validate_strategy_class,
    validate_strategy_params,
)

STRATEGY_REGISTRY = {
    MACrossV1Strategy.strategy_id: MACrossV1Strategy,
    VolumeShrinkDropV1Strategy.strategy_id: VolumeShrinkDropV1Strategy,
    GMMVolumeV1Strategy.strategy_id: GMMVolumeV1Strategy,
}

STRATEGY_SOURCE_PATHS = {
    MACrossV1Strategy.strategy_id: str(
        Path(__file__).parent / "strategies" / "ma_cross_v1.py"
    ),
    VolumeShrinkDropV1Strategy.strategy_id: str(
        Path(__file__).parent / "strategies" / "volume_shrink_drop_v1.py"
    ),
    GMMVolumeV1Strategy.strategy_id: str(
        Path(__file__).parent / "strategies" / "gmm_volume_v1.py"
    ),
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
                "supported_modes": validation.get("supported_modes"),
                "min_history_bars": validation.get("min_history_bars"),
                "signal_timestamp": validation.get("signal_timestamp"),
                "score_definition": validation.get("score_definition"),
                "score_range": validation.get("score_range"),
                "exit_rule": validation.get("exit_rule"),
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


def validate_strategy_runtime(strategy_id: str, params: dict, mode: str | None = None):
    """提交前后共用同一套运行时校验。

    返回 (strategy_instance, validated_params)；校验失败抛 StrategyValidationError，
    便于路由层映射为 422，并在运行层兜底拦截。
    """
    strategy_cls = STRATEGY_REGISTRY.get(strategy_id)
    if not strategy_cls:
        raise StrategyValidationError(
            [{"field": "strategy_id", "message": f"未知策略: {strategy_id}"}]
        )

    class_result = validate_strategy_class(strategy_cls)
    if not class_result.usable:
        reason = "; ".join(class_result.unusable_reasons) or "策略不可用"
        raise StrategyValidationError(
            [
                {
                    "field": "strategy_id",
                    "message": f"策略不可用({strategy_id}): {reason}",
                }
            ]
        )

    param_result = validate_strategy_params(strategy_cls, params, mode)
    if not param_result.valid:
        raise StrategyValidationError(param_result.errors)

    return strategy_cls, param_result.validated_params


def get_strategy_source_path(strategy_id: str) -> str | None:
    return STRATEGY_SOURCE_PATHS.get(strategy_id)
