"""astock_provider（mootdx 主路径）的 warmup 区间取数回归测试。

覆盖 VEW-32 阻断项 2：mootdx bars() 只拉"相对今天最近 N 根"，再按
[start_date, end_date] 后置过滤——若过滤后最早一根仍晚于 start_date，说明
warmup 回拉窗口（如 GMM-250 的 eff_start）未被取到，必须返回 None 触发
支持日期区间取数的 Eastmoney / Tushare 兜底，避免静默截断。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from app.providers.astock_provider import AStockDataProvider


def _mootdx_bars(start: str = "2025-06-01", n: int = 400) -> pd.DataFrame:
    """构造一份 mootdx bars() 风格的返回帧（datetime 为工作日）。"""
    dates = pd.bdate_range(start, periods=n, freq="D")
    return pd.DataFrame(
        {
            "datetime": dates,
            "open": [10.0] * n,
            "close": [10.0] * n,
            "high": [11.0] * n,
            "low": [9.0] * n,
            "volume": [100.0] * n,
            "amount": [1000.0] * n,
        }
    )


class MootdxWarmupRangeTests(unittest.TestCase):
    def setUp(self):
        self.provider = AStockDataProvider()

    def test_returns_none_when_recent_window_does_not_reach_start(self):
        """start_date 远早于 mootdx 近期窗口 → 判定未覆盖，返回 None 触发兜底。"""
        mock_client = MagicMock()
        # 返回 2025-06-01 起的工作日 bars（近期窗口），不覆盖 2024 年
        mock_client.bars.return_value = _mootdx_bars(start="2025-06-01", n=400)
        with patch("app.providers.astock_provider._mootdx_client", mock_client):
            df = self.provider._fetch_kline_mootdx(
                "000001",
                period="101",
                start_date="2024-01-01",
                end_date="2026-09-03",
                count=500,
            )
        self.assertIsNone(df)
        mock_client.bars.assert_called_once()

    def test_returns_data_when_window_is_covered(self):
        """start_date 落在近期窗口内 → 正常返回过滤后的日期区间。"""
        mock_client = MagicMock()
        mock_client.bars.return_value = _mootdx_bars(start="2025-06-01", n=400)
        with patch("app.providers.astock_provider._mootdx_client", mock_client):
            df = self.provider._fetch_kline_mootdx(
                "000001",
                period="101",
                start_date="2025-07-01",
                end_date="2025-08-01",
                count=500,
            )
        self.assertIsNotNone(df)
        self.assertFalse(df.empty)
        self.assertGreaterEqual(df["datetime"].min()[:10], "2025-07-01")
        self.assertLessEqual(df["datetime"].max()[:10], "2025-08-01")

    def test_empty_start_date_skips_coverage_check(self):
        """start_date 为空（前端滚动加载语义）时不触发覆盖率校验，直接返回。"""
        mock_client = MagicMock()
        mock_client.bars.return_value = _mootdx_bars(start="2025-06-01", n=400)
        with patch("app.providers.astock_provider._mootdx_client", mock_client):
            df = self.provider._fetch_kline_mootdx(
                "000001",
                period="101",
                start_date="",
                end_date="2025-08-01",
                count=500,
            )
        self.assertIsNotNone(df)


if __name__ == "__main__":
    unittest.main()
