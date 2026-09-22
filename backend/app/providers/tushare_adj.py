"""Tushare adj_factor 缓存与配额管理（VEW-55）。

背景：当前 Tushare 账号的 ``adj_factor`` 接口配额极低（约 5 次/天 + 1 次/分钟 +
1 次/小时），而 qfq/hfq 日线依赖该因子。每次请求都直接调 ``pro_bar(adj=...)``
会快速撞限，把整个 Tushare 备源拖垮。

设计：
- 按 ``ts_code`` 缓存复权因子序列，内存 + 磁盘（CSV）双份，跨进程/重启复用；
- 每日配额守卫：记录当日调用次数（持久化到 ``quota.json``），超过
  ``TUSHARE_ADJ_FACTOR_DAILY_QUOTA`` 后拒绝真实拉取，只返回缓存或 None；
- 最小间隔守卫：两次真实调用之间至少间隔 ``TUSHARE_ADJ_FACTOR_MIN_INTERVAL``
  秒，规避 1 次/分钟 的限制；
- 新鲜度信号：每次拉取记录日期（``cache_meta.json`` 持久化），
  ``cache_date(ts_code)`` 暴露缓存日期；超过 ``TUSHARE_ADJ_FACTOR_CACHE_TTL_DAYS``
  视为陈旧，配额允许时在请求路径上顺手刷新，配额耗尽时仍返回陈旧缓存
  （比完全失败好，且调用方/前端能经 ``adjust_factor_date`` 感知陈旧）。

调用方（astock_provider / akshare_provider）在 adj_factor 不可用时降级为非复权，
而不是让整次日线请求失败。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from app.core.config import settings

try:
    import tushare as ts
except Exception:  # pragma: no cover - 依赖可选
    ts = None

logger = logging.getLogger(__name__)


def _cache_dir() -> Path:
    """adj_factor 磁盘缓存目录（相对 backend/ 解析，避免依赖启动 cwd）。"""
    base = Path(__file__).parent.parent.parent  # backend/
    return base / getattr(settings, "TUSHARE_ADJ_FACTOR_CACHE_DIR", "data/tushare_adj")


class AdjFactorStore:
    """Tushare adj_factor 的缓存 + 配额守卫。

    线程安全：内部锁保护缓存字典与配额状态；真实网络调用放在锁外执行，避免
    持锁等待网络。
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        daily_quota: Optional[int] = None,
        min_interval: Optional[int] = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else _cache_dir()
        self.daily_quota = (
            daily_quota
            if daily_quota is not None
            else int(getattr(settings, "TUSHARE_ADJ_FACTOR_DAILY_QUOTA", 5))
        )
        self.min_interval = (
            min_interval
            if min_interval is not None
            else int(getattr(settings, "TUSHARE_ADJ_FACTOR_MIN_INTERVAL", 60))
        )
        self.cache_ttl_days = int(
            getattr(settings, "TUSHARE_ADJ_FACTOR_CACHE_TTL_DAYS", 7)
        )
        self._lock = threading.Lock()
        self._factors: dict[str, pd.DataFrame] = {}
        self._fetched_at: dict[str, str] = {}  # ts_code -> 最近拉取日期 YYYY-MM-DD
        self._last_fetch_at: Optional[float] = None
        self._quota_date: Optional[str] = None
        self._quota_count = 0
        self._load_quota_state()
        self._load_cache_meta()

    # ------------------------------------------------------------------
    # 配额状态（每日持久化）
    # ------------------------------------------------------------------

    def _quota_state_path(self) -> Path:
        return self.cache_dir / "quota.json"

    def _cache_meta_path(self) -> Path:
        return self.cache_dir / "cache_meta.json"

    def _load_quota_state(self) -> None:
        try:
            with open(self._quota_state_path(), encoding="utf-8") as f:
                state = json.load(f)
            self._quota_date = state.get("date")
            self._quota_count = int(state.get("count", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            self._quota_date = None
            self._quota_count = 0

    def _load_cache_meta(self) -> None:
        """恢复各 ts_code 的缓存拉取日期（无记录时用文件 mtime 兜底）。"""
        try:
            with open(self._cache_meta_path(), encoding="utf-8") as f:
                meta = json.load(f)
            self._fetched_at = {
                str(k): str(v) for k, v in (meta or {}).items() if isinstance(v, str)
            }
        except (OSError, ValueError, json.JSONDecodeError):
            self._fetched_at = {}

    def _save_cache_meta(self) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._cache_meta_path(), "w", encoding="utf-8") as f:
                json.dump(self._fetched_at, f)
        except OSError as e:  # pragma: no cover - 只读文件系统等
            logger.warning(f"adj_factor 缓存元信息持久化失败: {e}")

    def _save_quota_state(self) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._quota_state_path(), "w", encoding="utf-8") as f:
                json.dump({"date": self._quota_date, "count": self._quota_count}, f)
        except OSError as e:  # pragma: no cover - 只读文件系统等
            logger.warning(f"adj_factor 配额状态持久化失败: {e}")

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def _refresh_today_count(self) -> None:
        """跨天时重置当日计数。"""
        today = self._today()
        if self._quota_date != today:
            self._quota_date = today
            self._quota_count = 0

    @property
    def quota_remaining(self) -> int:
        with self._lock:
            self._refresh_today_count()
            return max(0, self.daily_quota - self._quota_count)

    def _can_fetch(self) -> bool:
        self._refresh_today_count()
        if self._quota_count >= self.daily_quota:
            return False
        if self._last_fetch_at is not None:
            elapsed = time.monotonic() - self._last_fetch_at
            if elapsed < self.min_interval:
                return False
        return True

    def _record_fetch(self) -> None:
        """调用前占额；调用失败时由调用方 _refund_fetch 退还。"""
        self._refresh_today_count()
        self._quota_count += 1
        self._last_fetch_at = time.monotonic()
        self._save_quota_state()

    def _refund_fetch(self) -> None:
        self._refresh_today_count()
        if self._quota_count > 0:
            self._quota_count -= 1
        self._save_quota_state()

    # ------------------------------------------------------------------
    # 缓存读写
    # ------------------------------------------------------------------

    def _cache_path(self, ts_code: str) -> Path:
        safe = ts_code.replace(".", "_")
        return self.cache_dir / f"{safe}.csv"

    def _load_from_disk(self, ts_code: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(ts_code)
        try:
            df = pd.read_csv(path, dtype={"trade_date": str})
        except (OSError, ValueError):  # 文件不存在或损坏
            return None
        if df is None or df.empty:
            return None
        # 无缓存元信息（升级前写入的 CSV）时，用文件 mtime 作为缓存日期兜底
        if ts_code not in self._fetched_at:
            try:
                self._fetched_at[ts_code] = time.strftime(
                    "%Y-%m-%d", time.localtime(path.stat().st_mtime)
                )
            except OSError:  # pragma: no cover - 文件已被删除
                pass
        return df.sort_values("trade_date").reset_index(drop=True)

    def _save_to_disk(self, ts_code: str, df: pd.DataFrame) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            df.to_csv(self._cache_path(ts_code), index=False)
        except OSError as e:  # pragma: no cover - 只读文件系统等
            logger.warning(f"adj_factor 磁盘缓存写入失败 {ts_code}: {e}")
        self._fetched_at[ts_code] = self._today()
        self._save_cache_meta()

    def get_cached(self, ts_code: str) -> Optional[pd.DataFrame]:
        """返回缓存的 adj_factor 序列（trade_date 升序），无缓存返回 None。"""
        with self._lock:
            if ts_code in self._factors:
                return self._factors[ts_code]
        df = self._load_from_disk(ts_code)
        if df is not None:
            with self._lock:
                self._factors[ts_code] = df
        return df

    def cache_date(self, ts_code: str) -> Optional[str]:
        """返回 ts_code 最近一次拉取 adj_factor 的日期（YYYY-MM-DD），无缓存返回 None。"""
        if ts_code in self._fetched_at:
            return self._fetched_at[ts_code]
        # 内存没有但磁盘可能有（_load_from_disk 会回填 _fetched_at）
        if self.get_cached(ts_code) is not None:
            return self._fetched_at.get(ts_code)
        return None

    @staticmethod
    def _is_stale(fetched_date: Optional[str], ttl_days: int) -> bool:
        """fetched_date 距今是否超过 ttl_days 天。无日期记录视为陈旧。"""
        if not fetched_date:
            return True
        try:
            fetched = pd.Timestamp(fetched_date).normalize()
            age = (pd.Timestamp.now().normalize() - fetched).days
        except (ValueError, TypeError):
            return True
        return age > ttl_days

    # ------------------------------------------------------------------
    # 真实拉取（配额守卫）
    # ------------------------------------------------------------------

    def _fetch_from_tushare(self, ts_code: str) -> Optional[pd.DataFrame]:
        if ts is None:
            logger.warning("tushare 依赖未安装，无法拉取 adj_factor")
            return None
        if not settings.TUSHARE_TOKEN:
            logger.warning("Tushare 未配置 token，无法拉取 adj_factor")
            return None

        try:
            ts.set_token(settings.TUSHARE_TOKEN)
            pro = ts.pro_api()
            df = pro.adj_factor(ts_code=ts_code)
        except Exception as e:
            logger.error(f"Tushare adj_factor 拉取失败 {ts_code}: {e}")
            return None

        if df is None or df.empty or "adj_factor" not in df.columns:
            logger.warning(f"Tushare adj_factor 返回为空 {ts_code}")
            return None
        out = df[["trade_date", "adj_factor"]].copy()
        out["trade_date"] = out["trade_date"].astype(str)
        return out.sort_values("trade_date").reset_index(drop=True)

    def get_or_fetch(
        self, ts_code: str, max_age_days: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """返回 ts_code 的 adj_factor 序列。

        优先级：
        1. 缓存命中且未超过新鲜度阈值（默认 TUSHARE_ADJ_FACTOR_CACHE_TTL_DAYS）→ 直接返回；
        2. 缓存陈旧且配额允许 → 刷新（成功替换缓存；失败保留陈旧缓存并告警，返回陈旧值）；
        3. 无缓存且配额允许 → 真实拉取并写缓存；
        4. 配额耗尽/间隔未到 → 返回缓存（哪怕陈旧）或 None（调用方降级为非复权）。
        """
        max_age_days = (
            self.cache_ttl_days if max_age_days is None else int(max_age_days)
        )
        cached = self.get_cached(ts_code)
        if cached is not None:
            fetched = self.cache_date(ts_code)
            if not self._is_stale(fetched, max_age_days):
                return cached
            # 陈旧缓存：配额允许则顺手刷新；否则降级返回陈旧值（可经 cache_date 感知）。
            refreshed = self._fetch_if_allowed(ts_code)
            if refreshed is not None:
                return refreshed
            logger.warning(
                f"adj_factor 缓存已陈旧（{fetched}），配额/间隔受限，返回陈旧缓存 {ts_code}"
            )
            return cached

        return self._fetch_if_allowed(ts_code)

    def _fetch_if_allowed(self, ts_code: str) -> Optional[pd.DataFrame]:
        """配额/间隔守卫下真实拉取；不允许或失败返回 None。"""
        with self._lock:
            if not self._can_fetch():
                logger.warning(
                    f"adj_factor 配额/间隔受限，跳过拉取 {ts_code} "
                    f"(今日剩余 {max(0, self.daily_quota - self._quota_count)})"
                )
                return None
            self._record_fetch()

        df = self._fetch_from_tushare(ts_code)
        if df is None:
            # 失败不占配额，退还给后续请求
            with self._lock:
                self._refund_fetch()
            return None

        self._save_to_disk(ts_code, df)
        with self._lock:
            self._factors[ts_code] = df
        return df


# 模块级单例
adj_factor_store = AdjFactorStore()


def get_adj_factor(ts_code: str) -> Optional[pd.DataFrame]:
    """便捷入口：返回 ts_code 的 adj_factor 序列（缓存优先，配额守卫）。"""
    return adj_factor_store.get_or_fetch(ts_code)


def apply_adjust(
    df: pd.DataFrame, adj: pd.DataFrame, adjust: str
) -> Optional[pd.DataFrame]:
    """用 adj_factor 序列对非复权日线做 qfq/hfq 复权。

    ``df`` 需含 ``trade_date``（YYYYMMDD 字符串）与 OHLC 列；``adj`` 需含
    ``trade_date`` 与 ``adj_factor`` 列（trade_date 升序）。qfq 用最新因子归一
    （``factor = adj_factor / adj_factor[-1]``，标准前复权），hfq 直接用 adj_factor。
    因子缺失（NaN）时返回 None，由调用方降级为非复权。

    口径注意（VEW-55）：Tushare 服务端 ``pro_bar(adj='qfq')`` 是「请求窗口首日
    因子归一」，而这里用「最新因子归一」，两者绝对值不同；统一走本地复权后
    qfq 价位与旧口径有偏移，volume profile / CYQ 分箱需按最新口径理解。
    """
    if df is None or df.empty or adj is None or adj.empty:
        return None
    if "trade_date" not in df.columns or "adj_factor" not in adj.columns:
        return None

    adj = adj.sort_values("trade_date").reset_index(drop=True)
    if adjust not in {"qfq", "hfq"}:
        return df.copy()

    merged = df.merge(adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
    merged["adj_factor"] = merged["adj_factor"].ffill().bfill()
    if merged["adj_factor"].isna().any():
        return None

    if adjust == "qfq":
        latest = float(adj["adj_factor"].iloc[-1])
        if latest <= 0:
            return None
        factor = merged["adj_factor"] / latest
    else:
        factor = merged["adj_factor"]

    out = merged.drop(columns=["adj_factor"]).copy()
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col] * factor
    return out
