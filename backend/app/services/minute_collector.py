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
``completed`` / ``empty`` / ``no_session`` / ``failed``。重跑时只有 completed 与
empty 直接跳过（两者都代表「已确认无需再取」），``no_session`` 与 failed 都会重试。
日志之外还会合并分钟库里已落盘的标的集合，这样即使日志写失败也不会重复采集已入库的
标的。``force=True`` 忽略上述判据全量重采。

「取数返回空」为什么不能直接记为终态
------------------------------------
空有两种成因，后果完全相反：

- **源故障 / 镜像静默返回空**（VEW-60 描述的现象）：必须可重试。若记成终态，
  ``done() = completed | empty`` 会让这些标的在重跑时被永久跳过，整天数据静默缺失。
- **该标的当日确实无数据**（停牌 / 退市 / 非交易日）：重试没有意义。

因此本轮结束前用**源级探针**判定（``_probe_source``，走同一条 provider 路径、含取数
锁）三态：

- ``PROBE_OK``（样本股在目标日取到 bar）→ 源可用，空 = 该标的当日无数据 → 记
  ``empty``（终态），并有占比兜底（见下）；
- ``PROBE_NO_SESSION``（目标日取不到、但回看窗口内更早的交易日取到）→ 记
  ``no_session``，**可重试**；
- ``PROBE_DOWN``（两者都取不到）→ 源故障 → 记 ``failed``，可重试，打 ERROR 日志。

``no_session`` 为什么也必须是可重试的（而不是终态）
---------------------------------------------------
「目标日无 bar、历史有 bar」**不等于非交易日**，它同样是「源只能给历史、给不了今天」的
形态：mootdx 镜像缓存滞后一天（或当日数据未上架）时目标日取空、回退东财又被限流，
而回看走 mootdx 能拿到 → 在一个**真实交易日**上判定 NO_SESSION。此时批量取数遇到的是
同一个源状态，全市场几乎全空；若把这一支做成终态，任何重跑都是 ``requested=0``，
这一天在回测事实源里**永久缺失**，而日志讲的是一个可信的故事（「判定为非交易日」）。

「记终态是为了避免节假日反复重取」这个理由不成立：当前没有自动重跑机制（重试只由
人工触发），调度任务也只针对 ``date.today()``，所以节假日那天的记录永远不会被自动
重跑 —— 记可重试在节假日成本为零。反过来，正常交易日 + 健康源下 NO_SESSION 基本不会
触发（要求样本股当天一根 bar 都没有），也不会污染正常日的 ``failed`` 桶。因此单列一个
``no_session`` 桶：节假日报 ``no_session=5921``（不打 ERROR、不污染 failed），源滞后
那天变成「重跑可补回」而不是「不可恢复」。

探针同时充当**交易日历的替代物**（仓库内没有交易日历，``grep is_trading_day`` 无命中，
而 cron ``0 21 * * 1-5`` 会在节假日照常触发）：它让节假日那轮的日志说的是「非交易日」
而不是假的「源异常」。但正因为它只用于**分类展示**、不再决定终态，误判的代价被限制在
观感上，不再有丢数据的风险。

兜底：探针健康但空结果占**全市场**过半（``MINUTE_COLLECT_EMPTY_RATIO_LIMIT``）时，
说明源只坏了一部分（探针恰好落在好的那部分），同样按可重试失败处理。正常日空结果
只有个位数百分比（停牌），不会误触发；分母用全市场规模而非本轮待采规模，避免续采轮
（待采集合可能只剩少量停牌股）被误判成源故障。
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
from datetime import date, datetime, timedelta
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

