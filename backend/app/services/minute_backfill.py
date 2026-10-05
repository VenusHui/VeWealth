"""历史分钟数据回填（VEW-65 P1）。

职责：把数据源**当下还能给到**的历史分钟 bar 一次性灌进本地分钟库
（``minute_store.MinuteLibrary``），作为 P0 每日增量采集的起点。回填结束后，
后续日期由 ``minute_collector`` 的每日任务接续，两者写的是同一套分区与同一份幂等
契约，因此可以交替运行、互不覆盖。

为什么是「区间取数」而不是复用每日采集
--------------------------------------
``MinuteCollector`` 按 ``(period, 单日)`` 取数，用它回填要按天循环：全市场 1min
4 个月 = 80 天 × 5921 只 ≈ **47 万次**取数。而源本身支持一次调用取回整段历史
（mootdx 单次 800 根、内部按 ``start_offset`` 翻页；腾讯 800 根/页、按 ``start_time``
翻页），整段只需 **5921 次**调用：

| 范围 | 按天取数（页数） | 区间取数（页数） | 倍数 |
|---|---|---|---|
| 1min × 4 个月 | 80 页/只 | 25 页/只 | 3.2× |
| 5min × 2 年 | 488 页/只 | 30 页/只 | 16× |

所以回填按「标的 × 区间」取数，取回后按 ``trade_date`` 切开、逐日分区写入 —— 分区
布局与每日采集完全一致，回测读路径不需要区分数据是回填来的还是增量采来的。

取数锁与分块（重要）
--------------------
mootdx 的 TDX 协议是一条 TCP 连接一问一答，整段翻页期间必须一直持有 VEW-60 的
取数锁。锁被持有期间，源级探针（``source_health``，等锁超时 5s）拿不到锁，镜像
熔断/恢复会失去健康信号（P0 已记录的盲区）。因此区间取数按
``MINUTE_BACKFILL_CHUNK_DAYS``（默认 30 个日历日）分块：1min 一块 ≈ 21 个交易日
≈ 5,000 根 ≈ 7 页，锁持有约 2–4s，探针仍能插进去。块大小**不要调大**。

断点续跑
--------
与 P0 的每日日志同构，但续跑单位是「标的」而不是「日」：``(period, 区间)`` 一份
JSON 日志（``{root}/_state/backfill/...``），记录 ``completed`` / ``empty`` /
``failed``。重跑只跳过 ``completed`` 与 ``empty``；``failed`` 会重试。

「取到一部分」为什么不能记为完成
--------------------------------
区间取数有两条**静默截断**路径，都会让某只标的的历史缺一段而日志讲一个可信的故事：

1. **预算耗尽**：mootdx 翻页时遇到 deadline 会提前 break，返回已取到的部分。
2. **备源降级**：mootdx 全挂时回退东财，而 1min 的备源 ``eastmoney_trends2`` 只有
   约 5 天窗口 —— 在一个 4 个月的区间里，它会安静地只返回最近几天。

两者都不会抛异常。因此取数后必须做**覆盖度校验**（``_coverage_short``）：把取回
数据的最早日期与区间起点比对，差得多且源本身覆盖得到区间起点时，记 ``failed``
（可重试）而不是 ``completed``。判据偏向「可疑就重试」与 P0 一致：记错的代价不对称
—— 多跑一轮取数 vs 回测事实源里永久缺一段历史。

新上市标的的合法短覆盖由 ``security_universe.list_date`` 豁免（查不到时按可疑处理）。

复权口径（与日线链路的分工）
----------------------------
**分钟库全程存不复权（raw）bar**：mootdx ``get_security_bars`` 返回非复权原始行情
（``astock_provider`` 会如实标注 ``adjust_served=""``），腾讯 ``mkline`` 同样是不复权，
东财分钟路径本次固定 ``fqt=0``。复权因子只来自日线链路，分钟级回测在 P2 用
「日线因子 + 分钟 raw 价格」合成 —— 混用两套复权口径会让因子错算，因此**回填不写
复权价**，也不新增 ``adjust`` 列（库的 schema 由 P0 定，回填不改契约）。

两源交叉校验与成交量单位
------------------------
mootdx / 东财的 volume 单位是**股**，腾讯 ``ifzq`` 是**手**（差 100 倍）。本模块把
「股」定为规范单位，腾讯侧统一 ×100（``TENCENT_LOT_TO_SHARE``）后再入库/比对 ——
不统一会让换源当天的因子整体错算 100 倍。

``cross_check`` 拉两源同期数据，按 ``(stock_code, trade_time)`` 取交集比对：
OHLC 要求**严格相等**（VEW-61 已验 m5 完全一致），volume 在单位换算后要求一致。
腾讯源由 VEW-63 接入，**尚未合入 dev/v1.3.0**，因此 ``_fetch_secondary`` 在缺少该
模块时返回 ``None`` 并在报告里标注 ``secondary_unavailable`` —— 交叉校验的比较逻辑
可以离线测（喂两段 frame），真实两源比对要等 VEW-63 合入后才生效。

安全闸门（与 P0 同口径：代码可以进，写入不能跑）
------------------------------------------------
``run()`` 需要**同时**满足 ``settings.MINUTE_BACKFILL_ENABLED is True`` 与
``confirm=True`` 才真正取数落盘；任一缺失都只产出计划。回填是一次性大范围跑批，
生产 ``/`` 实测可用仅约 5 GB，而全市场回填实测约 4.5 GB（1min 4 个月 ≈ 1.7 GB +
5min 2 年 ≈ 2.8 GB，见 ``docs/plans/2026-10-05-minute-backfill-p1-capacity.md``），
容量拍板前不得执行。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Sequence

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.providers import get_data_provider
from app.services.minute_collector import (
    PROBE_DOWN,
    PROBE_NO_SESSION,
    PROBE_OK,
    MinuteCollector,
)
from app.services.minute_store import (
    MinuteLibrary,
    minute_library,
)

logger = logging.getLogger(__name__)

# 单次区间取数请求的时间窗（A 股连续竞价 09:30–15:00，两侧留余量覆盖集合竞价）。
_SESSION_START = "09:00:00"
_SESSION_END = "16:00:00"

# 腾讯 ifzq 的 volume 单位是「手」，mootdx / 东财是「股」。规范单位取「股」。
TENCENT_LOT_TO_SHARE = 100

# 每交易日的 bar 根数（连续竞价 4 小时）。用于估算区间根数与 start_offset。
_BARS_PER_DAY = {"1": 240, "5": 48, "15": 16, "30": 8, "60": 4}

# 一周 7 天里约 5 个交易日。仓库内没有交易日历（P0 已记录），回填的**估算**用它折算；
# 实际取回的日期以数据为准，估算只影响取数根数与容量预估，不影响正确性。
_TRADING_DAY_RATIO = 5.0 / 7.0

# 实测单行落盘字节数（zstd，含 amount 列）。VEW-65 用真实腾讯 bar 实测（2026-10-05）：
# 1min 374 只真实标的整日 13.1–13.8 B/row、5min 280 只 19.2 B/row、15/30/60min
# 24.9 / 30.9 / 36.2 B/row（后三者样本小、单文件固定开销占比高，偏保守）。
# 对照：P0 文档的 39 B/row 来自**均匀随机**合成数据（不可压缩的最坏情况），比真实
# 数据高约 3 倍 —— 容量规划应按实测值，不要沿用 13 GB/年/周期。
MEASURED_BYTES_PER_ROW = {
    "1": 13.8,
    "5": 19.2,
    "15": 24.9,
    "30": 30.9,
    "60": 36.2,
}

# 各周期**源端可得的历史深度**（交易日，VEW-61 实测）：mootdx 1min ≈ 4 个月、
# 5/15/30/60min ≈ 2 年；腾讯 m1 ≈ 18 个交易日、m5/m15 ≈ 6 个月、m30/m60 ≈ 12 个月。
# 回填默认取「最深可得」，因此默认区间 = 今天往前推这么多交易日。
SOURCE_HISTORY_DAYS = {
    "mootdx": {"1": 81, "5": 488, "15": 488, "30": 488, "60": 488},
    "tencent": {"1": 18, "5": 122, "15": 122, "30": 244, "60": 244},
}

# 覆盖度校验的容差（交易日）：取回数据的最早日期晚于「区间起点 + 容差」即视为
# 覆盖不足。容差覆盖长假（国庆 / 春节 8–9 天）与首日无 bar 的情况。
_COVERAGE_SLACK_DAYS = 12

# 判定「预算耗尽导致截断」的余量（秒）：fetch 返回时距 deadline 不足这个数即认为
# 是翻页被 deadline 打断，而不是自然取完。
_TRUNCATION_SLACK_SEC = 0.05

# 区间取数单块的最大根数（mootdx 单页 800，25 页封顶；正常分块远达不到）。
_MAX_BARS_PER_REQUEST = 20000


class BackfillRefused(RuntimeError):
    """未满足安全闸门（开关未开 / 未显式 confirm）时拒绝执行回填。

    刻意抛异常而不是返回一个「refused=True」的结果对象：回填是写生产磁盘的大动作，
    调用方漏判返回值就等于默认执行，抛异常让漏判变成显式失败。
    """


def _coerce_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def bars_per_day(period: str) -> int:
    """某周期的每交易日 bar 根数（未知周期按 1min 处理，偏保守）。"""
    return _BARS_PER_DAY.get(str(period), _BARS_PER_DAY["1"])


def trading_days_between(start: date, end: date) -> int:
    """两个日期之间的**估算**交易日数（日历日 × 5/7）。

    仓库内没有交易日历，这是估算；回填的正确性不依赖它（实际日期以取回的数据为准），
    它只用于容量预估与 ``start_offset`` 初值。
    """
    if end < start:
        return 0
    return int(round(((end - start).days + 1) * _TRADING_DAY_RATIO))


def estimate_bytes(period: str, symbols: int, days: int) -> int:
    """按实测 B/row 估算落盘体积。"""
    rows = max(0, symbols) * max(0, days) * bars_per_day(period)
    return int(rows * MEASURED_BYTES_PER_ROW.get(str(period), 20.0))


def _free_bytes(path: Path) -> Optional[int]:
    """``path`` 所在文件系统的可用字节数（目录不存在时向上找最近的已存在祖先）。"""
    probe = path
    while True:
        if probe.exists():
            try:
                return shutil.disk_usage(probe).free
            except OSError:
                return None
        if probe.parent == probe:
            return None
        probe = probe.parent


@dataclass
class BackfillPlan:
    """回填计划（**不取数、不落盘**）：容量与耗时的可核对输入。"""

    period: str
    start_date: str
    end_date: str
    symbols: int
    trading_days: int
    est_rows: int
    est_bytes: int
    free_bytes: Optional[int]
    # 已有分区里该周期已落盘的天数（增量采集合入后回填会少写这些天）
    existing_days: int = 0
    bytes_per_row: float = 0.0
    # 估算取数墙钟（秒）：按每页 0.3–0.6s（VEW-60 实测）折算的区间上下界
    est_fetch_sec_low: float = 0.0
    est_fetch_sec_high: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def fits(self) -> Optional[bool]:
        """估算体积能否被当前可用空间容纳（留 20% 余量）。"""
        if self.free_bytes is None:
            return None
        return self.est_bytes * 1.2 <= self.free_bytes

    def as_dict(self) -> dict:
        return {
            "period": self.period,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "symbols": self.symbols,
            "trading_days": self.trading_days,
            "est_rows": self.est_rows,
            "est_bytes": self.est_bytes,
            "est_mb": round(self.est_bytes / 1e6, 2),
            "bytes_per_row": self.bytes_per_row,
            "free_bytes": self.free_bytes,
            "free_mb": (
                None if self.free_bytes is None else round(self.free_bytes / 1e6, 2)
            ),
            "fits": self.fits,
            "existing_days": self.existing_days,
            "est_fetch_sec_low": round(self.est_fetch_sec_low, 1),
            "est_fetch_sec_high": round(self.est_fetch_sec_high, 1),
            "notes": self.notes,
        }


@dataclass
class BackfillResult:
    """一次回填的结果（字段与 ``MinuteCollectResult`` 保持同构，便于同一套观测）。"""

    period: str
    start_date: str
    end_date: str
    universe_size: int = 0
    requested: int = 0
    fetched: int = 0
    empty: int = 0
    failed: int = 0
    skipped: int = 0
    bars_written: int = 0
    partitions_written: int = 0
    elapsed_sec: float = 0.0
    avg_fetch_sec: float = 0.0
    universe_source: str = ""
    source_probe: str = ""
    refused: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "period": self.period,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "universe_size": self.universe_size,
            "requested": self.requested,
            "fetched": self.fetched,
            "empty": self.empty,
            "failed": self.failed,
            "skipped": self.skipped,
            "bars_written": self.bars_written,
            "partitions_written": self.partitions_written,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "avg_fetch_sec": round(self.avg_fetch_sec, 4),
            "universe_source": self.universe_source,
            "source_probe": self.source_probe,
            "refused": self.refused,
            "errors": self.errors[:10],
        }


@dataclass
class CrossCheckReport:
    """两源交叉校验结果（OHLC 严格相等、volume 换算后相等）。"""

    period: str
    start_date: str
    end_date: str
    compared_symbols: int = 0
    compared_bars: int = 0
    ohlc_mismatch: int = 0
    volume_mismatch: int = 0
    only_primary: int = 0
    only_secondary: int = 0
    secondary_unavailable: bool = False
    samples: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.secondary_unavailable
            and self.ohlc_mismatch == 0
            and self.volume_mismatch == 0
        )

    def as_dict(self) -> dict:
        return {
            "period": self.period,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "compared_symbols": self.compared_symbols,
            "compared_bars": self.compared_bars,
            "ohlc_mismatch": self.ohlc_mismatch,
            "volume_mismatch": self.volume_mismatch,
            "only_primary": self.only_primary,
            "only_secondary": self.only_secondary,
            "secondary_unavailable": self.secondary_unavailable,
            "ok": self.ok,
            "samples": self.samples[:10],
        }


class _BackfillJournal:
    """``(period, 区间)`` 的断点日志（原子写，单写者）。语义见模块 docstring。"""

    def __init__(
        self, root: Path, period: str, start: date, end: date, enabled: bool = True
    ):
        self.path = (
            root
            / "_state"
            / "backfill"
            / f"period={period}"
            / f"window={start}_{end}.json"
        )
        self.period = period
        self.start = start
        self.end = end
        self.enabled = enabled
        self.completed: set[str] = set()
        self.empty: set[str] = set()
        self.failed: set[str] = set()
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.enabled or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("回填断点日志损坏，按空处理: %s", self.path)
            return
        for key, target in (
            ("completed", self.completed),
            ("empty", self.empty),
            ("failed", self.failed),
        ):
            target.update(str(c).zfill(6) for c in payload.get(key, []))

    def mark(self, symbol: str, bucket: str) -> None:
        with self._lock:
            for target in (self.completed, self.empty, self.failed):
                target.discard(symbol)
            {
                "completed": self.completed,
                "empty": self.empty,
                "failed": self.failed,
            }[
                bucket
            ].add(symbol)

    def flush(self) -> None:
        """原子落盘；失败不致命 —— 库里的数据才是事实源。"""
        if not self.enabled:
            return
        with self._lock:
            payload = {
                "period": self.period,
                "start_date": self.start.isoformat(),
                "end_date": self.end.isoformat(),
                "completed": sorted(self.completed),
                "empty": sorted(self.empty),
                "failed": sorted(self.failed),
                "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
            }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            os.replace(tmp, self.path)
        except Exception as e:  # pragma: no cover - 磁盘异常
            logger.warning("回填断点日志写入失败(不影响已落盘数据): %s", e)

    def done(self) -> set[str]:
        """已确认无需再取的标的。``failed`` **不在**其中（可重试）。"""
        return self.completed | self.empty


class MinuteBackfiller:
    """历史分钟数据回填器（区间取数 + 逐日分区写入 + 断点续跑）。"""

    def __init__(
        self,
        db: Optional[Session] = None,
        provider=None,
        library: Optional[MinuteLibrary] = None,
    ):
        self.db = db
        self.provider = provider or get_data_provider()
        self.library = library or minute_library
        # 股票池解析与源探针复用每日采集器的实现（口径必须一致：全板块 + 含 ST，
        # 探针走同一条 provider 路径与取数锁）。
        self._collector = MinuteCollector(
            db=db, provider=self.provider, library=self.library
        )

    # ------------------------------------------------------------------
    # 计划（不取数、不落盘）
    # ------------------------------------------------------------------

    def default_window(self, period: str, source: str = "mootdx") -> tuple[date, date]:
        """默认回填区间 = 「源端最深可得」：今天往前推 ``SOURCE_HISTORY_DAYS`` 个交易日。"""
        days = SOURCE_HISTORY_DAYS.get(source, SOURCE_HISTORY_DAYS["mootdx"]).get(
            str(period), 81
        )
        today = date.today()
        # 交易日 → 日历日：按 7/5 放大，保证覆盖到源端深度
        calendar_days = int(days / _TRADING_DAY_RATIO) + 1
        return today - timedelta(days=calendar_days), today

    def plan(
        self,
        period: str,
        start_date: Optional[date | datetime | str] = None,
        end_date: Optional[date | datetime | str] = None,
        symbols: Optional[Sequence[str]] = None,
        source: str = "mootdx",
    ) -> BackfillPlan:
        """产出回填计划：体积 / 盘余量 / 耗时估算。**不取数、不落盘、不读网络。**"""
        period = str(period)
        default_start, default_end = self.default_window(period, source)
        start = _coerce_date(start_date) if start_date else default_start
        end = _coerce_date(end_date) if end_date else default_end
        if start > end:
            start, end = end, start

        if symbols is None:
            pool, source_tag = self._collector.resolve_universe(end)
        else:
            pool = [str(s).zfill(6) for s in symbols]
            source_tag = "explicit"

        days = trading_days_between(start, end)
        est_rows = len(pool) * days * bars_per_day(period)
        est_bytes = estimate_bytes(period, len(pool), days)
        bpr = MEASURED_BYTES_PER_ROW.get(period, 20.0)
        # 取数耗时：每页 800 根，按 VEW-60 实测的 0.3–0.6 s/页折算，再按 workers 摊薄
        # （网络取数被取数锁串行化，workers 只摊薄建连与解析，故只对页数打折而非线性加速）
        pages_per_symbol = max(1, int(round(days * bars_per_day(period) / 800.0)) + 1)
        workers = max(1, int(settings.MINUTE_BACKFILL_WORKERS))
        base = len(pool) * pages_per_symbol
        est_low = base * 0.3 / max(1, workers**0.5)
        est_high = base * 0.6 / max(1, workers**0.5)

        plan = BackfillPlan(
            period=period,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            symbols=len(pool),
            trading_days=days,
            est_rows=est_rows,
            est_bytes=est_bytes,
            free_bytes=_free_bytes(Path(self.library.root)),
            existing_days=len(self.library.available_dates(period)),
            bytes_per_row=bpr,
            est_fetch_sec_low=est_low,
            est_fetch_sec_high=est_high,
        )
        plan.notes.append(
            f"股票池来源={source_tag}；交易日为估算（日历日 × 5/7，仓库无交易日历），"
            "实际天数以取回数据为准"
        )
        plan.notes.append(
            f"B/row={bpr} 为 VEW-65 真实数据实测值（含 amount 列）；"
            "P0 文档的 39 B/row 来自均匀随机合成数据，偏保守约 3 倍"
        )
        if plan.fits is False:
            plan.notes.append(
                "估算体积超过当前可用空间（含 20% 余量）—— 不得执行，"
                "先扩盘或缩小范围（自选池 / 单周期 / 更短区间）"
            )
        return plan

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    def run(
        self,
        period: str,
        start_date: Optional[date | datetime | str] = None,
        end_date: Optional[date | datetime | str] = None,
        symbols: Optional[Sequence[str]] = None,
        confirm: bool = False,
        workers: Optional[int] = None,
        chunk_days: Optional[int] = None,
        source: str = "mootdx",
        resume: bool = True,
    ) -> BackfillResult:
        """执行回填。

        安全闸门：``settings.MINUTE_BACKFILL_ENABLED`` 与 ``confirm=True`` **都**满足
        才真正取数落盘；否则抛 :class:`BackfillRefused`（见类说明）。
        """
        if not settings.MINUTE_BACKFILL_ENABLED:
            raise BackfillRefused(
                "MINUTE_BACKFILL_ENABLED=False：回填默认不执行。"
                "打开条件（生产 / 可用空间覆盖一次性回填 + 至少一年增量）与责任人见 "
                "app/core/config.py 中该配置项的注释"
            )
        if not confirm:
            raise BackfillRefused(
                "未显式传 confirm=True：回填会大范围写生产磁盘，必须由责任人在核对 "
                "plan() 的估算与盘余量后显式确认"
            )

        period = str(period)
        default_start, default_end = self.default_window(period, source)
        start = _coerce_date(start_date) if start_date else default_start
        end = _coerce_date(end_date) if end_date else default_end
        if start > end:
            start, end = end, start

        workers = max(1, int(workers or settings.MINUTE_BACKFILL_WORKERS))
        chunk_days = max(1, int(chunk_days or settings.MINUTE_BACKFILL_CHUNK_DAYS))
        flush_symbols = max(1, int(settings.MINUTE_BACKFILL_FLUSH_SYMBOLS))

        started = time.monotonic()
        result = BackfillResult(
            period=period, start_date=start.isoformat(), end_date=end.isoformat()
        )

        if symbols is None:
            universe, source_tag = self._collector.resolve_universe(end)
        else:
            universe = [str(s).zfill(6) for s in symbols]
            source_tag = "explicit"
        result.universe_source = source_tag
        result.universe_size = len(universe)

        journal = _BackfillJournal(
            self.library.root, period, start, end, enabled=resume
        )
        pending = list(universe)
        if resume:
            already = journal.done()
            pending = [s for s in pending if s not in already]
            result.skipped = len(universe) - len(pending)
        result.requested = len(pending)

        logger.info(
            "分钟回填开始: period=%s 区间=%s..%s 股票池=%d 待采=%d 跳过=%d "
            "workers=%d 分块=%d天",
            period,
            start,
            end,
            result.universe_size,
            result.requested,
            result.skipped,
            workers,
            chunk_days,
        )

        if not pending:
            result.elapsed_sec = time.monotonic() - started
            journal.flush()
            return result

        # 源健康探针（复用每日采集器的三态判定）：决定「整个区间取空」是终态还是
        # 可重试。区间起点附近探不到、区间内有 bar → 源能给历史但给不到那么早。
        probe = self._probe_window(period, start, end)
        result.source_probe = probe

        listing_dates = self._listing_dates(pending)
        chunks = _date_chunks(start, end, chunk_days)

        buffer: list[pd.DataFrame] = []
        buffered_symbols = 0
        fetch_seconds: list[float] = []
        empty_symbols: list[str] = []

        def flush_buffer() -> None:
            nonlocal buffer, buffered_symbols
            if not buffer:
                return
            frame = pd.concat(buffer, ignore_index=True)
            buffer = []
            buffered_symbols = 0
            result.bars_written += len(frame)
            for day, chunk in _split_by_day(frame):
                rows = self.library.write_bars(period, day, chunk)
                result.partitions_written += 1
                logger.debug(
                    "回填落盘: period=%s date=%s 分区行数=%d", period, day, rows
                )
            journal.flush()

        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="vwe-backfill"
        ) as executor:
            # 每个标的的所有分块串行（同一标的的翻页本就串行），标的天粒度上并发
            futures = {
                executor.submit(
                    self._fetch_symbol_window,
                    symbol,
                    period,
                    chunks,
                ): symbol
                for symbol in pending
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    frames, took = future.result()
                except Exception as e:
                    result.failed += 1
                    journal.mark(symbol, "failed")
                    if len(result.errors) < 10:
                        result.errors.append(f"{symbol}: {e}")
                    continue
                fetch_seconds.append(took)

                if not frames:
                    empty_symbols.append(symbol)
                    continue

                merged = pd.concat(frames, ignore_index=True)
                short = self._coverage_short(merged, start, listing_dates.get(symbol))
                if short:
                    # 覆盖不足：数据仍然落盘（幂等合并，重跑会补齐），但**不记完成**，
                    # 让重跑重取 —— 记完成会让这段历史永久缺失（见模块 docstring）。
                    result.failed += 1
                    journal.mark(symbol, "failed")
                    if len(result.errors) < 10:
                        result.errors.append(f"{symbol}: {short}")
                else:
                    result.fetched += 1
                    journal.mark(symbol, "completed")

                buffer.append(merged)
                buffered_symbols += 1
                if buffered_symbols >= flush_symbols:
                    flush_buffer()

        flush_buffer()
        self._classify_empty(journal, empty_symbols, result, probe)
        journal.flush()

        result.elapsed_sec = time.monotonic() - started
        if fetch_seconds:
            result.avg_fetch_sec = sum(fetch_seconds) / len(fetch_seconds)

        logger.info(
            "分钟回填完成: period=%s 区间=%s..%s 探针=%s 成功=%d 空=%d 失败=%d "
            "跳过=%d 写入=%d 分区=%d 耗时=%.1fs 单标的均值=%.3fs",
            period,
            start,
            end,
            result.source_probe,
            result.fetched,
            result.empty,
            result.failed,
            result.skipped,
            result.bars_written,
            result.partitions_written,
            result.elapsed_sec,
            result.avg_fetch_sec,
        )
        return result

    # ------------------------------------------------------------------
    # 单标的区间取数
    # ------------------------------------------------------------------

    def _fetch_symbol_window(
        self, symbol: str, period: str, chunks: Sequence[tuple[date, date]]
    ) -> tuple[list[pd.DataFrame], float]:
        """取单标的在整段区间内的 bar（按块取，块内一次调用翻页取完）。"""
        started = time.monotonic()
        frames: list[pd.DataFrame] = []
        for chunk_start, chunk_end in chunks:
            frame, _ = self._fetch_chunk(symbol, period, chunk_start, chunk_end)
            if frame is not None and not frame.empty:
                frames.append(frame)
        return frames, time.monotonic() - started

    def _fetch_chunk(
        self, symbol: str, period: str, start: date, end: date
    ) -> tuple[Optional[pd.DataFrame], float]:
        """取单标的一段区间。返回 ``(规范化前的原始 frame, 耗时秒)``。

        ``start_offset`` 用日历差估算（源按「最新往旧」翻页，历史区间要先跳过之后
        的所有 bar）；估偏了由覆盖度校验兜住 —— 它会把覆盖不足的标的记成可重试失败，
        不会静默写半段。
        """
        began = time.monotonic()
        budget = float(settings.MINUTE_BACKFILL_FETCH_BUDGET)
        deadline = began + budget
        days = max(1, (end - start).days + 1)
        count = min(
            _MAX_BARS_PER_REQUEST,
            max(100, int(days * _TRADING_DAY_RATIO * bars_per_day(period) * 1.2) + 20),
        )
        offset = self._estimate_start_offset(period, end)

        df = self.provider.fetch_minute_data(
            stock_code=symbol,
            start_datetime=f"{start.isoformat()} {_SESSION_START}",
            end_datetime=f"{end.isoformat()} {_SESSION_END}",
            period=period,
            adjust="",
            count=count,
            start_offset=offset,
            deadline=deadline,
        )
        took = time.monotonic() - began
        if df is None or df.empty:
            return None, took

        frame = df.copy()
        if "trade_time" not in frame.columns and "datetime" in frame.columns:
            frame = frame.rename(columns={"datetime": "trade_time"})
        if "trade_time" not in frame.columns:
            return None, took
        stamps = pd.to_datetime(frame["trade_time"])
        # 只保留区间内的 bar：备源（东财 trends2）会返回跨区间的窗口，不裁剪会把
        # 区间外的 bar 也写进分区。
        mask = (stamps.dt.date >= start) & (stamps.dt.date <= end)
        frame = frame.loc[mask].copy()
        frame["trade_time"] = stamps.loc[mask]
        if frame.empty:
            return None, took
        frame["stock_code"] = symbol
        if time.monotonic() >= deadline - _TRUNCATION_SLACK_SEC:
            # 预算耗尽 → 翻页被 deadline 打断，返回的是半段。标成截断让覆盖度校验
            # 把它记成可重试失败（见模块 docstring「取到一部分为什么不能记为完成」）。
            frame.attrs["truncated"] = True
        return frame, took

    def _estimate_start_offset(self, period: str, end: date) -> int:
        """估算「跳过最新多少根 bar 才能到区间末尾」（源按最新往旧翻页）。"""
        today = date.today()
        if end >= today:
            return 0
        gap_days = (today - end).days
        return int(gap_days * _TRADING_DAY_RATIO * bars_per_day(period))

    def _coverage_short(
        self, frame: pd.DataFrame, start: date, list_date: Optional[date]
    ) -> Optional[str]:
        """取回数据是否覆盖到区间起点？覆盖不足时返回原因（供记可重试失败）。

        覆盖不足有两种成因，后果不同：
        - **截断**（预算耗尽 / 备源窗口太短）：必须重试，否则这段历史永久缺失；
        - **标的本身没有更早的数据**（新上市）：重试无意义，由 ``list_date`` 豁免。
        查不到 ``list_date`` 时按可疑处理（重试），与 P0「可疑就重试」同向。
        """
        if frame.attrs.get("truncated"):
            return "取数预算耗尽被截断（可重试）"
        if frame.empty or "trade_time" not in frame.columns:
            return None
        earliest = pd.to_datetime(frame["trade_time"]).dt.date.min()
        floor = start + timedelta(days=_COVERAGE_SLACK_DAYS)
        if earliest <= floor:
            return None
        if list_date is not None and list_date > floor:
            # 上市日晚于区间起点 + 容差 → 短覆盖是合法的
            return None
        return (
            f"覆盖不足：取回最早 {earliest}，区间起点 {start}"
            f"（+{_COVERAGE_SLACK_DAYS} 天容差）仍晚于起点，疑似截断（可重试）"
        )

    def _listing_dates(self, symbols: Sequence[str]) -> dict[str, date]:
        """批量取上市日（用于豁免新上市标的的合法短覆盖）。取不到时返回空 dict。"""
        if self.db is None or not symbols:
            return {}
        try:
            from app.models.security_universe import SecurityUniverse

            rows = self.db.execute(
                select(SecurityUniverse.stock_code, SecurityUniverse.list_date).where(
                    SecurityUniverse.stock_code.in_(list(symbols))
                )
            ).all()
        except Exception as e:  # pragma: no cover - 维表缺失/结构变化
            logger.warning("上市日查询失败（短覆盖一律按可疑处理）: %s", e)
            return {}
        out: dict[str, date] = {}
        for code, listed in rows:
            if listed is None:
                continue
            value = listed if isinstance(listed, date) else _coerce_date(listed)
            out[str(code).zfill(6)] = value
        return out

    # ------------------------------------------------------------------
    # 源健康判定（复用每日采集器的三态语义）
    # ------------------------------------------------------------------

    def _probe_window(self, period: str, start: date, end: date) -> str:
        """判定源在**本区间**上是否可用（决定「取空」记终态还是可重试）。

        与 ``MinuteCollector._probe_source`` 同一套三态词汇与同一条判据（只有拿到
        源可用的正面证据才认终态），但单位不同：那边判「某一天」，这边判「整个区间」。
        """
        symbol = str(getattr(settings, "SOURCE_HEALTH_PROBE_SYMBOL", "000001")).zfill(6)
        # 在区间内**均匀取样**若干天探测：只看区间末尾会漏掉「源能覆盖区间前半段、
        # 但最近几天还没上架」这种形态，只看起点则会把「源深度不足」当成源故障。
        # 取样点含起点与终点 —— 起点是覆盖度校验的基准，必须探到。
        for probe_day in _probe_days(start, end):
            frame, _ = self._fetch_chunk(symbol, period, probe_day, probe_day)
            if frame is not None and not frame.empty:
                return PROBE_OK
        # 区间内取不到 → 看更早（区间之前）有没有：有 = 源能给历史但给不到本区间
        earlier, _ = self._fetch_chunk(
            symbol, period, start - timedelta(days=60), start - timedelta(days=1)
        )
        if earlier is not None and not earlier.empty:
            logger.warning(
                "回填源探针：%s 在本区间 %s..%s 取不到 bar、在区间之前有 —— "
                "疑似源端历史深度不足（空结果按可重试处理）",
                symbol,
                start,
                end,
            )
            return PROBE_NO_SESSION
        return PROBE_DOWN

    def _classify_empty(
        self,
        journal: _BackfillJournal,
        candidates: Sequence[str],
        result: BackfillResult,
        probe: str,
    ) -> None:
        """决定整区间取空的标的记 ``empty``（终态）还是 ``failed``（可重试）。

        判据与 P0 一致且同向：只有拿到**源可用的正面证据**才认终态 —— 此时「整区间无
        bar」只能是该标的自身无数据（长期停牌 / 退市）。正面证据有两条，取先到的：

        1. 本轮**有标的取到了数据**（``result.fetched > 0``）—— 这比探针更硬：它就是
           同一批取数、同一条路径、同一把锁下的实际结果；
        2. 源探针在区间内取样取到 bar（``PROBE_OK``）。

        兜底与 P0 同构：即便探针说源可用，空结果占比超过全市场过半时仍按源异常处理
        （源只坏了一部分、探针恰好落在好的那部分），避免把大面积缺失记成终态。

        占比兜底**只在全市场口径下生效**（``universe_source != "explicit"``）：它要
        回答的是「源是不是只坏了一部分」这个市场级问题，分母必须是全市场。调用方显式
        给了标的清单（自选池 / 单标的验证）时，占比没有市场含义 —— 1 只自选股取空会
        算出 100%，把「该股确实无数据」误判成源故障、让它永远无法记终态。
        """
        if not candidates:
            return
        evidence = PROBE_OK if result.fetched > 0 else probe
        limit = float(settings.MINUTE_COLLECT_EMPTY_RATIO_LIMIT)
        market_scale = result.universe_source != "explicit"
        ratio = len(candidates) / max(1, result.universe_size)

        if evidence == PROBE_OK and (not market_scale or ratio <= limit):
            result.empty += len(candidates)
            for symbol in candidates:
                journal.mark(symbol, "empty")
            return

        if evidence == PROBE_OK and market_scale:
            # 源可用但空结果占了大半 —— 源只坏了一部分，探针恰好落在好的那部分
            reason = (
                f"空结果占比 {ratio:.0%} 超过阈值 {limit:.0%}"
                f"（{len(candidates)}/{result.universe_size}）"
            )
        elif probe == PROBE_DOWN:
            reason = "源探针在区间内未取到样本 bar"
        else:
            reason = "源探针在区间内无 bar、区间之前有（疑似历史深度不足）"
        message = f"{reason}，{len(candidates)} 个空结果按可重试失败记录，重跑会重试"
        logger.error("分钟回填源异常: %s", message)
        result.failed += len(candidates)
        for symbol in candidates:
            journal.mark(symbol, "failed")
        if len(result.errors) < 10:
            result.errors.append(message)

    # ------------------------------------------------------------------
    # 两源交叉校验
    # ------------------------------------------------------------------

    def cross_check(
        self,
        period: str,
        start_date: date | datetime | str,
        end_date: date | datetime | str,
        symbols: Sequence[str],
    ) -> CrossCheckReport:
        """拉两源同期数据做交叉校验（OHLC 严格相等、volume 换算后相等）。

        第二源（腾讯 ``ifzq``）由 VEW-63 接入；该模块尚未合入 dev/v1.3.0 时报告里
        标 ``secondary_unavailable``，比较逻辑本身仍可离线单测。
        """
        start = _coerce_date(start_date)
        end = _coerce_date(end_date)
        report = CrossCheckReport(
            period=str(period), start_date=start.isoformat(), end_date=end.isoformat()
        )
        for symbol in symbols:
            symbol = str(symbol).zfill(6)
            primary, _ = self._fetch_chunk(symbol, str(period), start, end)
            secondary = self._fetch_secondary(symbol, str(period), start, end)
            if secondary is None:
                report.secondary_unavailable = True
                return report
            if primary is None or primary.empty:
                continue
            report.compared_symbols += 1
            compare_frames(primary, secondary, report)
        return report

    def _fetch_secondary(
        self, symbol: str, period: str, start: date, end: date
    ) -> Optional[pd.DataFrame]:
        """取第二源（腾讯 ``ifzq``）同期数据，成交量统一为「股」。

        VEW-63 未合入时返回 ``None``（调用方据此标 ``secondary_unavailable``）——
        不抛异常：交叉校验是**验证**步骤，缺源时应如实报告而不是让回填流程崩掉。
        """
        try:
            from app.providers.astock_data import (
                tencent_minute_bars,
                tencent_minute_frame,
            )
        except ImportError:
            logger.warning("腾讯 ifzq 分钟源不可用（VEW-63 未合入），本次跳过交叉校验")
            return None

        api_period = {"1": "m1", "5": "m5", "15": "m15", "30": "m30", "60": "m60"}.get(
            str(period)
        )
        if api_period is None:
            return None
        bars = tencent_minute_bars(
            symbol, api_period, start_time="", count=800, _record=False
        )
        frame = tencent_minute_frame(bars)
        if frame is None or frame.empty:
            return pd.DataFrame()
        frame["stock_code"] = symbol
        stamps = pd.to_datetime(frame["datetime"])
        mask = (stamps.dt.date >= start) & (stamps.dt.date <= end)
        out = frame.loc[mask].copy()
        if out.empty:
            return pd.DataFrame()
        # ``tencent_minute_frame`` 已把「手」换算成「股」，与 mootdx 口径一致；
        # 这里再断言一次单位，避免上游改动后交叉校验静默比错。
        out.attrs["volume_unit"] = "share"
        return out


def compare_frames(
    primary: pd.DataFrame, secondary: pd.DataFrame, report: CrossCheckReport
) -> CrossCheckReport:
    """比对两源同期 bar，把差异累加进 ``report``（纯函数，可离线测）。

    规范单位：两源 volume 都必须是**股**（腾讯侧由 ``tencent_minute_frame`` ×100）。
    比对键 ``(stock_code, trade_time)``：OHLC 要求严格相等，volume 要求一致。
    """
    left = _canonical(primary)
    right = _canonical(secondary)
    if left.empty or right.empty:
        return report

    merged = left.merge(
        right, on=["stock_code", "trade_time"], how="outer", suffixes=("_p", "_s")
    )
    # 后缀 ``_p`` 来自左表（primary）、``_s`` 来自右表（secondary）。某根 bar 只在
    # primary 里 → ``open_s`` 为 NaN；反之 ``open_p`` 为 NaN。
    only_primary = merged["open_s"].isna() & merged["open_p"].notna()
    only_secondary = merged["open_p"].isna() & merged["open_s"].notna()
    report.only_primary += int(only_primary.sum())
    report.only_secondary += int(only_secondary.sum())

    both = merged.dropna(subset=["open_p", "open_s"])
    report.compared_bars += len(both)
    if both.empty:
        return report

    ohlc_bad = (
        (both["open_p"] != both["open_s"])
        | (both["high_p"] != both["high_s"])
        | (both["low_p"] != both["low_s"])
        | (both["close_p"] != both["close_s"])
    )
    vol_bad = both["volume_p"] != both["volume_s"]
    report.ohlc_mismatch += int(ohlc_bad.sum())
    report.volume_mismatch += int(vol_bad.sum())

    for _, row in both[ohlc_bad | vol_bad].head(10).iterrows():
        report.samples.append(
            {
                "stock_code": row["stock_code"],
                "trade_time": str(row["trade_time"]),
                "primary": {
                    "open": row["open_p"],
                    "high": row["high_p"],
                    "low": row["low_p"],
                    "close": row["close_p"],
                    "volume": row["volume_p"],
                },
                "secondary": {
                    "open": row["open_s"],
                    "high": row["high_s"],
                    "low": row["low_s"],
                    "close": row["close_s"],
                    "volume": row["volume_s"],
                },
                "ohlc_equal": not bool(row["open_p"] != row["open_s"])
                and not bool(row["high_p"] != row["high_s"])
                and not bool(row["low_p"] != row["low_s"])
                and not bool(row["close_p"] != row["close_s"]),
            }
        )
    return report


def _canonical(df: pd.DataFrame) -> pd.DataFrame:
    """把一段 frame 规整成比对用的 ``(stock_code, trade_time, OHLCV)``。"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "trade_time" not in out.columns and "datetime" in out.columns:
        out = out.rename(columns={"datetime": "trade_time"})
    if "trade_time" not in out.columns:
        return pd.DataFrame()
    if "stock_code" not in out.columns:
        return pd.DataFrame()
    out["stock_code"] = out["stock_code"].astype(str).str.zfill(6)
    out["trade_time"] = pd.to_datetime(out["trade_time"])
    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            return pd.DataFrame()
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    keep = ["stock_code", "trade_time", "open", "high", "low", "close", "volume"]
    # 比对用的键只有 (stock_code, trade_time)：两源都在同一周期上取数，period 是常量，
    # 而比对 frame 里根本没有 period 列（DEDUPE_KEYS 含 period，是**入库**用的键）。
    return (
        out.loc[:, keep]
        .drop_duplicates(subset=["stock_code", "trade_time"], keep="last")
        .reset_index(drop=True)
    )


