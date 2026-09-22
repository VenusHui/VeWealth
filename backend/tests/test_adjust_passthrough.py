"""复权口径透传与降级标记测试（VEW-55）。

覆盖：
- get_daily_data / get_daily_data_with_meta 把 adjust 透传给 provider；
- get_kline_data(period=101) 从 provenance 读取实际口径与降级标记并返回；
- get_depth_data 把 adjust_actual / adjust_degraded 透传到综合响应。
"""

from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from app.providers.provenance import DataProvenance
from app.services.stock_service import StockService


class AdjustPassthroughTests(unittest.TestCase):
    def setUp(self):
        self.service = object.__new__(StockService)

    def _daily_result(self, adjustment="qfq", degraded=False):
        df = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    ["2026-01-05 00:00:00", "2026-01-06 00:00:00"]
                ),
                "open": [10.0, 10.2],
                "high": [10.5, 10.6],
                "low": [9.9, 10.0],
                "close": [10.0, 10.4],
                "volume": [1000, 1200],
            }
        )
        df.attrs["provenance"] = DataProvenance(
            source="tushare",
            adjustment=adjustment,
            degraded=degraded,
            requested_start="20260101",
            requested_end="20260110",
        )
        return df

    def test_get_daily_data_passes_adjust_through(self):
        """get_daily_data 把 adjust 透传给 with_meta 分支。"""
        from app.providers.provenance import DailyDataResult

        df = self._daily_result()
        with mock.patch.object(self.service, "get_daily_data_with_meta") as meta:
            meta.return_value = DailyDataResult(
                df=df,
                provenance=df.attrs["provenance"],
            )
            result = self.service.get_daily_data(
                "600519", "2026-01-01", "2026-01-10", adjust="hfq"
            )
            self.assertEqual(meta.call_args.kwargs["adjust"], "hfq")
            self.assertEqual(result[0]["close"].iloc[0], 10.0)
            # provenance 挂到 df.attrs，供回测读取
            self.assertEqual(df.attrs["provenance"].adjustment, "qfq")

    def test_get_daily_data_with_meta_passes_adjust_to_provider(self):
        """with_meta 把 adjust 透传给 provider.fetch_daily_data_with_meta。"""
        from app.providers.provenance import DailyDataResult

        self.service.provider = mock.Mock()
        self.service.provider.fetch_daily_data_with_meta.return_value = DailyDataResult(
            df=self._daily_result(),
            provenance=DataProvenance(
                source="tushare",
                adjustment="",
                requested_start="2026-01-01",
                requested_end="2026-01-10",
            ),
        )
        self.service.get_daily_data_with_meta(
            "600519", "2026-01-01", "2026-01-10", adjust=""
        )
        self.assertEqual(
            self.service.provider.fetch_daily_data_with_meta.call_args.kwargs["adjust"],
            "",
        )

    def test_kline_daily_returns_adjust_actual_from_provenance(self):
        """get_kline_data(period=101) 从 provenance 读实际口径并返回。"""
        df = self._daily_result(adjustment="", degraded=True)
        with mock.patch.object(
            self.service,
            "get_daily_data",
            return_value=(df, "2026-01-05", "2026-01-06"),
        ):
            result = self.service.get_kline_data("600519", period="101", adjust="qfq")
        self.assertEqual(result["adjust"], "qfq")
        self.assertEqual(result["adjust_actual"], "")
        self.assertTrue(result["adjust_degraded"])
        self.assertEqual(len(result["klines"]), 2)

    def test_kline_daily_keeps_requested_adjust_when_no_degradation(self):
        """未降级时 adjust_actual 保持请求口径。"""
        df = self._daily_result(adjustment="qfq", degraded=False)
        with mock.patch.object(
            self.service,
            "get_daily_data",
            return_value=(df, "2026-01-05", "2026-01-06"),
        ):
            result = self.service.get_kline_data("600519", period="101", adjust="qfq")
        self.assertEqual(result["adjust_actual"], "qfq")
        self.assertFalse(result["adjust_degraded"])

    def _minute_df(self, adjust_served):
        df = pd.DataFrame(
            {
                "datetime": [
                    "2026-01-05 09:35:00",
                    "2026-01-05 09:40:00",
                    "2026-01-05 09:45:00",
                ],
                "open": [10.0, 10.1, 10.2],
                "high": [10.5, 10.6, 10.7],
                "low": [9.9, 10.0, 10.1],
                "close": [10.0, 10.4, 10.6],
                "volume": [100, 120, 110],
            }
        )
        df.attrs["adjust_served"] = adjust_served
        df.attrs["adjust_degraded"] = False
        return df

    def test_kline_minute_marks_mootdx_raw(self):
        """分钟线由 mootdx 返回时如实标记非复权降级。"""
        df = self._minute_df(adjust_served="")
        self.service.provider = mock.Mock()
        self.service.provider.fetch_minute_data.return_value = df
        result = self.service.get_kline_data("600519", period="5", adjust="qfq")
        self.assertEqual(result["adjust_actual"], "")
        self.assertTrue(result["adjust_degraded"])
        self.assertEqual(len(result["klines"]), 3)

    def test_kline_minute_keeps_eastmoney_adjust(self):
        """分钟线由东财 fqt 复权返回时按实际口径标注。"""
        df = self._minute_df(adjust_served="qfq")
        self.service.provider = mock.Mock()
        self.service.provider.fetch_minute_data.return_value = df
        result = self.service.get_kline_data("600519", period="5", adjust="qfq")
        self.assertEqual(result["adjust_actual"], "qfq")
        self.assertFalse(result["adjust_degraded"])

    def test_kline_daily_exposes_adjust_factor_date(self):
        """日线 provenance 的 adj_factor 缓存日期透传到响应。"""
        df = self._daily_result(adjustment="qfq", degraded=False)
        df.attrs["provenance"].adjust_factor_date = "2026-09-22"
        with mock.patch.object(
            self.service,
            "get_daily_data",
            return_value=(df, "2026-01-05", "2026-01-06"),
        ):
            result = self.service.get_kline_data("600519", period="101", adjust="qfq")
        self.assertEqual(result["adjust_factor_date"], "2026-09-22")

    def test_depth_data_forwards_adjust_flags(self):
        """get_depth_data 把 kline 的 adjust_actual/adjust_degraded 透传出去。"""
        self.service.get_kline_data = mock.Mock(
            return_value={
                "period": "daily",
                "adjust": "qfq",
                "adjust_actual": "",
                "adjust_degraded": True,
                "klines": [],
            }
        )
        self.service.get_cyq_data = mock.Mock(return_value={"cyq_info": None})
        self.service.get_stock_info = mock.Mock(
            return_value={"stock_info": None, "tencent_quote": {}}
        )
        with mock.patch(
            "app.services.stock_service.DataProcessor.compute_volume_profile",
            return_value={
                "profile": [],
                "total_volume": 0.0,
                "price_min": 0.0,
                "price_max": 0.0,
                "bin_size": 0.0,
                "poc": {"price": 0.0, "volume": 0.0},
                "value_area": {"vah": 0.0, "val": 0.0, "volume_pct": 0.0},
                "hvn_levels": [],
                "lvn_levels": [],
                "vwap": 0.0,
            },
        ), mock.patch(
            "app.services.stock_service.DataProcessor.fit_gaussian_mixture",
            return_value=None,
        ):
            result = self.service.get_depth_data("600519", period="101", adjust="qfq")
        self.assertEqual(result["adjust"], "qfq")
        self.assertEqual(result["adjust_actual"], "")
        self.assertTrue(result["adjust_degraded"])


if __name__ == "__main__":
    unittest.main()
