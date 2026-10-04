"""全市场分钟行情采集器（VEW-64 P0）。

职责：按 ``period`` × ``trade_date`` 采集**全市场**分钟 bar，幂等写入本地分钟库
（``minute_store.MinuteLibrary``），支持断点续采与并发取数。

与既有 ``DataCollector`` 的关系
-------------------------------
``data_collector.DataCollector`` 采集**自选股**并写 PG ``stock_minute_data``（在线
查询路径，近期窗口，行数小）；本模块采集**全市场**并写 Parquet 分钟库（归档与回测
的事实源，跨年 3.47 亿行/年量级）。两者职责不同，不合并：把全市场跨年数据写进 PG
行存正是 P0 存储选型要避免的事。

并发安全（重要）
----------------
本模块用线程池并发取数，依赖 VEW-60 在 ``astock_provider`` 落地的**取数串行化锁**：
mootdx 的 TDX 协议是一条 TCP 连接上一问一答，共享 client 被多线程并发调用时响应会
错位、**静默返回空**（调用方会误判为「该标的无数据」）。因此本模块**不得**绕过该锁
自行开裸并发；并发只用来摊薄建连与解析开销，网络取数仍是串行的（VEW-60 实测：加锁
20 标的 × 250 根 12.0s，且快于每线程独立建连的 23.8s）。

断点续采
--------
每个 ``(period, trade_date)`` 有一份 JSON 日志（``{root}/_state/...``），记录
``completed`` / ``empty`` / ``failed``。重跑时 completed 与 empty 直接跳过（两者都
代表「已确认无需再取」），failed 重试。日志之外还会合并分钟库里已落盘的标的集合，
这样即使日志写失败也不会重复采集已入库的标的。``force=True`` 忽略上述判据全量重采。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import settings
from app.providers import get_data_provider
from app.services.minute_store import MinuteLibrary, minute_library
from app.services.universe_service import (
    _load_static_codes,  # 静态清单兜底（维表为空时的既有降级路径）
    get_universe_as_of,
)

logger = logging.getLogger(__name__)

# 全市场采集覆盖的板块。universe_service 的默认值是 ["main"]，采集侧必须显式给全，
# 否则创业板 / 科创板 / 北交所会整块缺失。
ALL_BOARDS = ["main", "gem", "star", "bse"]

# 单次采集请求的时间窗（A 股连续竞价 09:30–15:00，两侧留余量覆盖集合竞价）。
_SESSION_START = "09:00:00"
_SESSION_END = "16:00:00"


def _coerce_trade_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass
class MinuteCollectResult:
    """一次采集的结果与成本基线（取数耗时分布用于校准容量估算）。"""

    period: str
    trade_date: str
    universe_size: int = 0
    requested: int = 0
    fetched: int = 0
    empty: int = 0
    failed: int = 0
    skipped: int = 0
    # 本次实际合并进库的 bar 数（各次 flush 的输入行数之和）
    bars_written: int = 0
    # 收尾时该分区的总行数（含历史已落盘部分，即当天库内全量）
    partition_rows: int = 0
    elapsed_sec: float = 0.0
    avg_fetch_sec: float = 0.0
    p95_fetch_sec: float = 0.0
    universe_source: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "period": self.period,
            "trade_date": self.trade_date,
            "universe_size": self.universe_size,
            "requested": self.requested,
            "fetched": self.fetched,
            "empty": self.empty,
            "failed": self.failed,
            "skipped": self.skipped,
            "bars_written": self.bars_written,
            "partition_rows": self.partition_rows,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "avg_fetch_sec": round(self.avg_fetch_sec, 4),
            "p95_fetch_sec": round(self.p95_fetch_sec, 4),
            "universe_source": self.universe_source,
            "errors": self.errors[:10],
        }


class _ResumeJournal:
    """``(period, trade_date)`` 的断点日志（原子写，单写者）。"""

    def __init__(self, root: Path, period: str, trade_date: date, enabled: bool = True):
        self.path = (
            root / "_state" / f"period={period}" / f"trade_date={trade_date}.json"
        )
        self.period = period
        self.trade_date = trade_date
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
            logger.warning("断点日志损坏，按空处理: %s", self.path)
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
            {"completed": self.completed, "empty": self.empty, "failed": self.failed}[
                bucket
            ].add(symbol)

    def flush(self) -> None:
        """原子落盘（临时文件 + ``os.replace``），失败不致命：库里的数据才是事实源。"""
        if not self.enabled:
            return
        with self._lock:
            payload = {
                "period": self.period,
                "trade_date": self.trade_date.isoformat(),
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
            logger.warning("断点日志写入失败(不影响已落盘数据): %s", e)

    def done(self) -> set[str]:
        return self.completed | self.empty


class MinuteCollector:
    """全市场分钟行情采集器。"""

    def __init__(
        self,
        db: Optional[Session] = None,
        provider=None,
        library: Optional[MinuteLibrary] = None,
    ):
        self.db = db
        self.provider = provider or get_data_provider()
        self.library = library or minute_library

    # ------------------------------------------------------------------
    # 股票池
    # ------------------------------------------------------------------

    def resolve_universe(
        self,
        trade_date: date,
        boards: Optional[Iterable[str]] = None,
        exclude_st: bool = False,
    ) -> tuple[list[str], str]:
        """解析采集股票池。

        默认取**全部板块、含 ST**：分钟库要存下当日真实可交易的全集，ST / 退市与否
        由回测在选池时按 as_of 过滤，采集侧提前过滤会留下无法补回的空洞。优先用当日
        （或更早最近）的 universe 快照（点状态），无快照时降级到当前维表，维表也为空
        时落到静态清单。

        Returns:
            ``(symbols, source)``，source 为快照 / 维表 / 静态清单的来源标记。
        """
        symbols: list[str] = []
        source = "empty"
        if self.db is not None:
            pool = get_universe_as_of(
                self.db,
                as_of=trade_date,
                boards=list(boards) if boards else ALL_BOARDS,
                exclude_st=exclude_st,
            )
            symbols = list(pool.symbols)
            source = pool.source
            if pool.warning:
                logger.warning("股票池降级: %s", pool.warning)

        if not symbols:
            symbols = _load_static_codes()
            source = "static_list"

        return [str(s).zfill(6) for s in symbols], source

    # ------------------------------------------------------------------
    # 采集
    # ------------------------------------------------------------------

    def collect(
        self,
        period: str,
        trade_date: date | datetime | str,
        symbols: Optional[Sequence[str]] = None,
        workers: Optional[int] = None,
        resume: bool = True,
        force: bool = False,
    ) -> MinuteCollectResult:
        """采集某周期、某交易日的全市场分钟 bar 并写入本地分钟库。

        Args:
            period: K 线周期（分钟），如 ``"1"`` / ``"5"``。
            trade_date: 交易日期。
            symbols: 显式指定标的（默认解析全市场）。
            workers: 并发取数线程数（默认 ``settings.MINUTE_COLLECT_WORKERS``）。
            resume: 是否读取断点日志跳过已确认的标的。
            force: 忽略断点与库内已有数据，全量重采（覆盖写，仍幂等）。
        """
        period = str(period)
        day = _coerce_trade_date(trade_date)
        workers = max(1, int(workers or settings.MINUTE_COLLECT_WORKERS))
        flush_every = max(1, int(settings.MINUTE_COLLECT_FLUSH_EVERY))

        started = time.monotonic()
        result = MinuteCollectResult(period=period, trade_date=day.isoformat())

        if symbols is None:
            universe, source = self.resolve_universe(day)
            result.universe_source = source
        else:
            universe = [str(s).zfill(6) for s in symbols]
            result.universe_source = "explicit"
        result.universe_size = len(universe)

        journal = _ResumeJournal(self.library.root, period, day, enabled=resume)

        pending = list(universe)
        if not force:
            already = journal.done() | self.library.covered_symbols(period, day)
            pending = [s for s in pending if s not in already]
            result.skipped = len(universe) - len(pending)

        result.requested = len(pending)
        logger.info(
            "分钟采集开始: period=%s date=%s 股票池=%d 待采=%d 跳过=%d workers=%d",
            period,
            day,
            result.universe_size,
            result.requested,
            result.skipped,
            workers,
        )

        if not pending:
            result.elapsed_sec = time.monotonic() - started
            journal.flush()
            return result

        buffer: list[pd.DataFrame] = []
        buffer_symbols = 0
        fetch_seconds: list[float] = []

        def flush_buffer() -> None:
            nonlocal buffer, buffer_symbols
            if not buffer:
                return
            frame = pd.concat(buffer, ignore_index=True)
            buffer = []
            buffer_symbols = 0
            result.bars_written += len(frame)
            # write_bars 返回的是**合并后分区总行数**（含历史已落盘部分），每次 flush
            # 都会重写整个分区，所以只有最后一次的返回值才是当天库内全量。
            result.partition_rows = self.library.write_bars(period, day, frame)
            journal.flush()

        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="vwe-minute"
        ) as executor:
            futures = {
                executor.submit(self._fetch_symbol, symbol, period, day): symbol
                for symbol in pending
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    frame, took = future.result()
                    fetch_seconds.append(took)
                except Exception as e:
                    result.failed += 1
                    journal.mark(symbol, "failed")
                    if len(result.errors) < 10:
                        result.errors.append(f"{symbol}: {e}")
                    continue

                if frame is None or frame.empty:
                    result.empty += 1
                    journal.mark(symbol, "empty")
                    continue

                result.fetched += 1
                journal.mark(symbol, "completed")
                buffer.append(frame)
                buffer_symbols += 1
                if buffer_symbols >= flush_every:
                    flush_buffer()

        flush_buffer()
        journal.flush()

        result.elapsed_sec = time.monotonic() - started
        if fetch_seconds:
            result.avg_fetch_sec = sum(fetch_seconds) / len(fetch_seconds)
            ordered = sorted(fetch_seconds)
            idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
            result.p95_fetch_sec = ordered[idx]

        logger.info(
            "分钟采集完成: period=%s date=%s 成功=%d 空=%d 失败=%d 跳过=%d "
            "写入=%d 分区行数=%d 耗时=%.1fs 单标的均值=%.3fs p95=%.3fs",
            period,
            day,
            result.fetched,
            result.empty,
            result.failed,
            result.skipped,
            result.bars_written,
            result.partition_rows,
            result.elapsed_sec,
            result.avg_fetch_sec,
            result.p95_fetch_sec,
        )
        return result

    def collect_periods(
        self,
        periods: Iterable[str],
        trade_date: date | datetime | str,
        **kwargs,
    ) -> list[MinuteCollectResult]:
        """按多个周期依次采集同一天（供调度任务使用）。"""
        return [
            self.collect(period=p, trade_date=trade_date, **kwargs) for p in periods
        ]

    # ------------------------------------------------------------------
    # 单标的取数
    # ------------------------------------------------------------------

    def _fetch_symbol(
        self, symbol: str, period: str, trade_date: date
    ) -> tuple[Optional[pd.DataFrame], float]:
        """取单标的当日分钟 bar，返回 ``(规范化后的 DataFrame, 耗时秒)``。

        只保留落在 ``trade_date`` 当天的 bar：备源（东财 trends2）会返回跨日窗口，
        不裁剪会把别的日期的 bar 标成今天写进分区。
        """
        started = time.monotonic()
        deadline = started + float(settings.MINUTE_COLLECT_FETCH_BUDGET)
        df = self.provider.fetch_minute_data(
            stock_code=symbol,
            start_datetime=f"{trade_date.isoformat()} {_SESSION_START}",
            end_datetime=f"{trade_date.isoformat()} {_SESSION_END}",
            period=period,
            adjust="",
            deadline=deadline,
        )
        took = time.monotonic() - started
        if df is None or df.empty:
            return None, took

        frame = df.copy()
        if "trade_time" not in frame.columns and "datetime" in frame.columns:
            frame = frame.rename(columns={"datetime": "trade_time"})
        if "trade_time" not in frame.columns:
            return None, took

        stamps = pd.to_datetime(frame["trade_time"])
        mask = stamps.dt.date == trade_date
        frame = frame.loc[mask].copy()
        frame["trade_time"] = stamps.loc[mask]
        if frame.empty:
            return None, took
        frame["stock_code"] = symbol
        return frame, took
