"""AStockDataProvider — implements MarketDataProvider via a-stock-data patterns.

Primary K-line source: mootdx (TCP, no IP block).
Fallback chain: Eastmoney HTTP → Tushare (daily only).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import pandas as pd

from app.core.config import settings
from app.providers.base import MarketDataProvider
from app.providers.astock_data import (
    eastmoney_all_stocks,
    eastmoney_cyq,
    eastmoney_kline,
    eastmoney_trends2,
    fqt_code,
)

try:
    import tushare as ts
except Exception:
    ts = None

try:
    from mootdx.quotes import Quotes

    _mootdx_client = Quotes.factory(market="std")
except Exception:
    _mootdx_client = None

logger = logging.getLogger(__name__)

# Per-attempt sleep multiplier
_RETRY_SLEEP = 0.6

# mootdx frequency mapping: API period str → mootdx frequency int
# From mootdx.consts: KLINE_1MIN=8, KLINE_5MIN=0, KLINE_15MIN=1,
# KLINE_30MIN=2, KLINE_1HOUR=3, KLINE_DAILY=4
_FREQ_MAP = {
    "1": 8,  # KLINE_1MIN
    "5": 0,  # KLINE_5MIN
    "15": 1,  # KLINE_15MIN
    "30": 2,  # KLINE_30MIN
    "60": 3,  # KLINE_1HOUR
    "101": 4,  # KLINE_DAILY
}


class AStockDataProvider(MarketDataProvider):
    """Data provider using mootdx TCP (primary) with Eastmoney/Tushare fallbacks.

    K-line data comes from mootdx → 通达信 servers via TCP, avoiding
    IP-level blocks that affect Eastmoney HTTP endpoints.
    """

    # ------------------------------------------------------------------
    # mootdx K-line (primary source)
    # ------------------------------------------------------------------

    def _fetch_kline_mootdx(
        self,
        stock_code: str,
        period: str,
        start_date: str,
        end_date: str,
        count: int = 500,
        start_offset: int = 0,
    ) -> Optional[pd.DataFrame]:
        """Fetch K-line data via mootdx TCP (通达信).

        Args:
            count: Number of bars to fetch (max 800 per request, capped).
            start_offset: Skip the first N most-recent bars. Used by the
                          frontend for dynamic scroll-based loading.
        """
        if _mootdx_client is None:
            return None

        freq = _FREQ_MAP.get(period)
        if freq is None:
            return None

        try:
            cols = ["open", "close", "high", "low", "volume", "amount"]
            page_size = min(count, 800)
            klines = _mootdx_client.bars(
                symbol=stock_code,
                frequency=freq,
                start=start_offset,
                offset=page_size,
            )
            if klines is None or klines.empty:
                return None

            if "datetime" in klines.columns:
                klines = klines.reset_index(drop=True)
            else:
                klines = klines.reset_index()

            df = klines
            if "index" in df.columns and "datetime" not in df.columns:
                df = df.rename(columns={"index": "datetime"})

            if "datetime" not in df.columns:
                logger.warning(f"mootdx 缺少datetime列 {stock_code}")
                return None

            df = df.sort_values("datetime")
            df["datetime"] = pd.to_datetime(df["datetime"])

            if start_date:
                df = df[df["datetime"] >= pd.Timestamp(start_date)]
            if end_date:
                df = df[df["datetime"] <= pd.Timestamp(end_date)]

            if df.empty:
                return None

            df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
            available = ["datetime"] + [c for c in cols if c in df.columns]
            return df[available]
        except Exception as e:
            logger.warning(f"mootdx K线请求失败 {stock_code}: {e}")
            return None

    # ------------------------------------------------------------------
    # Daily data
    # ------------------------------------------------------------------

    def fetch_daily_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        max_retries: int = 2,
        count: int = 500,
        start_offset: int = 0,
    ) -> Optional[pd.DataFrame]:
        # 1. Try mootdx first (TCP, no IP block)
        df = self._fetch_kline_mootdx(
            stock_code,
            period="101",
            start_date=start_date,
            end_date=end_date,
            count=count,
            start_offset=start_offset,
        )
        if df is not None and not df.empty:
            logger.info(f"股票 {stock_code} 日线由 mootdx 返回")
            return df

        # 2. Fallback: Eastmoney → Tushare
        fqt = fqt_code(adjust)
        for attempt in range(1, max_retries + 2):
            try:
                df = eastmoney_kline(
                    code=stock_code,
                    klt="101",
                    beg=start_date,
                    end=end_date,
                    fqt=fqt,
                )
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                logger.warning(
                    f"获取股票 {stock_code} 日线数据失败(第{attempt}次): {e}"
                )
            if attempt <= max_retries:
                time.sleep(_RETRY_SLEEP * attempt)
                continue
            logger.warning(f"股票 {stock_code} Eastmoney 日线重试耗尽，回退 Tushare")
            break

        return self._fetch_daily_tushare(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            max_retries=settings.TUSHARE_RETRY_TIMES,
        )

    # ------------------------------------------------------------------
    # Tushare fallback (carried over from AKShareProvider)
    # ------------------------------------------------------------------

    @staticmethod
    def _to_tushare_code(stock_code: str) -> str:
        code = str(stock_code).zfill(6)
        if code.startswith(("5", "6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    def _fetch_daily_tushare(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str,
        max_retries: int,
    ) -> Optional[pd.DataFrame]:
        if not settings.TUSHARE_ENABLED:
            return None
        if not settings.TUSHARE_TOKEN:
            logger.warning("Tushare 未配置 token，跳过备源")
            return None
        if ts is None:
            logger.warning("tushare 依赖未安装，跳过备源")
            return None

        ts_code = self._to_tushare_code(stock_code)
        adj = adjust if adjust in {"qfq", "hfq"} else None

        for attempt in range(1, max_retries + 2):
            try:
                ts.set_token(settings.TUSHARE_TOKEN)
                df = ts.pro_bar(
                    ts_code=ts_code,
                    adj=adj,
                    start_date=start_date,
                    end_date=end_date,
                )
                if df is None or df.empty:
                    return None

                normalized = pd.DataFrame(
                    {
                        "日期": pd.to_datetime(df["trade_date"], format="%Y%m%d"),
                        "开盘": df["open"],
                        "收盘": df["close"],
                        "最高": df["high"],
                        "最低": df["low"],
                        "成交量": df["vol"],
                        "成交额": df.get("amount"),
                    }
                )
                normalized = normalized.sort_values("日期").reset_index(drop=True)
                normalized["日期"] = normalized["日期"].dt.strftime("%Y-%m-%d 00:00:00")
                logger.info(f"股票 {stock_code} 日线数据由 Tushare 备源返回")
                return normalized.rename(
                    columns={
                        "日期": "datetime",
                        "开盘": "open",
                        "收盘": "close",
                        "最高": "high",
                        "最低": "low",
                        "成交量": "volume",
                        "成交额": "amount",
                    }
                )
            except Exception as e:
                if attempt <= max_retries:
                    logger.warning(
                        f"Tushare 获取股票 {stock_code} 日线失败(第{attempt}次重试): {e}"
                    )
                    time.sleep(0.8 * attempt)
                    continue
                logger.error(f"Tushare 获取股票 {stock_code} 日线失败: {e}")
                return None
        return None

    # ------------------------------------------------------------------
    # Minute data
    # ------------------------------------------------------------------

    def fetch_minute_data(
        self,
        stock_code: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "1",
        adjust: str = "",
        max_retries: int = 2,
        count: int = 500,
        start_offset: int = 0,
    ) -> Optional[pd.DataFrame]:
        # 1. Try mootdx first (TCP, supports all periods including 1min)
        df = self._fetch_kline_mootdx(
            stock_code,
            period=period,
            start_date=start_datetime,
            end_date=end_datetime,
            count=count,
            start_offset=start_offset,
        )
        if df is not None and not df.empty:
            logger.info(
                f"股票 {stock_code} period={period} 由 mootdx 返回 (共{len(df)}行)"
            )
            return df

        # 2. Fallback: Eastmoney HTTP
        for attempt in range(1, max_retries + 2):
            try:
                if period == "1":
                    df = eastmoney_trends2(code=stock_code)
                else:
                    fqt = fqt_code(adjust)
                    df = eastmoney_kline(
                        code=stock_code,
                        klt=period,
                        beg="",
                        end="20500101",
                        fqt=fqt,
                    )

                if df is not None and not df.empty:
                    # Filter to requested datetime range
                    if "datetime" in df.columns:
                        df["datetime"] = pd.to_datetime(df["datetime"])
                        mask = (df["datetime"] >= pd.Timestamp(start_datetime)) & (
                            df["datetime"] <= pd.Timestamp(end_datetime)
                        )
                        df = df[mask]
                        df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")

                    if not df.empty:
                        return df

            except Exception as e:
                logger.warning(
                    f"获取股票 {stock_code} 分钟数据失败(第{attempt}次): {e}"
                )

            if attempt <= max_retries:
                time.sleep(_RETRY_SLEEP * attempt)
                continue
            logger.warning(
                f"股票 {stock_code} 在 {start_datetime}-{end_datetime} 期间无分钟数据"
            )
            return None

    # ------------------------------------------------------------------
    # Real-time data
    # ------------------------------------------------------------------

    def fetch_realtime_data(self) -> Optional[pd.DataFrame]:
        try:
            df = eastmoney_all_stocks()
            if df is None or df.empty:
                logger.warning("获取实时行情数据为空")
                return None
            return df
        except Exception as e:
            logger.error(f"获取实时行情数据失败: {e}")
            return None

    # ------------------------------------------------------------------
    # CYQ data
    # ------------------------------------------------------------------

    def _compute_cyq_locally(self, stock_code: str) -> Optional[dict[str, Any]]:
        """Compute chip distribution from mootdx daily klines.

        Uses volume-weighted price distribution with exponential decay
        (newer bars weighted more heavily) to approximate where current
        shareholders acquired their shares.
        """
        import numpy as np

        df = self._fetch_kline_mootdx(
            stock_code,
            period="101",
            start_date="",
            end_date="",
            count=210,
        )
        if df is None or df.empty:
            return None

        df = df.sort_values("datetime")
        prices = (df["high"] + df["low"] + df["close"]) / 3.0
        volumes = df["volume"].values
        n = len(df)

        if n < 10 or volumes.sum() <= 0:
            return None

        # Build cost distribution: 200 price bins
        price_min = df["low"].min()
        price_max = df["high"].max()
        if price_max <= price_min:
            price_max = price_min + 0.01
        bins = 200
        bin_size = (price_max - price_min) / bins
        dist = np.zeros(bins)

        for i in range(n):
            vol = volumes[i]
            if vol <= 0:
                continue
            # Exponential decay: older bars have less weight
            decay = np.exp(-0.5 * (n - 1 - i) / max(n - 1, 1))
            weight = vol * decay
            low = df.iloc[i]["low"]
            high_val = df.iloc[i]["high"]
            bar_range = high_val - low
            if bar_range <= 0:
                b = int((low - price_min) / bin_size)
                b = max(0, min(b, bins - 1))
                dist[b] += weight
            else:
                vol_per_unit = weight / bar_range
                low_b = int((low - price_min) / bin_size)
                high_b = int((high_val - price_min) / bin_size)
                low_b = max(0, min(low_b, bins - 1))
                high_b = max(0, min(high_b, bins - 1))
                for b in range(low_b, high_b + 1):
                    bin_low = price_min + b * bin_size
                    bin_high = bin_low + bin_size
                    overlap = max(0.0, min(high_val, bin_high) - max(low, bin_low))
                    dist[b] += vol_per_unit * overlap

        total = dist.sum()
        if total <= 0:
            return None

        # Average cost
        bin_centers = np.array([price_min + (i + 0.5) * bin_size for i in range(bins)])
        avg_cost = float(np.sum(bin_centers * dist) / total)

        # Current price (last close)
        current_price = float(df.iloc[-1]["close"])

        # Profit ratio: percentage of distribution below current price
        below = dist[bin_centers <= current_price].sum()
        profit_ratio = float(below / total)

        # 90% and 70% cost ranges via cumulative distribution
        cumsum = 0.0
        # Sort bin centers by distance from distribution peak
        peak_idx = int(np.argmax(dist))
        low_idx = peak_idx
        high_idx = peak_idx
        cumsum = dist[peak_idx]

        target_90 = total * 0.90
        while cumsum < target_90:
            can_low = low_idx > 0
            can_high = high_idx < bins - 1
            if not can_low and not can_high:
                break
            if can_low and can_high:
                if dist[low_idx - 1] >= dist[high_idx + 1]:
                    low_idx -= 1
                else:
                    high_idx += 1
            elif can_low:
                low_idx -= 1
            else:
                high_idx += 1
            cumsum += dist[low_idx if can_low else high_idx]
        cost_90_low = float(bin_centers[low_idx])
        cost_90_high = float(bin_centers[high_idx])
        concentration_90 = (
            float((cost_90_high - cost_90_low) / avg_cost) if avg_cost > 0 else 0
        )

        # 70% range
        low_idx = peak_idx
        high_idx = peak_idx
        cumsum = dist[peak_idx]
        target_70 = total * 0.70
        while cumsum < target_70:
            can_low = low_idx > 0
            can_high = high_idx < bins - 1
            if not can_low and not can_high:
                break
            if can_low and can_high:
                if dist[low_idx - 1] >= dist[high_idx + 1]:
                    low_idx -= 1
                else:
                    high_idx += 1
            elif can_low:
                low_idx -= 1
            else:
                high_idx += 1
            cumsum += dist[low_idx if can_low else high_idx]
        cost_70_low = float(bin_centers[low_idx])
        cost_70_high = float(bin_centers[high_idx])
        concentration_70 = (
            float((cost_70_high - cost_70_low) / avg_cost) if avg_cost > 0 else 0
        )

        return {
            "date": str(df.iloc[-1]["datetime"]),
            "profit_ratio": profit_ratio,
            "avg_cost": round(avg_cost, 2),
            "cost_90_low": round(cost_90_low, 2),
            "cost_90_high": round(cost_90_high, 2),
            "concentration_90": round(concentration_90, 4),
            "cost_70_low": round(cost_70_low, 2),
            "cost_70_high": round(cost_70_high, 2),
            "concentration_70": round(concentration_70, 4),
        }

    def fetch_cyq_data(
        self, stock_code: str, adjust: str = ""
    ) -> Optional[pd.DataFrame]:
        fqt = fqt_code(adjust)
        try:
            df = eastmoney_cyq(code=stock_code, fqt=fqt)
            if df is None or df.empty:
                raise Exception("Eastmoney CYQ returned empty")
            return df
        except Exception as e:
            logger.info(f"Eastmoney CYQ不可用，回退mootdx本地计算 {stock_code}: {e}")

        # Local computation via mootdx
        local = self._compute_cyq_locally(stock_code)
        if local is None:
            return None
        df = pd.DataFrame([local])
        # Add required column names for normalize_cyq_data compatibility
        df["获利比例"] = df["profit_ratio"]
        df["平均成本"] = df["avg_cost"]
        df["90成本-低"] = df["cost_90_low"]
        df["90成本-高"] = df["cost_90_high"]
        df["90集中度"] = df["concentration_90"]
        df["70成本-低"] = df["cost_70_low"]
        df["70成本-高"] = df["cost_70_high"]
        df["70集中度"] = df["concentration_70"]
        return df

    def normalize_cyq_data(self, df: pd.DataFrame) -> dict[str, Any]:
        latest = df.iloc[-1]
        return {
            "date": str(latest.get("日期", latest.get("date", ""))),
            "profit_ratio": float(
                latest.get("获利比例", latest.get("profit_ratio", 0))
            ),
            "avg_cost": float(latest.get("平均成本", latest.get("avg_cost", 0))),
            "cost_90_low": float(latest.get("90成本-低", latest.get("cost_90_low", 0))),
            "cost_90_high": float(
                latest.get("90成本-高", latest.get("cost_90_high", 0))
            ),
            "concentration_90": float(
                latest.get("90集中度", latest.get("concentration_90", 0))
            ),
            "cost_70_low": float(latest.get("70成本-低", latest.get("cost_70_low", 0))),
            "cost_70_high": float(
                latest.get("70成本-高", latest.get("cost_70_high", 0))
            ),
            "concentration_70": float(
                latest.get("70集中度", latest.get("concentration_70", 0))
            ),
        }
