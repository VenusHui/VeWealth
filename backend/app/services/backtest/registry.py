"""策略注册表"""

from app.services.backtest.strategies.ma_cross_v1 import MACrossV1Strategy

STRATEGY_REGISTRY = {
    MACrossV1Strategy.strategy_id: MACrossV1Strategy,
}


def list_strategies() -> list[dict]:
    items = []
    for strategy_id, strategy_cls in STRATEGY_REGISTRY.items():
        items.append(
            {
                "strategy_id": strategy_id,
                "name": strategy_cls.name,
                "description": strategy_cls.description,
                "param_schema": strategy_cls.param_schema(),
            }
        )
    return items


def get_strategy(strategy_id: str):
    strategy_cls = STRATEGY_REGISTRY.get(strategy_id)
    if not strategy_cls:
        raise ValueError(f"未知策略: {strategy_id}")
    return strategy_cls()
