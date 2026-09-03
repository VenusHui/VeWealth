"""策略校验器：静态契约校验 + 运行时参数/模式校验。

- 静态可用性校验（validate_strategy_class）：检查策略类是否完整实现契约。
- 运行时校验（validate_strategy_runtime / validate_strategy_params）：参数类型、
  边界、跨字段关系与模式支持。提交前后共用同一套，保证「合法参数」并非「稳定 0 命中」。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

from app.services.backtest.policies.registry import get_profile, get_policy
from app.services.backtest.strategies.contracts import (
    BaseStrategyV2,
    SUPPORTED_BACKTEST_MODES,
)

REQUIRED_V2_CLASS_METHODS = [
    "param_schema",
    "required_columns",
    "default_policy_profile",
    "generate_candidates",
]

# 数值型参数类型 -> 用于强转的构造器
_NUMERIC_TYPES = {"int", "float"}


@dataclass
class StrategyValidationResult:
    strategy_id: str
    usable: bool
    unusable_reasons: list[str]
    policy_profile: str | None = None
    supported_modes: list[str] | None = None
    min_history_bars: int | None = None
    signal_timestamp: str | None = None
    score_definition: str | None = None
    exit_rule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StrategyValidationError(Exception):
    """携带结构化错误明细的运行时校验异常，供路由映射为 422。"""

    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__("; ".join(e.get("message", "") for e in errors))


@dataclass
class RuntimeValidationResult:
    valid: bool
    errors: list[dict[str, Any]]
    #: 经默认值填充 + 类型强转后的参数，供下游使用
    validated_params: dict[str, Any] = field(default_factory=dict)

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


def _coerce_value(value: Any, ptype: str, key: str, errors: list[dict]) -> Any:
    """按 schema type 强转参数；非数值/类型不匹配时记录错误。"""
    if value is None or value == "":
        return value
    try:
        if ptype == "int":
            if isinstance(value, str) and not value.strip().isdigit():
                raise ValueError("非法整数")
            return int(float(value))
        if ptype == "float":
            if isinstance(value, str) and not (
                value.strip().replace(".", "", 1).isdigit()
            ):
                raise ValueError("非法数值")
            return float(value)
        if ptype in ("str", "string"):
            return str(value)
        return value
    except (ValueError, TypeError):
        errors.append(
            {"field": key, "message": f"参数 {key} 应为 {ptype} 类型，收到 {value!r}"}
        )
        return value


def _check_numeric_bounds(
    key: str, value: float, schema_item: dict, errors: list[dict]
):
    vmin = schema_item.get("min")
    vmax = schema_item.get("max")
    if vmin is not None and value < float(vmin):
        errors.append(
            {"field": key, "message": f"参数 {key} 不能小于 {vmin}，当前 {value}"}
        )
    if vmax is not None and value > float(vmax):
        errors.append(
            {"field": key, "message": f"参数 {key} 不能大于 {vmax}，当前 {value}"}
        )


def _validate_ma_cross_cross_field(params: dict, errors: list[dict]):
    short = params.get("short_window")
    long = params.get("long_window")
    if short is not None and long is not None:
        if short >= long:
            errors.append(
                {
                    "field": "short_window",
                    "message": f"short_window({short}) 必须小于 long_window({long})",
                }
            )


#: 策略级跨字段关系校验（keyed by strategy_id）
_CROSS_FIELD_RULES = {
    "ma_cross_v1": _validate_ma_cross_cross_field,
}


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

        # 策略能力契约字段校验
        supported_modes = getattr(strategy_cls, "supported_modes", None)
        if not isinstance(supported_modes, set) or not supported_modes:
            reasons.append("supported_modes 必须是非空 set")
        elif not supported_modes.issubset(SUPPORTED_BACKTEST_MODES):
            reasons.append(
                f"supported_modes 包含非法模式({sorted(supported_modes - SUPPORTED_BACKTEST_MODES)})"
            )

        min_history_bars = getattr(strategy_cls, "min_history_bars", None)
        if not isinstance(min_history_bars, int) or min_history_bars < 0:
            reasons.append("min_history_bars 必须是非负整数")

        for attr in ("signal_timestamp", "score_definition", "exit_rule"):
            value = getattr(strategy_cls, attr, None)
            if not isinstance(value, str) or not value.strip():
                reasons.append(f"{attr} 不能为空")

        # 根据 supported_modes 校验对应信号生成方法存在
        if isinstance(supported_modes, set):
            if "manual_symbols" in supported_modes and not callable(
                getattr(strategy_cls, "generate_signals", None)
            ):
                reasons.append("支持 manual_symbols 但缺少 generate_signals")
            if "strategy_select" in supported_modes and not callable(
                getattr(strategy_cls, "generate_candidates", None)
            ):
                reasons.append("支持 strategy_select 但缺少 generate_candidates")

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
        supported_modes=(
            sorted(strategy_cls.supported_modes)
            if isinstance(getattr(strategy_cls, "supported_modes", None), set)
            else None
        ),
        min_history_bars=getattr(strategy_cls, "min_history_bars", None),
        signal_timestamp=getattr(strategy_cls, "signal_timestamp", None),
        score_definition=getattr(strategy_cls, "score_definition", None),
        exit_rule=getattr(strategy_cls, "exit_rule", None),
    )


def validate_strategy_params(
    strategy_cls: type, params: dict[str, Any], mode: str | None = None
) -> RuntimeValidationResult:
    """校验给定参数在策略契约下是否合法（类型/边界/跨字段/模式支持）。"""
    errors: list[dict[str, Any]] = []
    supported_modes = getattr(strategy_cls, "supported_modes", SUPPORTED_BACKTEST_MODES)

    if mode is not None and mode not in supported_modes:
        errors.append(
            {
                "field": "mode",
                "message": f"策略 {strategy_cls.strategy_id} 不支持模式 {mode}，"
                f"支持: {sorted(supported_modes)}",
            }
        )

    schema_items: dict[str, dict] = {}
    validated: dict[str, Any] = {}
    try:
        schema = strategy_cls.param_schema()
        schema_items = {item["key"]: item for item in schema}
    except Exception as e:
        errors.append({"field": "schema", "message": f"param_schema 读取失败: {e}"})
        return RuntimeValidationResult(valid=False, errors=errors)

    # 1. 用 schema default 填充缺省值
    for key, item in schema_items.items():
        if key in params and params[key] not in (None, ""):
            validated[key] = params[key]
        elif item.get("required", False) and item.get("default") is not None:
            validated[key] = item["default"]
        elif item.get("required", False) and item.get("default") is None:
            errors.append({"field": key, "message": f"缺少必填参数 {key}"})
        else:
            validated[key] = item.get("default")

    # 2. 类型强转 + 边界校验
    for key, item in schema_items.items():
        raw = validated.get(key)
        ptype = item.get("type", "float")
        coerced = _coerce_value(raw, ptype, key, errors)
        validated[key] = coerced
        if coerced is None or coerced == "" or isinstance(coerced, str):
            continue
        if ptype in _NUMERIC_TYPES:
            _check_numeric_bounds(key, float(coerced), item, errors)

    # 3. 跨字段关系校验
    cross_field = _CROSS_FIELD_RULES.get(strategy_cls.strategy_id)
    if cross_field is not None:
        cross_field(validated, errors)

    return RuntimeValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        validated_params=validated,
    )
