"""运行时校验测试：validate_strategy_params / validate_strategy_runtime / 路由 422。

覆盖「合法参数」与「非法参数」的边界，防止像 VSD 负浮点被 str.isdigit() 误判为
非法这类缺陷漏网。
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.schemas.backtest import BacktestRunRequest
from app.services.backtest.registry import STRATEGY_REGISTRY, validate_strategy_runtime
from app.services.backtest.strategies.contracts import BaseStrategyV2
from app.services.backtest.strategies.ma_cross_v1 import MACrossV1Strategy
from app.services.backtest.strategies.volume_shrink_drop_v1 import (
    VolumeShrinkDropV1Strategy,
)
from app.services.backtest.validators.strategy_validator import (
    StrategyValidationError,
    validate_strategy_class,
    validate_strategy_params,
)
from app.routers.backtest import _validate_submission, create_backtest_job


class RuntimeValidationTests(unittest.TestCase):
    def test_negative_float_param_passes(self):
        """合法负浮点（VSD min_price_drop_pct=-1.0）不应被误判为非法。"""
        result = validate_strategy_params(
            VolumeShrinkDropV1Strategy,
            {"min_price_drop_pct": "-1.0"},
            "manual_symbols",
        )
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.validated_params["min_price_drop_pct"], -1.0)

    def test_empty_param_set_fills_defaults(self):
        result = validate_strategy_params(MACrossV1Strategy, {}, "manual_symbols")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.validated_params["short_window"], 5)
        self.assertEqual(result.validated_params["long_window"], 20)

    def test_non_numeric_param_fails(self):
        result = validate_strategy_params(
            MACrossV1Strategy,
            {"short_window": "abc", "long_window": 10},
            "manual_symbols",
        )
        self.assertFalse(result.valid)
        self.assertTrue(any("应为 int 类型" in e["message"] for e in result.errors))

    def test_out_of_bounds_param_fails(self):
        result = validate_strategy_params(
            MACrossV1Strategy,
            {"short_window": 999, "long_window": 20},
            "manual_symbols",
        )
        self.assertFalse(result.valid)
        self.assertTrue(any("不能大于 120" in e["message"] for e in result.errors))

    def test_cross_field_short_ge_long_fails(self):
        result = validate_strategy_params(
            MACrossV1Strategy, {"short_window": 10, "long_window": 5}, "manual_symbols"
        )
        self.assertFalse(result.valid)
        self.assertTrue(
            any("必须小于 long_window" in e["message"] for e in result.errors)
        )

    def test_unsupported_mode_fails(self):
        result = validate_strategy_params(
            MACrossV1Strategy, {"short_window": 5, "long_window": 20}, "bogus_mode"
        )
        self.assertFalse(result.valid)
        self.assertTrue(any("不支持模式" in e["message"] for e in result.errors))

    def test_runtime_unknown_strategy_raises(self):
        with self.assertRaises(StrategyValidationError):
            validate_strategy_runtime("not_a_strategy", {}, "manual_symbols")

    def test_runtime_unusable_strategy_raises(self):
        class UnusableStrategy(BaseStrategyV2):
            strategy_id = "unusable_test"
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

        self.assertFalse(validate_strategy_class(UnusableStrategy).usable)
        with patch.dict(STRATEGY_REGISTRY, {"unusable_test": UnusableStrategy}):
            with self.assertRaises(StrategyValidationError):
                validate_strategy_runtime("unusable_test", {}, "manual_symbols")

    def test_create_backtest_job_maps_error_to_422(self):
        """非法参数提交 /jobs 应映射为 HTTP 422 而非 500/200。"""
        request = BacktestRunRequest(
            name="t",
            strategy_id="ma_cross_v1",
            strategy_params={"short_window": "abc", "long_window": 10},
            mode="manual_symbols",
            universe_type="all",
            symbols=["000001"],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            initial_cash=100000,
        )
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(create_backtest_job(request, current_user=MagicMock()))
        self.assertEqual(cm.exception.status_code, 422)
        self.assertTrue(cm.exception.detail)

    def test_strategy_select_preserves_non_schema_params(self):
        """strategy_select 模式经 _validate_submission 后，非 schema 业务键应保留。

        回归：写回 validated_params（只含 param_schema 键）会丢弃 boards / exclude_st /
        policy_profile，导致 _run_strategy_select_mode 读取 params["boards"] /
        params["exclude_st"] 时回退到默认值（["main"] / True），用户所选板块与「去 ST」
        开关静默失效。写回时应只覆盖 schema 键，保留其余透传键。
        """
        request = BacktestRunRequest(
            name="t",
            strategy_id="ma_cross_v1",
            strategy_params={
                "short_window": "5",
                "long_window": "20",
                "boards": ["gem", "star"],
                "exclude_st": False,
            },
            mode="strategy_select",
            universe_type="all",
            symbols=[],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            initial_cash=100000,
        )
        _validate_submission(request)
        params = request.strategy_params
        # 非 schema 键原样透传
        self.assertEqual(params["boards"], ["gem", "star"])
        self.assertIs(params["exclude_st"], False)
        # schema 键仍被强转为 int
        self.assertEqual(params["short_window"], 5)
        self.assertEqual(params["long_window"], 20)


if __name__ == "__main__":
    unittest.main()
