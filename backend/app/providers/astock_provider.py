"""AStockDataProvider — implements MarketDataProvider via a-stock-data patterns.

Primary K-line source: mootdx (TCP, no IP block).
Fallback chain: Eastmoney HTTP → Tushare (daily only).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import pandas as pd

from app.core.config import settings
from app.providers.base import MarketDataProvider
from app.providers.provenance import DailyDataResult, DataProvenance
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# mootdx client (lazy, self-healing)
#
# The client is initialized on first use rather than at import time. A transient
# init failure at process start (e.g. TDX mirror handshake dropped mid-connect)
# used to leave ``_mootdx_client = None`` for the whole process lifetime, silently
# disabling the primary K-line source until the container was restarted (VEW-36).
# A lock guards concurrent first-use, and a cooldown prevents hammering the
# mirrors when the handshake keeps failing.
# ---------------------------------------------------------------------------

_mootdx_client: Optional["Quotes"] = None
_mootdx_client_lock = threading.Lock()
_mootdx_init_failed_at: Optional[float] = None
# Cooldown between re-init attempts after a failed handshake (seconds).
_MOOTDX_RETRY_COOLDOWN = 30.0

# Curated public TDX HQ mirrors. Connectivity alone is not enough: some mirrors
# accept the TCP handshake but return no K-line body, so the primary source would
# silently serve nothing (VEW-36). We keep a known-good set and validate each on
# init with a probe fetch, picking the first mirror that actually returns bars.
_MOOTDX_SERVERS: list[tuple[str, int]] = [
    ("115.238.56.198", 7709),
    ("115.238.90.165", 7709),
    ("180.153.18.170", 7709),
    ("60.191.117.167", 7709),
]

# 建连超时（秒）。选中的客户端沿用该超时用于后续取数，故定成常量便于调整。
_MOOTDX_CONNECT_TIMEOUT = 5

# 探针校验的取数周期：深度图默认 5 分钟（frequency=0），日线（frequency=4）作
# 备用。两者都必须能取到才认为镜像可用 —— 只握手、部分周期空回来的镜像不能选。
_MOOTDX_PROBE_FREQUENCIES: tuple[int, ...] = (4, 0)


def _try_mootdx_server(Quotes, server: Optional[tuple[str, int]]):
    """Build a client for one TDX mirror and confirm it returns K-lines.

    ``server`` of ``None`` means "let mootdx use its configured default" (a bare
    ``Quotes.factory``). We probe the two periods the depth chart actually uses —
    daily (frequency=4) and 5-minute (frequency=0) — and accept the mirror only if
    both return bars, otherwise ``None``. A mirror that handshakes but serves one
    period empty can still leave part of the UI blank (VEW-36).
    """
    try:
        if server is None:
            client = Quotes.factory(market="std")
        else:
            client = Quotes.factory(
                market="std", server=server, timeout=_MOOTDX_CONNECT_TIMEOUT
            )
    except Exception as e:
        logger.warning(f"mootdx 连接 {server or '配置默认'} 失败: {e}")
        return None

    for freq in _MOOTDX_PROBE_FREQUENCIES:
        try:
            probe = client.bars(symbol="000001", frequency=freq, start=0, offset=3)
        except Exception as e:  # pragma: no cover - 防御性
            logger.warning(
                f"mootdx 通过 {server or '配置默认'} 拉取 freq={freq} K线失败: {e}"
            )
            return None
        if probe is None or probe.empty:
            logger.warning(
                f"mootdx 镜像 {server or '配置默认'} freq={freq} 未返回有效K线, 跳过"
            )
            return None

    logger.info(f"mootdx 通过 {server or '配置默认'} 取得K线(日线+5分钟), 使用该镜像")
    return client


def _init_mootdx_client():
    """Create a mootdx client connected to a mirror that returns real data.

    ``Quotes.factory(market="std")`` without ``bestip`` just reuses whatever mirror
    is recorded in the local mootdx config, and that mirror may handshake but serve
    no K-line body. Here we probe the curated mirrors (then the configured default
    as a fallback) and keep the first one that returns bars. Returns ``None`` only
    if no mirror yields data. Isolated into its own function so tests can inject a
    failing/succeeding factory without reaching the live TDX mirrors.
    """
    try:
        from mootdx.quotes import Quotes
    except Exception as e:  # pragma: no cover - 依赖缺失
        logger.warning(f"mootdx 依赖不可用: {e}")
        return None

    # Curated mirrors first (fast, reachable), then the configured default.
    candidates: list[Optional[tuple[str, int]]] = list(_MOOTDX_SERVERS) + [None]
    for server in candidates:
        client = _try_mootdx_server(Quotes, server)
        if client is not None:
            return client
    return None


def _get_mootdx_client():
    """Return the mootdx client, lazily (re)initializing it if needed.

    Returns ``None`` only if the handshake failed and the cooldown hasn't elapsed;
    a subsequent call after the cooldown retries. Never permanently wedges the
    primary source the way the old import-time init did.
    """
    global _mootdx_client, _mootdx_init_failed_at
    if _mootdx_client is not None:
        return _mootdx_client

    now = time.monotonic()
    if _mootdx_init_failed_at is not None and (
        now - _mootdx_init_failed_at < _MOOTDX_RETRY_COOLDOWN
    ):
        return None

    with _mootdx_client_lock:
        if _mootdx_client is not None:
            return _mootdx_client
        # Re-check cooldown inside the lock so a failed attempt isn't retried
        # immediately by two requests racing on the first call.
        now = time.monotonic()
        if _mootdx_init_failed_at is not None and (
            now - _mootdx_init_failed_at < _MOOTDX_RETRY_COOLDOWN
        ):
            return None
        client = _init_mootdx_client()
        if client is None:
            _mootdx_init_failed_at = time.monotonic()
            return None
        _mootdx_client = client
        _mootdx_init_failed_at = None
        return _mootdx_client


# Per-attempt sleep multiplier
_RETRY_SLEEP = 0.6


def _norm_request_date(value: str | None) -> str | None:
    """把请求日期统一归一化为 YYYY-MM-DD（入参可能是 YYYYMMDD 或 YYYY-MM-DD）。"""
    if not value:
        return None
    try:
        return str(pd.Timestamp(str(value)).normalize())[:10]
    except Exception:
        return str(value)


def _coverage_gap(
    req_start: str | None,
    req_end: str | None,
    actual_start: str | None,
    actual_end: str | None,
) -> bool:
    """判断实际范围是否覆盖请求范围：起点滞后或终点提前即为覆盖缺口。"""
    if not actual_start or not actual_end:
        return True
    gap = False
    if req_start:
        if pd.Timestamp(actual_start).normalize() > pd.Timestamp(req_start).normalize():
            gap = True
    if req_end:
        if pd.Timestamp(actual_end).normalize() < pd.Timestamp(req_end).normalize():
            gap = True
    return gap


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
        client = _get_mootdx_client()
        if client is None:
            return None

        freq = _FREQ_MAP.get(period)
        if freq is None:
            return None

        try:
            cols = ["open", "close", "high", "low", "volume", "amount"]
            # mootdx 单次最多 800；count>800 时按 start_offset 逐页向前翻，避免长区间静默截断。
            wanted = max(int(count or 500), 1)
            page_size = min(wanted, 800)
            if page_size <= 0:
                return None

            frames: list[pd.DataFrame] = []
            collected = 0
            offset = int(start_offset or 0)
            while collected < wanted:
                klines = client.bars(
                    symbol=stock_code,
                    frequency=freq,
                    start=offset,
                    offset=page_size,
                )
                if klines is None or klines.empty:
                    break
                frames.append(klines)
                n = len(klines)
                collected += n
                offset += n
                if n < page_size:
                    break

            if not frames:
                return None

            df = pd.concat(frames, ignore_index=True)
            if "datetime" in df.columns:
                df = df.reset_index(drop=True)
            else:
                df = df.reset_index()

            if "index" in df.columns and "datetime" not in df.columns:
                df = df.rename(columns={"index": "datetime"})

            if "datetime" not in df.columns:
                logger.warning(f"mootdx 缺少datetime列 {stock_code}")
                return None

            # 分页交界可能重叠，按 datetime 去重后再排序
            df = df.drop_duplicates(subset=["datetime"], keep="last")
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
        # 保持旧签名兼容；带 provenance 的实现见 fetch_daily_data_with_meta
        return self.fetch_daily_data_with_meta(
            stock_code,
            start_date,
            end_date,
            adjust=adjust,
            max_retries=max_retries,
            count=count,
            start_offset=start_offset,
        ).df

    def fetch_daily_data_with_meta(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
        max_retries: int = 2,
        count: int = 500,
        start_offset: int = 0,
    ) -> DailyDataResult:
        """获取日线数据并返回结构化 provenance（source / 复权 / 请求实际范围 / gap / 失败原因）。

        0. 请求入参可能是 YYYYMMDD（provider 层约定），这里统一归一化为 YYYY-MM-DD。
        1. 先试 mootdx（TCP，无 IP 封锁），count>800 时分页取全。
        2. 回退 Eastmoney HTTP。
        3. 回退 Tushare（仅日线）。
        全部失败时 df=None 并给出 failure_reason。
        """
        req_start = _norm_request_date(start_date)
        req_end = _norm_request_date(end_date)
        provenance = DataProvenance(
            source=None,
            adjustment=adjust,
            requested_start=req_start,
            requested_end=req_end,
        )

        # 1. Try mootdx first (TCP, no IP block)
        df = self._fetch_kline_mootdx(
            stock_code,
            period="101",
            start_date=req_start,
            end_date=req_end,
            count=count,
            start_offset=start_offset,
        )
        if df is not None and not df.empty:
            logger.info(f"股票 {stock_code} 日线由 mootdx 返回")
            provenance.source = "mootdx"
            self._fill_provenance(provenance, df, req_start, req_end, adjust)
            return DailyDataResult(df=df, provenance=provenance)

        # 2. Fallback: Eastmoney → Tushare
        fqt = fqt_code(adjust)
        for attempt in range(1, max_retries + 2):
            try:
                df = eastmoney_kline(
                    code=stock_code,
                    klt="101",
                    beg=start_date or "",
                    end=end_date or "",
                    fqt=fqt,
                )
                if df is not None and not df.empty:
                    provenance.source = "eastmoney"
                    self._fill_provenance(provenance, df, req_start, req_end, adjust)
                    return DailyDataResult(df=df, provenance=provenance)
            except Exception as e:
                logger.warning(
                    f"获取股票 {stock_code} 日线数据失败(第{attempt}次): {e}"
                )
            if attempt <= max_retries:
                time.sleep(_RETRY_SLEEP * attempt)
                continue
            logger.warning(f"股票 {stock_code} Eastmoney 日线重试耗尽，回退 Tushare")
            break

        # 3. Tushare
        df = self._fetch_daily_tushare(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            max_retries=settings.TUSHARE_RETRY_TIMES,
        )
        if df is not None and not df.empty:
            provenance.source = "tushare"
            self._fill_provenance(provenance, df, req_start, req_end, adjust)
            return DailyDataResult(df=df, provenance=provenance)

        provenance.failure_reason = "全部数据源无数据或失败"
        return DailyDataResult(df=None, provenance=provenance)

    @staticmethod
    def _fill_provenance(
        provenance: DataProvenance,
        df: pd.DataFrame,
        req_start: Optional[str],
        req_end: Optional[str],
        adjust: str,
    ) -> None:
        """从实际返回的 df 填充 provenance 的范围、bar 数、覆盖缺口。"""
        if df is None or df.empty or "datetime" not in df.columns:
            provenance.bar_count = 0
            provenance.gap = True
            return
        dates = pd.to_datetime(df["datetime"])
        actual_start = str(dates.min())[:10]
        actual_end = str(dates.max())[:10]
        provenance.actual_start = actual_start
        provenance.actual_end = actual_end
        provenance.bar_count = int(len(df))
        provenance.last_bar = actual_end
        provenance.adjustment = adjust
        provenance.gap = _coverage_gap(req_start, req_end, actual_start, actual_end)

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
