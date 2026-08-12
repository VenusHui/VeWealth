"""测试策略管理服务"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock

from app.services.backtest.strategy_management_service import (
    BacktestStrategyManagementService,
    CORE_METHODS,
)


class StrategyManagementServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = BacktestStrategyManagementService()

    def test_core_methods_defined(self):
        self.assertIn("param_schema", CORE_METHODS)
        self.assertIn("required_columns", CORE_METHODS)
        self.assertIn("default_policy_profile", CORE_METHODS)
        self.assertIn("generate_candidates", CORE_METHODS)

    def test_read_source_file_exists(self):
        with TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_strategy.py"
            file_path.write_text("class TestStrategy:\n    pass\n")
            text, lines, path = self.service._read_source(str(file_path))
            self.assertIsNotNone(text)
            self.assertIn("class TestStrategy", text)
            self.assertEqual(lines, 2)
            self.assertEqual(path, file_path.as_posix())

    def test_read_source_file_not_found(self):
        text, lines, path = self.service._read_source("/nonexistent/path.py")
        self.assertIsNone(text)
        self.assertEqual(lines, 0)
        self.assertIsNone(path)

    def test_read_source_none_path(self):
        text, lines, path = self.service._read_source(None)
        self.assertIsNone(text)
        self.assertEqual(lines, 0)
        self.assertIsNone(path)

    def test_extract_core_snippet_finds_class(self):
        source = '''
class MyStrategy(BaseStrategyV2):
    strategy_id = "my_test"

    @classmethod
    def param_schema(cls):
        return []

    @classmethod
    def required_columns(cls):
        return {"datetime"}

    def other_method(self):
        pass

    @classmethod
    def default_policy_profile(cls):
        return "vsd_v1_default"

    def generate_candidates(self, df, params):
        return df
'''
        snippet = self.service._extract_core_snippet(source, "MyStrategy")
        self.assertIn("class MyStrategy", snippet)
        self.assertIn("param_schema", snippet)
        self.assertIn("required_columns", snippet)
        self.assertIn("default_policy_profile", snippet)
        self.assertIn("generate_candidates", snippet)
        self.assertNotIn("other_method", snippet)

    def test_extract_core_snippet_class_not_found(self):
        source = "x = 1\n"
        snippet = self.service._extract_core_snippet(source, "NoSuchClass")
        self.assertEqual(snippet, source)

    def test_extract_core_snippet_parse_error(self):
        source = "this is not valid python @@@"
        snippet = self.service._extract_core_snippet(source, "AnyClass")
        self.assertEqual(snippet, source)

    @patch.object(
        BacktestStrategyManagementService,
        "_extract_latest_backtest",
        return_value=None,
    )
    @patch(
        "app.services.backtest.strategy_management_service.Path.exists",
        return_value=True,
    )
    @patch(
        "app.services.backtest.strategy_management_service.Path.stat",
    )
    def test_build_all_items(self, mock_stat, mock_exists, mock_latest):
        mock_stat.return_value.st_mtime = 1711200000.0

        items = self.service._build_all_items(MagicMock())
        self.assertEqual(len(items), 3)
        ids = {item["strategy_id"] for item in items}
        self.assertSetEqual(
            ids, {"ma_cross_v1", "volume_shrink_drop_v1", "gmm_volume_v1"}
        )

        for item in items:
            self.assertIn("strategy_id", item)
            self.assertIn("name", item)
            self.assertIn("description", item)
            self.assertIn("usable", item)
            self.assertIn("policy_profile", item)
            self.assertIn("last_modified_at", item)
            self.assertIn("latest_backtest", item)
            self.assertIn("has_code", item)
            self.assertEqual(item["has_code"], True)

    @patch.object(
        BacktestStrategyManagementService,
        "_build_all_items",
    )
    def test_list_strategies_pagination(self, mock_build):
        items = [
            {
                "strategy_id": f"s{i}",
                "name": f"策略{i}",
                "description": f"描述{i}",
                "usable": True,
                "policy_profile": "vsd_v1_default",
                "last_modified_at": datetime(2026, 1, i),
                "latest_backtest": None,
                "has_code": True,
            }
            for i in range(1, 11)
        ]
        mock_build.return_value = items

        result = self.service.list_strategies(MagicMock(), page=1, page_size=3)
        self.assertEqual(result["total"], 10)
        self.assertEqual(len(result["data"]), 3)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 3)

    @patch.object(
        BacktestStrategyManagementService,
        "_build_all_items",
    )
    def test_list_strategies_filter_by_query(self, mock_build):
        items = [
            {
                "strategy_id": "ma_cross_v1",
                "name": "双均线策略 v1",
                "description": "...",
                "usable": True,
                "policy_profile": "vsd_v1_default",
                "last_modified_at": datetime(2026, 1, 1),
                "latest_backtest": None,
                "has_code": True,
            },
            {
                "strategy_id": "volume_shrink_drop_v1",
                "name": "连续缩量下跌反弹 v1",
                "description": "...",
                "usable": True,
                "policy_profile": "vsd_v1_default",
                "last_modified_at": datetime(2026, 1, 2),
                "latest_backtest": None,
                "has_code": True,
            },
        ]
        mock_build.return_value = items

        result = self.service.list_strategies(MagicMock(), query="均线")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["data"][0]["strategy_id"], "ma_cross_v1")

    @patch.object(
        BacktestStrategyManagementService,
        "_build_all_items",
    )
    def test_list_strategies_filter_usable(self, mock_build):
        items = [
            {
                "strategy_id": "s1",
                "name": "可用策略",
                "description": "...",
                "usable": True,
                "policy_profile": None,
                "last_modified_at": datetime(2026, 1, 1),
                "latest_backtest": None,
                "has_code": False,
            },
            {
                "strategy_id": "s2",
                "name": "不可用策略",
                "description": "...",
                "usable": False,
                "policy_profile": None,
                "last_modified_at": datetime(2026, 1, 2),
                "latest_backtest": None,
                "has_code": False,
            },
        ]
        mock_build.return_value = items

        result = self.service.list_strategies(MagicMock(), usable="true")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["data"][0]["strategy_id"], "s1")

        result = self.service.list_strategies(MagicMock(), usable="false")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["data"][0]["strategy_id"], "s2")

    @patch.object(
        BacktestStrategyManagementService,
        "_build_all_items",
    )
    def test_list_strategies_sort_by_annual_return(self, mock_build):
        items = [
            {
                "strategy_id": "s1",
                "name": "a",
                "description": "...",
                "usable": True,
                "policy_profile": None,
                "last_modified_at": None,
                "latest_backtest": {"annual_return": 0.1},
                "has_code": False,
            },
            {
                "strategy_id": "s2",
                "name": "b",
                "description": "...",
                "usable": True,
                "policy_profile": None,
                "last_modified_at": None,
                "latest_backtest": {"annual_return": 0.3},
                "has_code": False,
            },
            {
                "strategy_id": "s3",
                "name": "c",
                "description": "...",
                "usable": True,
                "policy_profile": None,
                "last_modified_at": None,
                "latest_backtest": None,
                "has_code": False,
            },
        ]
        mock_build.return_value = items

        result = self.service.list_strategies(
            MagicMock(), sort_by="annual_return", sort_order="desc"
        )
        self.assertEqual(result["data"][0]["strategy_id"], "s2")
        self.assertEqual(result["data"][1]["strategy_id"], "s1")
        self.assertEqual(result["data"][2]["strategy_id"], "s3")

    def test_get_strategy_detail_unknown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.service.get_strategy_detail(MagicMock(), "nonexistent")
        self.assertIn("未知策略", str(ctx.exception))

    @patch(
        "app.services.backtest.strategy_management_service.get_strategy_source_path",
        return_value=None,
    )
    @patch(
        "app.services.backtest.strategy_management_service.get_strategy_validation",
    )
    def test_get_strategy_detail_known(self, mock_validation, mock_source_path):
        mock_validation.return_value = {
            "usable": True,
            "unusable_reasons": [],
            "policy_profile": "vsd_v1_default",
        }
        mock_source_path.return_value = None

        detail = self.service.get_strategy_detail(MagicMock(), "ma_cross_v1")

        self.assertIn("strategy_info", detail)
        self.assertIn("latest_backtest", detail)
        self.assertIn("code", detail)
        self.assertEqual(detail["strategy_info"]["strategy_id"], "ma_cross_v1")
        self.assertEqual(detail["strategy_info"]["name"], "双均线策略 v1")
        self.assertEqual(detail["code"]["language"], "python")
        self.assertEqual(detail["code"]["line_count"], 0)


if __name__ == "__main__":
    unittest.main()
