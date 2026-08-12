"""测试回测策略注册表"""

import unittest

from app.services.backtest.registry import (
    STRATEGY_REGISTRY,
    list_strategies,
    get_strategy,
    get_strategy_source_path,
    get_strategy_validation,
)
from app.services.backtest.strategies.ma_cross_v1 import MACrossV1Strategy
from app.services.backtest.strategies.volume_shrink_drop_v1 import (
    VolumeShrinkDropV1Strategy,
)


class StrategyRegistryTests(unittest.TestCase):
    def test_registry_contains_both_strategies(self):
        self.assertIn("ma_cross_v1", STRATEGY_REGISTRY)
        self.assertIn("volume_shrink_drop_v1", STRATEGY_REGISTRY)
        self.assertIs(STRATEGY_REGISTRY["ma_cross_v1"], MACrossV1Strategy)
        self.assertIs(
            STRATEGY_REGISTRY["volume_shrink_drop_v1"], VolumeShrinkDropV1Strategy
        )

    def test_list_strategies_returns_all(self):
        result = list_strategies()
        self.assertEqual(len(result), 3)
        ids = {s["strategy_id"] for s in result}
        self.assertSetEqual(
            ids, {"ma_cross_v1", "volume_shrink_drop_v1", "gmm_volume_v1"}
        )

    def test_list_strategies_fields(self):
        for s in list_strategies():
            self.assertIn("strategy_id", s)
            self.assertIn("name", s)
            self.assertIn("description", s)
            self.assertIn("param_schema", s)
            self.assertIsInstance(s["param_schema"], list)
            self.assertIn("usable", s)
            self.assertIn("unusable_reasons", s)
            self.assertIn("policy_profile", s)

    def test_list_strategies_usable_only(self):
        result = list_strategies(include_unusable=False)
        for s in result:
            self.assertTrue(s["usable"], msg=f"{s['strategy_id']} should be usable")

    def test_get_strategy_valid(self):
        for sid in ("ma_cross_v1", "volume_shrink_drop_v1"):
            instance = get_strategy(sid)
            self.assertEqual(instance.strategy_id, sid)

    def test_get_strategy_unknown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            get_strategy("nonexistent_strategy")
        self.assertIn("未知策略", str(ctx.exception))

    def test_get_strategy_require_usable(self):
        instance = get_strategy("ma_cross_v1", require_usable=True)
        self.assertIsNotNone(instance)

    def test_get_strategy_source_path(self):
        for sid in ("ma_cross_v1", "volume_shrink_drop_v1"):
            path = get_strategy_source_path(sid)
            self.assertIsNotNone(path)
            self.assertIn(sid, path)

    def test_get_strategy_source_path_unknown(self):
        self.assertIsNone(get_strategy_source_path("nonexistent"))

    def test_get_strategy_validation_known(self):
        result = get_strategy_validation("ma_cross_v1")
        self.assertTrue(result["usable"])
        self.assertEqual(result["strategy_id"], "ma_cross_v1")
        self.assertEqual(result["unusable_reasons"], [])

    def test_get_strategy_validation_unknown(self):
        result = get_strategy_validation("nonexistent")
        self.assertFalse(result["usable"])
        self.assertIn("未知策略", result["unusable_reasons"][0])

    def test_strategies_have_param_schema(self):
        for sid, cls in STRATEGY_REGISTRY.items():
            schema = cls.param_schema()
            self.assertIsInstance(schema, list)
            self.assertGreater(len(schema), 0)
            for param in schema:
                self.assertIn("key", param)
                self.assertIn("label", param)
                self.assertIn("type", param)

    def test_strategies_have_required_columns(self):
        for sid, cls in STRATEGY_REGISTRY.items():
            cols = cls.required_columns()
            self.assertIsInstance(cols, set)
            self.assertIn("datetime", cols)

    def test_strategies_have_default_policy_profile(self):
        for sid, cls in STRATEGY_REGISTRY.items():
            profile = cls.default_policy_profile()
            self.assertIsInstance(profile, str)
            self.assertGreater(len(profile), 0)


if __name__ == "__main__":
    unittest.main()