# 源探针判定值（见模块 docstring「取数返回空为什么不能直接记为终态」）。
PROBE_OK = "ok"  # 样本股在目标日取到 bar：源可用
PROBE_NO_SESSION = "no_session"  # 目标日无 bar 但更早有：疑似当天无行情（非交易日）
PROBE_DOWN = "down"  # 目标日与回看窗口都无 bar：源故障
# 回看窗口（日历日）。要覆盖含相邻周末的长假：国庆 8 天、春节常见 8–9 天，故取 15 天
# 留余量 —— 落在长假期尾部的那些天回看不到最近交易日就会误判成 PROBE_DOWN，让真正
# 代表源故障的那条 ERROR 变成噪音。代价只在探针已经失败时才付（最多多 8 次探针）。
_PROBE_LOOKBACK_DAYS = 15
# 回看探针的单次取数预算（秒）。比批量取数更紧：源故障时最多多花 15 次探针。
_PROBE_BUDGET_SEC = 5.0


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
    # 确认为「该标的当日无数据」的标的数（终态，重跑跳过）
    empty: int = 0
    # 疑似非交易日的标的数（可重试，重跑会重取；单列一桶以免污染 failed 与误报 ERROR）
    no_session: int = 0
    # 可重试的失败数（取数异常 + 源异常时被判为可疑的空结果），重跑会重试
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
    # 源探针判定值（PROBE_OK / PROBE_NO_SESSION / PROBE_DOWN），观测用
    source_probe: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "period": self.period,
            "trade_date": self.trade_date,
            "universe_size": self.universe_size,
            "requested": self.requested,
            "fetched": self.fetched,
            "empty": self.empty,
            "no_session": self.no_session,
            "failed": self.failed,
            "skipped": self.skipped,
            "bars_written": self.bars_written,
            "partition_rows": self.partition_rows,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "avg_fetch_sec": round(self.avg_fetch_sec, 4),
            "p95_fetch_sec": round(self.p95_fetch_sec, 4),
            "universe_source": self.universe_source,
            "source_probe": self.source_probe,
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
        # 疑似非交易日：**可重试**（不进 done()），单列一桶以免污染 failed 的语义
        self.no_session: set[str] = set()
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
            ("no_session", self.no_session),
            ("failed", self.failed),
        ):
            target.update(str(c).zfill(6) for c in payload.get(key, []))

    def mark(self, symbol: str, bucket: str) -> None:
        with self._lock:
            for target in (self.completed, self.empty, self.no_session, self.failed):
                target.discard(symbol)
            {
                "completed": self.completed,
                "empty": self.empty,
                "no_session": self.no_session,
                "failed": self.failed,
            }[bucket].add(symbol)

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
                "no_session": sorted(self.no_session),
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
        """已确认无需再取的标的。``no_session`` 与 ``failed`` 都**不在**其中（可重试）。"""
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

        # 取数前先探源：空结果能否记为终态取决于源是否可用（见模块 docstring）。
        # 探针走同一条 provider 路径，因此它反映的正是批量取数会遇到的状态。
        probe = self._probe_source(period, day)
        result.source_probe = probe
        if probe == PROBE_DOWN:
            logger.error(
                "分钟采集：源探针在 %s 及之前 %d 天均未取到样本 bar，本轮空结果将按"
                "可重试失败记录（源恢复后重跑即可补齐）",
                day,
                _PROBE_LOOKBACK_DAYS,
            )

        buffer: list[pd.DataFrame] = []
        buffer_symbols = 0
        fetch_seconds: list[float] = []
        # 空结果先攒着，等本轮结束、拿到完整的源健康判定后再决定记 empty 还是 failed
        empty_candidates: list[str] = []

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
                    empty_candidates.append(symbol)
                    continue

                result.fetched += 1
                journal.mark(symbol, "completed")
                buffer.append(frame)
                buffer_symbols += 1
                if buffer_symbols >= flush_every:
                    flush_buffer()

        flush_buffer()
        self._classify_empty(journal, empty_candidates, result, probe)
        journal.flush()

        result.elapsed_sec = time.monotonic() - started
        if fetch_seconds:
            result.avg_fetch_sec = sum(fetch_seconds) / len(fetch_seconds)
            ordered = sorted(fetch_seconds)
            idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
            result.p95_fetch_sec = ordered[idx]

        logger.info(
            "分钟采集完成: period=%s date=%s 探针=%s 成功=%d 空=%d 疑似休市=%d "
            "失败=%d 跳过=%d 写入=%d 分区行数=%d 耗时=%.1fs 单标的均值=%.3fs p95=%.3fs",
            period,
            day,
            result.source_probe,
            result.fetched,
            result.empty,
            result.no_session,
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
    # 空结果分类（源健康判定）
    # ------------------------------------------------------------------

    def _classify_empty(
        self,
        journal: _ResumeJournal,
        candidates: Sequence[str],
        result: MinuteCollectResult,
        probe: str,
    ) -> None:
        """决定本轮「取数返回空」的标的记 ``empty``（终态）还是可重试的桶。

        记错方向的代价是不对称的：把源故障记成终态 = 整天数据静默缺失且重跑也补不回
        （``done() = completed | empty``）；把确实无数据记成可重试 = 多花一轮取数。
        因此判据偏向「可疑就重试」：只有拿到**源可用的正面证据**（``PROBE_OK``）时才
        认终态 ``empty``，其余两种判定都进可重试的桶。
        """
        if not candidates:
            return

        limit = float(settings.MINUTE_COLLECT_EMPTY_RATIO_LIMIT)
        ratio = len(candidates) / max(1, result.universe_size)

        if probe == PROBE_NO_SESSION:
            # 「目标日无 bar、历史有 bar」不等于非交易日 —— 镜像缓存滞后一天 + 备源被
            # 限流就是这个形态（见模块 docstring）。故单列 no_session 桶且**可重试**：
            # 节假日报这里（不打 ERROR、不污染 failed），源滞后那天则能被重跑补回。
            result.no_session += len(candidates)
            for symbol in candidates:
                journal.mark(symbol, "no_session")
            logger.warning(
                "分钟采集：%s 疑似非交易日（样本股在目标日无 bar、更早的交易日有），"
                "%d 个空结果记为 no_session（可重试，非源故障）",
                result.trade_date,
                len(candidates),
            )
            return

        if probe == PROBE_OK and ratio <= limit:
            # 源可用的正面证据 + 空结果占比正常 → 空 = 该标的停牌 / 退市，终态。
            result.empty += len(candidates)
            for symbol in candidates:
                journal.mark(symbol, "empty")
            return

        # 其余一律可重试：源故障，或探针健康但空结果占比异常（源只坏了一部分，
        # 探针恰好落在好的那部分）。
        if probe == PROBE_DOWN:
            reason = (
                f"源探针在 {result.trade_date} 及之前 {_PROBE_LOOKBACK_DAYS} 天"
                "均未取到样本 bar"
            )
        else:
            reason = (
                f"空结果占比 {ratio:.0%} 超过阈值 {limit:.0%}"
                f"（{len(candidates)}/{result.universe_size}）"
            )
        message = f"{reason}，{len(candidates)} 个空结果按可重试失败记录，重跑会重试"
        logger.error("分钟采集源异常: %s", message)
        result.failed += len(candidates)
        for symbol in candidates:
            journal.mark(symbol, "failed")
        if len(result.errors) < 10:
            result.errors.append(message)

    def _probe_source(self, period: str, trade_date: date) -> str:
        """判定数据源与目标交易日的状态（见模块 docstring 的三态说明）。

        这是**交易日历的替代物**：仓库内没有交易日历，cron ``0 21 * * 1-5`` 会在
        节假日照常触发；回看窗口用样本股的真实 bar 判断「源可用但当天无行情」，
        不需要维护假期表。代价是源故障时最多多花 ``_PROBE_LOOKBACK_DAYS`` 次探针。

        ⚠️ ``PROBE_NO_SESSION`` 只是「疑似非交易日」，不是证据：镜像缓存滞后一天 +
        备源被限流同样会命中它。所以调用方必须把这一支当**可重试**处理，不能记终态。
        """
        symbol = str(getattr(settings, "SOURCE_HEALTH_PROBE_SYMBOL", "000001")).zfill(6)
        if self._probe_day(symbol, period, trade_date):
            return PROBE_OK
        for back in range(1, _PROBE_LOOKBACK_DAYS + 1):
            earlier = trade_date - timedelta(days=back)
            if self._probe_day(symbol, period, earlier):
                logger.warning(
                    "源探针：%s 在 %s 无 bar、在 %s 有 bar —— 疑似 %s 非交易日"
                    "（不排除镜像缓存滞后，空结果按可重试处理）",
                    symbol,
                    trade_date,
                    earlier,
                    trade_date,
                )
                return PROBE_NO_SESSION
        return PROBE_DOWN

    def _probe_day(self, symbol: str, period: str, day: date) -> bool:
        """样本股在 ``day`` 是否取到 bar（任何异常一律按「未取到」）。"""
        try:
            frame, _ = self._fetch_symbol(symbol, period, day, budget=_PROBE_BUDGET_SEC)
        except Exception as e:
            logger.warning("源探针取数异常(%s %s): %s", symbol, day, e)
            return False
        return frame is not None and not frame.empty

    # ------------------------------------------------------------------
    # 单标的取数
    # ------------------------------------------------------------------

    def _fetch_symbol(
        self,
        symbol: str,
        period: str,
        trade_date: date,
        budget: Optional[float] = None,
    ) -> tuple[Optional[pd.DataFrame], float]:
        """取单标的当日分钟 bar，返回 ``(规范化后的 DataFrame, 耗时秒)``。

        只保留落在 ``trade_date`` 当天的 bar：备源（东财 trends2）会返回跨日窗口，
        不裁剪会把别的日期的 bar 标成今天写进分区。

        ``budget`` 覆盖单标的取数墙钟预算（秒）；源探针用更紧的预算，避免源故障时
        探针自己把启动阶段拖长。
        """
        started = time.monotonic()
        seconds = float(
            settings.MINUTE_COLLECT_FETCH_BUDGET if budget is None else budget
        )
        deadline = started + seconds
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
