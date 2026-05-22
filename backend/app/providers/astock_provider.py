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

# mootdx frequency mapping: period str → mootdx frequency int
_FREQ_MAP = {
    "1": 0,   # 1min
    "5": 1,   # 5min
    "15": 2,  # 15min
    "30": 3,  # 30min
    "60": 4,  # 60min
    "101": 9, # daily
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
    ) -> Optional[pd.DataFrame]:
        """Fetch K-line data via mootdx TCP (通达信).

        Returns DataFrame with standardized columns [datetime, open, close,
        high, low, volume, amount] or None on failure.

        Note: mootdx returns unadjusted (不复权) data.  Callers that require
        qfq/hfq adjusted data should rely on the Eastmoney fallback chain
        in fetch_daily_data / fetch_minute_data.
        """
        if _mootdx_client is None:
            return None

        freq = _FREQ_MAP.get(period)
        if freq is None:
            return None

        try:
            klines = _mootdx_client.bars(
                symbol=stock_code, frequency=freq, start=0, offset=count,
            )
            if klines is None or klines.empty:
                return None

            df = klines
            # Keep only standard columns; mootdx returns English names
            # (open, close, high, low, volume, amount) with datetime index.
            cols = ["open", "close", "high", "low", "volume", "amount"]
            available = [c for c in cols if c in df.columns]
            if "datetime" not in df.columns and df.index.name == "datetime":
                df = df.reset_index()
            if "datetime" in df.columns:
                available.insert(0, "datetime")
                df["datetime"] = pd.to_datetime(df["datetime"])
                # Filter by date range
                if start_date:
                    df = df[df["datetime"] >= pd.Timestamp(start_date)]
                if end_date:
                    df = df[df["datetime"] <= pd.Timestamp(end_date)]
                df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                # Guard: mootdx should always return datetime as the index,
                # but if it doesn't, bail out instead of returning a
                # DataFrame with no time column.
                logger.warning(f"mootdx 缺少datetime列 {stock_code}")
                return None
            if df.empty:
                return None
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
    ) -> Optional[pd.DataFrame]:
        # 1. Try mootdx first (TCP, no IP block)
        df = self._fetch_kline_mootdx(
            stock_code, period="101", start_date=start_date, end_date=end_date
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
            logger.warning(
                f"股票 {stock_code} Eastmoney 日线重试耗尽，回退 Tushare"
            )
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
                normalized["日期"] = normalized["日期"].dt.strftime(
                    "%Y-%m-%d 00:00:00"
                )
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
    ) -> Optional[pd.DataFrame]:
        # 1. Try mootdx first (TCP, supports all periods including 1min)
        df = self._fetch_kline_mootdx(
            stock_code, period=period,
            start_date=start_datetime, end_date=end_datetime,
        )
        if df is not None and not df.empty:
            logger.info(f"股票 {stock_code} period={period} 由 mootdx 返回 (共{len(df)}行)")
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
                        df["datetime"] = df["datetime"].dt.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )

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

    def fetch_cyq_data(
        self, stock_code: str, adjust: str = ""
    ) -> Optional[pd.DataFrame]:
        fqt = fqt_code(adjust)
        try:
            df = eastmoney_cyq(code=stock_code, fqt=fqt)
            if df is None or df.empty:
                logger.warning(f"股票 {stock_code} 无筹码分布数据")
                return None
            return df
        except Exception as e:
            logger.error(f"获取股票 {stock_code} 筹码分布数据失败: {e}")
            return None

    def normalize_cyq_data(self, df: pd.DataFrame) -> dict[str, Any]:
        latest = df.iloc[-1]
        return {
            "date": str(latest.get("日期", "")),
            "profit_ratio": float(latest.get("获利比例", 0)),
            "avg_cost": float(latest.get("平均成本", 0)),
            "cost_90_low": float(latest.get("90成本-低", 0)),
            "cost_90_high": float(latest.get("90成本-高", 0)),
            "concentration_90": float(latest.get("90集中度", 0)),
            "cost_70_low": float(latest.get("70成本-低", 0)),
            "cost_70_high": float(latest.get("70成本-高", 0)),
            "concentration_70": float(latest.get("70集中度", 0)),
        }
