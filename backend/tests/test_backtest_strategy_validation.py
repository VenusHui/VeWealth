"""测试策略验证器"""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from app.services.backtest.validators.strategy_validator import (
    validate_strategy_class,
    StrategyValidationResult,
    _check_policy_profile,
)
from app.services.backtest.strategies.ma_cross_v1 import MACrossV1Strategy
from app.services.backtest.strategies.volume_shrink_drop_v1 import (
    VolumeShrinkDropV1Strategy,
)
from app.services.backtest.strategies.contracts import BaseStrategyV2


class StrategyValidatorTests(unittest.TestCase):
    def test_both_strategies_pass_validation(self):
        for cls in (MACrossV1Strategy, VolumeShrinkDropV1Strategy):
            result = validate_strategy_class(cls)
            self.assertTrue(result.usable, msg=f"{cls.strategy_id}: {result.unusable_reasons}")
            self.assertEqual(result.unusable_reasons, [])

    def test_validation_result_fields(self):
        result = validate_strategy_class(MACrossV1Strategy)
        self.assertEqual(result.strategy_id, "ma_cross_v1")
        self.assertTrue(result.usable)
        self.assertEqual(result.policy_profile, "vsd_v1_default")

    def test_validation_result_to_dict(self):
        result = validate_strategy_class(MACrossV1Strategy)
        d = result.to_dict()
        self.assertEqual(d["strategy_id"], "ma_cross_v1")
        self.assertTrue(d["usable"])
        self.assertIsInstance(d["unusable_reasons"], list)

    def test_missing_name_triggers_unusable(self):
        # BaseStrategyV2 provides default name/description, so we need to override
        # them to None to test the validation logic.
        class NoNameStrategy(BaseStrategyV2):
            strategy_id = "no_name_test"
            name = None
            description = "test"

            @classmethod
            def param_schema(cls):
                return []

            @classmethod
            def required_columns(cls):
                return {"datetime"}

            @classmethod
            def default_policy_profile(cls):
                return "vsd_v1_default"

            def generate_candidates(self, market_df, params):
                import pandas as pd
                return pd.DataFrame()

        result = validate_strategy_class(NoNameStrategy)
        self.assertFalse(result.usable)
        self.assertIn("缺少 name", result.unusable_reasons)

    def test_missing_description_triggers_unusable(self):
        class NoDescStrategy(BaseStrategyV2):
            strategy_id = "no_desc_test"
            name = "Test"
            description = None

            @classmethod
            def param_schema(cls):
                return []

            @classmethod
            def required_columns(cls):
                return {"datetime"}

            @classmethod
            def default_policy_profile(cls):
                return "vsd_v1_default"

            def generate_candidates(self, market_df, params):
                import pandas as pd
                return pd.DataFrame()

        result = validate_strategy_class(NoDescStrategy)
        self.assertFalse(result.usable)
        self.assertIn("缺少 description", result.unusable_reasons)

    def test_non_v2_strategy_fails(self):
        class NonV2Strategy:
            strategy_id = "non_v2"
            name = "Test"
            description = "Test"

        result = validate_strategy_class(NonV2Strategy)
        self.assertFalse(result.usable)
        self.assertIn("未实现 BaseStrategyV2 契约", result.unusable_reasons)

    def test_param_schema_not_list_fails(self):
        class BadSchemaStrategy(BaseStrategyV2):
            strategy_id = "bad_schema"
            name = "Test"
            description = "Test"

            @classmethod
            def param_schema(cls):
                return "not_a_list"

            @classmethod
            def required_columns(cls):
                return {"datetime"}

            @classmethod
            def default_policy_profile(cls):
                return "vsd_v1_default"

            def generate_candidates(self, market_df, params):
                import pandas as pd
                return pd.DataFrame()

        result = validate_strategy_class(BadSchemaStrategy)
        self.assertFalse(result.usable)
        self.assertIn("param_schema 必须是 list", result.unusable_reasons)

    def test_required_columns_not_set_fails(self):
        class BadColsStrategy(BaseStrategyV2):
            strategy_id = "bad_cols"
            name = "Test"
            description = "Test"

            @classmethod
            def param_schema(cls):
                return []

            @classmethod
            def required_columns(cls):
                return ["not", "a", "set"]

            @classmethod
            def default_policy_profile(cls):
                return "vsd_v1_default"

            def generate_candidates(self, market_df, params):
                import pandas as pd
                return pd.DataFrame()

        result = validate_strategy_class(BadColsStrategy)
        self.assertFalse(result.usable)
        self.assertIn("required_columns 必须是 set", result.unusable_reasons)

    def test_empty_required_columns_fails(self):
        class EmptyColsStrategy(BaseStrategyV2):
            strategy_id = "empty_cols"
            name = "Test"
            description = "Test"

            @classmethod
            def param_schema(cls):
                return []

            @classmethod
            def required_columns(cls):
                return set()

            @classmethod
            def default_policy_profile(cls):
                return "vsd_v1_default"

            def generate_candidates(self, market_df, params):
                import pandas as pd
                return pd.DataFrame()

        result = validate_strategy_class(EmptyColsStrategy)
        self.assertFalse(result.usable)
        self.assertIn("required_columns 不能为空", result.unusable_reasons)

    def test_empty_policy_profile_fails(self):
        class EmptyProfileStrategy(BaseStrategyV2):
            strategy_id = "empty_profile"
            name = "Test"
            description = "Test"

            @classmethod
            def param_schema(cls):
                return []

            @classmethod
            def required_columns(cls):
                return {"datetime"}

            @classmethod
            def default_policy_profile(cls):
                return ""

            def generate_candidates(self, market_df, params):
                import pandas as pd
                return pd.DataFrame()

        result = validate_strategy_class(EmptyProfileStrategy)
        self.assertFalse(result.usable)
        self.assertIn("default_policy_profile 不能为空", result.unusable_reasons)

    def test_strategy_validation_result_is_usable_when_no_reasons(self):
        result = StrategyValidationResult("test", True, [])
        self.assertTrue(result.usable)

    def test_strategy_validation_result_is_not_usable_when_reasons_exist(self):
        result = StrategyValidationResult("test", False, ["reason1"])
        self.assertFalse(result.usable)

    @patch("app.services.backtest.validators.strategy_validator.get_profile")
    @patch("app.services.backtest.validators.strategy_validator.get_policy")
    def test_check_policy_profile_valid(self, mock_get_policy, mock_get_profile):
        # Use the real TopKSelection policy which has SelectionPolicy with
        # the "同日多标的" docstring as its parent class.
        from app.services.backtest.policies.registry import TopKSelection

        mock_get_policy.return_value = TopKSelection()
        mock_get_profile.return_value = MagicMock(
            ranking_policy="r1",
            selection_policy="s1",
            allocation_policy="a1",
            risk_policy="rk1",
            execution_policy="e1",
        )

        reasons: list[str] = []
        _check_policy_profile("test_profile", reasons)
        self.assertEqual(reasons, [])

    @patch("app.services.backtest.validators.strategy_validator.get_profile")
    def test_check_policy_profile_unknown_profile(self, mock_get_profile):
        mock_get_profile.side_effect = ValueError("未知 profile")

        reasons: list[str] = []
        _check_policy_profile("bad_profile", reasons)
        self.assertTrue(any("policy_profile 不可用" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()
