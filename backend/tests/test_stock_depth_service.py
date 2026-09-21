"""深度数据组合服务的首屏降级行为测试（VEW-54）。"""

from __future__ import annotations

import unittest
from unittest import mock

from app.services.stock_service import StockService


class StockDepthServiceTests(unittest.TestCase):
    def test_can_skip_cyq_without_dropping_core_or_quote_data(self):
        service = object.__new__(StockService)
        service.get_kline_data = mock.Mock(
            return_value={
                "period": "daily",
                "klines": [
                    {
                        "datetime": "2026-09-18 00:00:00",
                        "open": 10.0,
                        "close": 10.5,
                        "high": 10.8,
                        "low": 9.9,
                        "volume": 1000.0,
                    }
                ],
            }
        )
        service.get_cyq_data = mock.Mock(side_effect=AssertionError("CYQ must be lazy"))
        service.get_stock_info = mock.Mock(
            return_value={
                "stock_info": {"name": "测试"},
                "tencent_quote": {"price": 10.5},
            }
        )

        profile = {
            "profile": [],
            "total_volume": 1000.0,
            "price_min": 9.9,
            "price_max": 10.8,
            "bin_size": 0.1,
            "poc": {"price": 10.5, "volume": 1000.0},
            "value_area": {"vah": 10.8, "val": 9.9, "volume_pct": 100.0},
            "hvn_levels": [],
            "lvn_levels": [],
            "vwap": 10.5,
        }
        with mock.patch(
            "app.services.stock_service.DataProcessor.compute_volume_profile",
            return_value=profile,
        ), mock.patch(
            "app.services.stock_service.DataProcessor.fit_gaussian_mixture",
            return_value=None,
        ):
            result = service.get_depth_data("000001", period="101", include_cyq=False)

        service.get_cyq_data.assert_not_called()
        self.assertEqual(len(result["klines"]), 1)
        self.assertIsNone(result["cyq_info"])
        self.assertEqual(result["tencent_quote"]["price"], 10.5)


if __name__ == "__main__":
    unittest.main()