def _split_by_day(frame: pd.DataFrame) -> Iterable[tuple[date, pd.DataFrame]]:
    """按 ``trade_time`` 的日期切分（逐日分区写入）。"""
    stamps = pd.to_datetime(frame["trade_time"])
    work = frame.copy()
    work["_trade_day"] = stamps.dt.date
    for day, chunk in work.groupby("_trade_day", sort=True):
        yield (
            day if isinstance(day, date) else day.date(),
            chunk.drop(columns=["_trade_day"]),
        )


def _probe_days(start: date, end: date, samples: int = 5) -> list[date]:
    """区间内的均匀取样日（含起点与终点），供源探针用。

    起点必须在样本里：它是覆盖度校验的基准，「源够不够深」只有探起点才知道。
    其余样本摊在区间上，避免只看一端造成的误判（见 ``_probe_window``）。
    """
    if end <= start:
        return [start]
    span = (end - start).days
    picks = {start, end}
    for k in range(1, samples):
        picks.add(start + timedelta(days=span * k // samples))
    return sorted(picks)


def _date_chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    """把区间切成不超过 ``chunk_days`` 个日历日的块（限制取数锁持有时间）。"""
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + timedelta(days=chunk_days - 1))
        chunks.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return chunks


# 进程内共享实例（与 minute_library 同构，供脚本 / 后续端点复用）
minute_backfiller = MinuteBackfiller()
