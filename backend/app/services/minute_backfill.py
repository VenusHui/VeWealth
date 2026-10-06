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

两者都不会抛异常。因此取数后必须做**覆盖度校验**，且必须**逐块 + 整体**两级都做：

- **逐块**（``_chunk_problem``）：本块被预算截断，或本块取到了 bar 但最早一根明显晚于
  块起点（块首缺一截）。截断标志由 ``_fetch_chunk`` **作为返回值带出**，不走
  ``DataFrame.attrs`` —— pandas 只在所有输入的 attrs 完全相同时才在 ``pd.concat`` 时
  保留 attrs，而窗口跨多块是默认情形，走 attrs 必丢标志（VEW-65 评审①）。
- **整体**（``_coverage_short``）：合并后最早日期够不着区间起点（源端深度不足）。

只做整体校验会漏掉**中间块的洞**：合并后的最早日期由第 1 块决定，中间块开头缺一截
既不改变它、也不触发任何整体判据。两级都判不过就记 ``failed``（可重试）而不是
``completed``。判据偏向「可疑就重试」与 P0 一致：记错的代价不对称 —— 多跑一轮取数
vs 回测事实源里永久缺一段历史。

新上市标的的合法短覆盖由 ``security_universe.list_date`` 豁免（查不到时按可疑处理）。

收敛：为什么需要重试上限
------------------------
「可疑就重试」必须配一个上限，否则**永久性**的覆盖不足会让标的永远停在 ``failed``：
典型是**块首停牌** —— 标的在块起点后停牌 19 个交易日才复牌，本块照样有 bar（复牌后的），
所以不是「整块空」，而 ``list_date`` 豁免不了老标的。这种块每轮都判不过，且**重试多少次
都不会变**，结果是每轮重取整段区间、``skipped`` 永远填不满，运维上还与真实源故障长得
一模一样。

判据本身分不开「可重试的截断」与「永久性的停牌缺口」，只能靠次数区分：**按标的**计数，
前 ``MINUTE_BACKFILL_MAX_ATTEMPTS - 1`` 轮记 ``failed``（可重试），第 N 轮转入
``gapped`` 终态（有缺口完成）并保留最后一次的原因。缺口数据仍已落盘，只是不再重取。

计数**只对「该标的自身覆盖不全」生效**，也就是**只对逐块「块内覆盖不足」计次**。
其余三条失败路径都不计次，一律保持可重试：

- 源级故障：整区间取空（``_classify_empty``）、取数抛异常；
- **预算截断**（``truncated``）：纯墙钟判据，与数据完整性无关 —— 慢但在线的镜像每块
  都完整返回也会命中；
- **整体覆盖不足**（``_coverage_short``）：源端深度不足。

后两条与源级故障一样是**源侧条件**，不是该标的的事：计次等于「源一慢/一浅，全市场
就收敛到有缺口完成」，而源恢复后这些标的历史再也取不回来 —— 正是本模块要防的事。
（把计次条件写成「有没有抛异常」是错的，这是 VEW-65 三轮评审的核心。）

即便如此，单标的判据仍分不开「这个标的停牌」与「源整体变浅」（东财备源降级只给最近
几天时，每个标的的最新一块都会块首缺失），所以再加一道**市场级占比兜底**：本轮计次
标的占本轮尝试数过半时视作源侧事件、整轮不计次（与 ``_classify_empty`` 同一机制、
同一门槛，且同样只在全市场口径下生效）。计次决策因此在**轮末**统一做
（``_apply_gap_attempts``），不在取数循环里。

要重试已记 ``gapped`` 的标的，显式传 ``run(retry_gaps=True)``；否则只能删日志文件。

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
第二源按区间翻页取数（``start_time`` 游标），并在报告里给出 ``coverage``；
``ok`` 要求**确实比对上 bar 且覆盖率达标**，否则「两边交集为空」会被误报成通过。

腾讯源由 VEW-63 接入，已随 ``dev/v1.3.0`` 合入。``_fetch_secondary`` 仍保留
「模块不可用即降级」的守卫：import 失败时返回 ``None`` 并在报告里标注
``secondary_unavailable`` —— 交叉校验是**验证**步骤，缺源时应如实报告，不能让回填
流程崩掉。比较逻辑可离线测（喂两段 frame），不依赖活的外部源。

安全闸门（与 P0 同口径：代码可以进，写入不能跑）
------------------------------------------------
``run()`` 需要**同时**满足 ``settings.MINUTE_BACKFILL_ENABLED is True`` 与
``confirm=True``，并通过盘余量硬校验（估算体积含一年增量，放不下即拒绝，除非显式
``force=True``）才真正取数落盘。回填是一次性大范围跑批，全市场双周期实测约 4.5 GB
（1min 4 个月 ≈ 1.7 GB + 5min 2 年 ≈ 2.8 GB），首年增量另需约 6.9 GB，见
``docs/plans/2026-10-05-minute-backfill-p1-capacity.md``。容量拍板前不得执行。
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
from typing import Iterable, NamedTuple, Optional, Sequence

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

# 一年的交易日数（用于把「一次性回填」与「至少一年的增量增长」放在同一口径下估算）。
# 仓库没有交易日历，与 ``trading_days_between`` 同源：按日历日 × 5/7 折算。
_TRADING_DAYS_PER_YEAR = 244

# 交叉校验第二源（腾讯 ifzq）的单页根数上限与最多翻页数。单页 800 根是源端硬上限
# （VEW-63），翻页上限用来给「窗口比源端深度还长」的情况封顶：拿不满就少比，由
# ``CrossCheckReport.coverage`` 判成不通过，而不是无限翻页。
_SECONDARY_PAGE_BARS = 800
_SECONDARY_MAX_PAGES = 40

# 交叉校验判定通过所需的最低覆盖率（被比对上的 bar / 两源 bar 总数）。第二源只覆盖
# 窗口一小截时，「比过的部分一致」不能代表整段一致 —— 覆盖率不够就判不通过。
_CROSS_CHECK_MIN_COVERAGE = 0.8


class BackfillRefused(RuntimeError):
    """未满足安全闸门（开关未开 / 未显式 confirm）时拒绝执行回填。

    刻意抛异常而不是返回一个「refused=True」的结果对象：回填是写生产磁盘的大动作，
    调用方漏判返回值就等于默认执行，抛异常让漏判变成显式失败。
    """


class ChunkProblem(NamedTuple):
    """单块取数的问题，以及它是否**该由该标的自己负责**。

    ``countable`` 决定这条问题是否累积该标的的重试计数（计数到上限转 ``gapped``
    终态）。判据是「成因在标的还是源」：

    - **块内覆盖不足**（``countable=True``）：本块取到了 bar 但块首缺一截，且没有截断
      标志。成因可能是该标的停牌（永久），也可能是源静默变浅 —— 单看一个标的分不开，
      所以先按「该标的的事」计次，靠重试次数收敛；市场级占比兜底见
      ``MinuteBackfiller._apply_gap_attempts``。
    - **预算截断**（``countable=False``）：源侧条件（墙钟预算），与标的身无关，
      源恢复后必须还能重取，绝不能吃终态上限。

    单独一个 ``bool`` 不够表达这件事，用字符串匹配又太脆 —— 这个区分已经被评审打回
    两次（VEW-65 二轮/三轮），所以放进类型里。
    """

    reason: str
    countable: bool


def _chunk_problem(
    frame: Optional[pd.DataFrame],
    chunk_start: date,
    chunk_end: date,
    truncated: bool,
    list_date: Optional[date] = None,
) -> Optional[ChunkProblem]:
    """单块取数是否有问题？有问题返回 :class:`ChunkProblem`，否则 ``None``。

    **逐块判定**，不看合并后的整体：合并后的「最早日期」由第 1 块决定，中间块开头缺
    一截既不改变它、也不会触发任何整体判据，是个完全无判据的洞（VEW-65 评审①）。

    两条判据：

    1. **预算截断**（``truncated``）：源按「最新往旧」翻页，预算耗尽时丢的是本块**最旧**
       的那一段。标志由 ``_fetch_chunk`` 直接返回，不经过 ``DataFrame.attrs``。
       这是**源侧**条件（``countable=False``）：纯墙钟判据，与数据完整性无关 ——
       慢但在线的镜像每块都完整返回也会命中，不能因此把标的判成终态。
    2. **块内覆盖不足**：本块取到了 bar，但最早一根明显晚于块起点。这正是截断在数据上
       的签名（也覆盖 ``_estimate_start_offset`` 估偏导致的块首缺失），是唯一
       ``countable=True`` 的判据。

    第 2 条只对「取到了 bar」的块生效：整块取空可能是**合法**的（区间内长期停牌），
    无法与源故障区分，交给整体覆盖度校验与 ``_classify_empty`` 判定，不在这里误杀。

    ``list_date`` 晚于块起点时同样豁免第 2 条：标的在块起点还没上市，块首没有 bar 是
    事实而不是缺失（与 ``_coverage_short`` 同一豁免，否则新上市标的永远重试）。截断
    不豁免 —— 那是源侧故障，与标的何时上市无关。

    注意**不要**把第 2 条提到第 1 条前面：被截断的块确实可能缺最旧的一段，即便取回的
    部分看起来够得着块起点。截断要继续产生「问题」（记可重试失败），只是不吃终态上限。
    """
    if truncated:
        return ChunkProblem(
            f"{chunk_start}..{chunk_end} 取数预算耗尽被截断，本块最旧的一段可能缺失"
            "（可重试）",
            False,
        )
    if frame is None or frame.empty or "trade_time" not in frame.columns:
        return None
    if list_date is not None and list_date > chunk_start:
        return None
    earliest = pd.to_datetime(frame["trade_time"]).dt.date.min()
    floor = chunk_start + timedelta(days=_COVERAGE_SLACK_DAYS)
    if earliest <= floor:
        return None
    return ChunkProblem(
        f"{chunk_start}..{chunk_end} 块内覆盖不足：取回最早 {earliest}，"
        f"晚于块起点 {chunk_start}（+{_COVERAGE_SLACK_DAYS} 天容差）→ 块首疑似缺失"
        "（可重试）",
        True,
    )


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
    # 同一范围的**每年增量**（同一批标的 × 244 交易日）。闸门口径是「一次性回填 +
    # 至少一年增量」，只看 est_bytes 会把门槛低估成一半以下（VEW-65 评审⑥）。
    annual_bytes: int = 0
    # 已有分区里该周期已落盘的天数（增量采集合入后回填会少写这些天）
    existing_days: int = 0
    bytes_per_row: float = 0.0
    # 估算取数墙钟（秒）：按每页 0.3–0.6s（VEW-60 实测）折算的区间上下界
    est_fetch_sec_low: float = 0.0
    est_fetch_sec_high: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def required_bytes(self) -> int:
        """闸门要求的空间 = （一次性回填 + 一年增量）× 20% 余量。"""
        return int((self.est_bytes + self.annual_bytes) * 1.2)

    @property
    def fits(self) -> Optional[bool]:
        """一次性回填 + 一年增量能否被当前可用空间容纳（留 20% 余量）。"""
        if self.free_bytes is None:
            return None
        return self.required_bytes <= self.free_bytes

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
            "annual_bytes": self.annual_bytes,
            "annual_mb": round(self.annual_bytes / 1e6, 2),
            "required_bytes": self.required_bytes,
            "required_mb": round(self.required_bytes / 1e6, 2),
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
    # 达到重试上限、带缺口转入终态的标的数（与 failed 互斥：failed 会重试，gapped 不会）
    gapped: int = 0
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
            "gapped": self.gapped,
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
    # 第二源在窗口内一根 bar 都没有的标的数（与 secondary_unavailable 不同：源在，
    # 但这个窗口没数据 —— 如窗口早于其历史深度）。
    secondary_empty: int = 0
    samples: list[dict] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """主源 bar 里被实际比对上的比例（分母含单边 bar）。"""
        total = self.compared_bars + self.only_primary + self.only_secondary
        if total <= 0:
            return 0.0
        return self.compared_bars / total

    @property
    def ok(self) -> bool:
        """校验是否**真的**通过。

        三条件缺一不可，否则「没比成」会被当成「比过且一致」：

        1. 第二源可用（``secondary_unavailable`` 为假）；
        2. **确实比对上了 bar**（``compared_bars > 0``）—— 否则两边交集为空时
           ``ohlc_mismatch``/``volume_mismatch`` 天然是 0，会假通过；
        3. 覆盖率不低于 ``_CROSS_CHECK_MIN_COVERAGE`` —— 第二源只覆盖窗口的一小截时，
           「比过的那截一致」不能代表整段区间一致，必须显式判不通过而不是给个绿灯。

        覆盖率这一条的**实际约束是第二源的深度上限，不是单页条数**：早期版本的
        理由写的是「单页 800 根、1min 约 3.3 个交易日」，那是分页之前的事；现在
        ``_fetch_secondary`` 按 ``start_time`` 游标翻页取到窗口起点，条数不再是瓶颈。
        真正的瓶颈是 VEW-61 实测的源端历史深度（``SOURCE_HISTORY_DAYS``）：腾讯 1min
        只回溯约 18 个交易日，所以 **1min 的校验窗口必须 ≤ 约 18 个交易日**才可能
        ``ok=True``；5/15min 约 6 个月、30/60min 约 12 个月，长窗口没有这个问题。
        窗口超出源端深度时正确结果是「不通过」，不是「通过」。
        """
        return (
            not self.secondary_unavailable
            and self.compared_bars > 0
            and self.ohlc_mismatch == 0
            and self.volume_mismatch == 0
            and self.coverage >= _CROSS_CHECK_MIN_COVERAGE
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
            "secondary_empty": self.secondary_empty,
            "coverage": round(self.coverage, 4),
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
        # 每标的的**覆盖不足**计数，以及达到上限后的终态桶（原因一并保留）。
        # 存在的理由见模块 docstring「收敛：为什么需要重试上限」：块首停牌这类永久性
        # 缺口重试多少次都不会变，没有上限就会永远停在 failed、每轮重取整段区间。
        # 计数**只**由覆盖不足累积；源级故障不碰它（源恢复后必须还能重取）。
        self.attempts: dict[str, int] = {}
        self.gapped: dict[str, str] = {}
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
        for key, raw in (payload.get("attempts") or {}).items():
            try:
                self.attempts[str(key).zfill(6)] = int(raw)
            except (TypeError, ValueError):
                continue
        for key, reason in (payload.get("gapped") or {}).items():
            self.gapped[str(key).zfill(6)] = str(reason)

    def mark(self, symbol: str, bucket: str) -> None:
        with self._lock:
            for target in (self.completed, self.empty, self.failed):
                target.discard(symbol)
            self.gapped.pop(symbol, None)
            {
                "completed": self.completed,
                "empty": self.empty,
                "failed": self.failed,
            }[
                bucket
            ].add(symbol)
            if bucket in ("completed", "empty"):
                # 取全了：清掉覆盖不足计数，让计数保持「连续」语义 —— 中间成功过一次
                # 就不该算进上限。
                self.attempts.pop(symbol, None)

    def record_gap_failure(self, symbol: str, reason: str, limit: int) -> bool:
        """记一次**覆盖不足**失败，返回 True 表示本轮到上限、转入 ``gapped`` 终态。

        只有「该标的自身取数覆盖不全」走这里（块被截断 / 块首缺失 / 区间起点够不着）。
        源级故障（整区间取空、取数抛异常）**不走**这里 —— 那是源的问题，源恢复后必须
        还能重取；若也计次，连续几轮源故障会把全市场标的一次性推进终态、永久丢历史。
        """
        with self._lock:
            count = self.attempts.get(symbol, 0) + 1
            self.attempts[symbol] = count
            if count < max(1, int(limit)):
                return False
            for target in (self.completed, self.empty, self.failed):
                target.discard(symbol)
            self.gapped[symbol] = reason
            # 计数只服务「还在重试中」的标的：转终态后原因已记在 gapped 里，计数是死数据。
            self.attempts.pop(symbol, None)
            return True

    def clear_gaps(self, symbols: Iterable[str]) -> int:
        """把指定标的从 ``gapped`` 终态放回待采（``retry_gaps=True`` 时用）。"""
        with self._lock:
            cleared = 0
            for symbol in symbols:
                if self.gapped.pop(symbol, None) is not None:
                    self.attempts.pop(symbol, None)
                    cleared += 1
            return cleared

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
                "attempts": {k: v for k, v in sorted(self.attempts.items()) if v},
                "gapped": {k: self.gapped[k] for k in sorted(self.gapped)},
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
        """已确认无需再取的标的。

        ``failed`` **不在**其中（可重试）；``gapped``（有缺口完成）在 —— 它正是为了让
        永久性缺口不再被无限重试才存在的。要重取走 ``run(retry_gaps=True)``。
        """
        return self.completed | self.empty | set(self.gapped)


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
            annual_bytes=estimate_bytes(period, len(pool), _TRADING_DAYS_PER_YEAR),
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
        plan.notes.append(
            f"闸门口径 = 一次性回填 {plan.est_bytes / 1e9:.2f} GB + 一年增量 "
            f"{plan.annual_bytes / 1e9:.2f} GB，× 1.2 余量 = "
            f"{plan.required_bytes / 1e9:.2f} GB；只看一次性回填会低估门槛（VEW-65 评审⑥）"
        )
        if plan.fits is False:
            plan.notes.append(
                "估算体积（一次性回填 + 一年增量，含 20% 余量）超过当前可用空间 —— "
                "不得执行，先扩盘或缩小范围（自选池 / 单周期 / 更短区间）"
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
        force: bool = False,
        retry_gaps: bool = False,
    ) -> BackfillResult:
        """执行回填。

        安全闸门（三层，全部在取数与落盘**之前**）：

        1. ``settings.MINUTE_BACKFILL_ENABLED`` 为 True —— 挡「误跑」；
        2. ``confirm=True`` —— 挡「无人确认就跑」；
        3. 盘余量硬校验：本次范围的估算体积（一次性 + 一年增量，含余量）必须放得下，
           否则抛 :class:`BackfillRefused` —— 挡「明知放不下还跑」。

        第 3 条是 ``plan().fits`` 的**执行侧**对应物：``plan()`` 只把 ``fits=False``
        写进 notes，``run()`` 不看就等于没闸门（VEW-65 评审⑤）。issue 的立项目标正是
        「写满的是系统盘，postgres + backend + frontend 一起挂」，所以这里必须硬拦。
        确实要在余量不足时执行（如已确认要分批小范围跑）走 ``force=True`` 显式放行。

        收敛：覆盖不足按标的计次，``MINUTE_BACKFILL_MAX_ATTEMPTS`` 轮后转 ``gapped``
        终态（有缺口完成，不再重取）。想重取这些缺口传 ``retry_gaps=True``。语义与
        为什么源级故障不计次，见模块 docstring。
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
        max_attempts = max(1, int(settings.MINUTE_BACKFILL_MAX_ATTEMPTS))

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
        if retry_gaps:
            # 显式要求重取「有缺口完成」的标的：清掉终态与计数让它们回到待采。
            # 没有这条路径，gapped 就只能靠删日志文件才能重试 —— 那是个运维陷阱。
            cleared = journal.clear_gaps(universe)
            if cleared:
                logger.info("分钟回填: %d 个有缺口标的按 retry_gaps 放回待采", cleared)
        pending = list(universe)
        if resume:
            already = journal.done()
            pending = [s for s in pending if s not in already]
            result.skipped = len(universe) - len(pending)
        result.requested = len(pending)

        # 盘余量硬校验（第三层闸门）：按**本轮待采**的标的数估算，不是全量 ——
        # 续跑批次小的时候不该被全量估算拦住。估算含一年增量（与 plan() 同口径）。
        self._assert_disk_headroom(
            period, len(pending), start, end, force=force, requested=result.requested
        )

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
        # 本轮的覆盖不足失败（标的, 原因, 是否该由该标的自己负责）。轮末统一计次 ——
        # 市场级占比兜底需要本轮总数，单标的判据分不开「这个标的停牌」与「源整体变浅」。
        gap_candidates: list[tuple[str, str, bool]] = []

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
                    listing_dates.get(symbol),
                ): symbol
                for symbol in pending
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    frames, took, problems = future.result()
                except Exception as e:
                    result.failed += 1
                    journal.mark(symbol, "failed")
                    if len(result.errors) < 10:
                        result.errors.append(f"{symbol}: {e}")
                    continue
                fetch_seconds.append(took)

                if not frames and not problems:
                    empty_symbols.append(symbol)
                    continue

                # 覆盖问题**逐块**收集（截断 / 块首缺失），再叠加整体覆盖度校验
                # （区间起点够不着 = 源端深度不足）。两者都不写 completed：
                # 记完成会让这段历史永久缺失（见模块 docstring）。
                # 归一成 list 再 join：``problems`` 的元素是 ChunkProblem，
                # ``_coverage_short`` 返回 str，直接 join 会把原因按**单字**拆开
                # （评审前就在的错误消息格式问题，正好落在这条 errors 上）。
                short: list[str] = [p.reason for p in problems]
                # 只有「块内覆盖不足」算该标的自身的事；截断与整体覆盖不足都是源侧条件，
                # 只记可重试失败、不吃终态上限（否则慢镜像/源变浅会把全市场推进终态）。
                countable = any(p.countable for p in problems)
                if frames:
                    merged = pd.concat(frames, ignore_index=True)
                    coverage_reason = self._coverage_short(
                        merged, start, listing_dates.get(symbol)
                    )
                    if coverage_reason:
                        short.append(coverage_reason)
                else:
                    merged = None

                if short:
                    # 数据仍然落盘（幂等合并，重跑会补齐）。先一律记可重试 failed ——
                    # 这是安全默认；够格计次的标的在轮末由 _apply_gap_attempts 转终态。
                    result.failed += 1
                    journal.mark(symbol, "failed")
                    if len(result.errors) < 10:
                        result.errors.append(f"{symbol}: {'; '.join(short)}")
                    gap_candidates.append((symbol, "; ".join(short), countable))
                    if merged is None:
                        continue
                else:
                    result.fetched += 1
                    journal.mark(symbol, "completed")

                buffer.append(merged)
                buffered_symbols += 1
                if buffered_symbols >= flush_symbols:
                    flush_buffer()

        flush_buffer()
        self._apply_gap_attempts(journal, gap_candidates, result, max_attempts)
        self._classify_empty(journal, empty_symbols, result, probe)
        journal.flush()

        result.elapsed_sec = time.monotonic() - started
        if fetch_seconds:
            result.avg_fetch_sec = sum(fetch_seconds) / len(fetch_seconds)

        logger.info(
            "分钟回填完成: period=%s 区间=%s..%s 探针=%s 成功=%d 空=%d 失败=%d "
            "有缺口=%d 跳过=%d 写入=%d 分区=%d 耗时=%.1fs 单标的均值=%.3fs",
            period,
            start,
            end,
            result.source_probe,
            result.fetched,
            result.empty,
            result.failed,
            result.gapped,
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

    def _assert_disk_headroom(
        self,
        period: str,
        symbols: int,
        start: date,
        end: date,
        force: bool,
        requested: int,
    ) -> None:
        """盘余量硬校验：放不下就抛 :class:`BackfillRefused`（``force=True`` 放行）。

        估的是**一次性回填 + 一年增量**（与 ``plan().fits`` 同口径），不是只看这次写入：
        issue 的闸门就是这两项之和，只看一次性会把门槛低估成一半以下。

        量不出盘余量（``_free_bytes`` 返回 ``None``）时**不拦**：拿不到事实就不该假装
        有事实，此时由前两层闸门与责任人兜底。这一取舍是显式的，不是遗漏。
        """
        free = _free_bytes(Path(self.library.root))
        if free is None:
            logger.warning("盘余量量不出，跳过容量硬校验（由开关与 confirm 兜底）")
            return
        days = trading_days_between(start, end)
        est = estimate_bytes(period, symbols, days)
        annual = estimate_bytes(period, symbols, _TRADING_DAYS_PER_YEAR)
        required = int((est + annual) * 1.2)
        if required <= free:
            return
        message = (
            f"盘余量不足：本次范围（待采 {requested} 只，{start}..{end}）估算 "
            f"一次性 {est / 1e9:.2f} GB + 一年增量 {annual / 1e9:.2f} GB = "
            f"{required / 1e9:.2f} GB（含 20% 余量），可用仅 {free / 1e9:.2f} GB。"
            "写满的是系统盘，postgres + backend + frontend 会一起挂。"
            "请先扩盘或缩小范围（自选池 / 单周期 / 更短区间）；"
            "确需在余量不足时执行，显式传 force=True"
        )
        if not force:
            raise BackfillRefused(message)
        logger.warning("盘余量不足但显式 force=True，继续执行。%s", message)

    def _fetch_symbol_window(
        self,
        symbol: str,
        period: str,
        chunks: Sequence[tuple[date, date]],
        list_date: Optional[date] = None,
    ) -> tuple[list[pd.DataFrame], float, list[ChunkProblem]]:
        """取单标的在整段区间内的 bar（按块取，块内一次调用翻页取完）。

        返回 ``(frames, 耗时秒, 覆盖问题列表)``。**覆盖问题必须逐块判定、随返回值
        带出**，不能靠 ``DataFrame.attrs`` 穿过 ``pd.concat`` —— pandas 只在所有输入
        的 attrs 完全相同时才保留，一旦窗口跨多块（默认参数下必然如此）标志就被丢掉，
        被截断的块会被静默记成 completed、历史永久缺失（VEW-65 评审①②）。

        问题带 ``countable`` 标志（见 :class:`ChunkProblem`）：只有「块内覆盖不足」算
        该标的自身的事，可累积重试计数；「预算截断」是源侧条件，只记可重试失败。
        """
        started = time.monotonic()
        frames: list[pd.DataFrame] = []
        problems: list[ChunkProblem] = []
        for chunk_start, chunk_end in chunks:
            frame, _, truncated = self._fetch_chunk(
                symbol, period, chunk_start, chunk_end
            )
            problem = _chunk_problem(
                frame, chunk_start, chunk_end, truncated, list_date
            )
            if problem:
                problems.append(problem)
            if frame is not None and not frame.empty:
                frames.append(frame)
        return frames, time.monotonic() - started, problems

    def _fetch_chunk(
        self, symbol: str, period: str, start: date, end: date
    ) -> tuple[Optional[pd.DataFrame], float, bool]:
        """取单标的一段区间。返回 ``(规范化前的原始 frame, 耗时秒, 是否被预算截断)``。

        ``start_offset`` 用日历差估算（源按「最新往旧」翻页，历史区间要先跳过之后
        的所有 bar）；估偏了由覆盖度校验兜住 —— 它会把覆盖不足的标的记成可重试失败，
        不会静默写半段。

        截断标志**作为返回值带出**而不是写 ``frame.attrs``：调用方会把多块 concat
        起来，而 pandas 只在所有输入的 attrs 相同时才保留 attrs，走 attrs 必丢
        （VEW-65 评审①）。
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
        # 预算耗尽 → 翻页被 deadline 打断，返回的是半段。无论取没取到 bar 都要带出这个
        # 标志：取空 + 预算耗尽同样可疑（见 _chunk_problem）。
        truncated = time.monotonic() >= deadline - _TRUNCATION_SLACK_SEC
        if df is None or df.empty:
            return None, took, truncated

        frame = df.copy()
        if "trade_time" not in frame.columns and "datetime" in frame.columns:
            frame = frame.rename(columns={"datetime": "trade_time"})
        if "trade_time" not in frame.columns:
            return None, took, truncated
        stamps = pd.to_datetime(frame["trade_time"])
        # 只保留区间内的 bar：备源（东财 trends2）会返回跨区间的窗口，不裁剪会把
        # 区间外的 bar 也写进分区。
        mask = (stamps.dt.date >= start) & (stamps.dt.date <= end)
        frame = frame.loc[mask].copy()
        frame["trade_time"] = stamps.loc[mask]
        if frame.empty:
            return None, took, truncated
        frame["stock_code"] = symbol
        return frame, took, truncated

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
        """整体覆盖度：取回的数据够得着区间起点吗？不够则返回原因（供记可重试失败）。

        只管**区间起点**这一件事：区间起点够不着只有两种成因，后果不同 ——

        - **源端深度不足 / 预算截断**：必须重试（截断由 ``_chunk_problem`` 逐块判定，
          这里兜住「整段都没够到起点」的情况）；
        - **标的本身没有更早的数据**（新上市）：重试无意义，由 ``list_date`` 豁免。

        查不到 ``list_date`` 时按可疑处理（重试），与 P0「可疑就重试」同向。

        **中间块的洞不在这里判** —— 合并后的最早日期由第 1 块决定，中间块开头缺一截
        既不改变它也不触发本判据。那类洞由 ``_chunk_problem`` 逐块负责。
        """
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

        取数异常按 ``PROBE_DOWN`` 降级而不是向上抛：探针在**第一个标的之前**跑，抛出去
        会让 12–30 小时的回填整体失败；降级后只是把「取空」判成可重试，方向安全
        （VEW-65 三轮评审的非阻塞观察）。
        """
        symbol = str(getattr(settings, "SOURCE_HEALTH_PROBE_SYMBOL", "000001")).zfill(6)
        # 在区间内**均匀取样**若干天探测：只看区间末尾会漏掉「源能覆盖区间前半段、
        # 但最近几天还没上架」这种形态，只看起点则会把「源深度不足」当成源故障。
        # 取样点含起点与终点 —— 起点是覆盖度校验的基准，必须探到。
        try:
            for probe_day in _probe_days(start, end):
                frame, _, _ = self._fetch_chunk(symbol, period, probe_day, probe_day)
                if frame is not None and not frame.empty:
                    return PROBE_OK
            # 区间内取不到 → 看更早（区间之前）有没有：有 = 源能给历史但给不到本区间
            earlier, _, _ = self._fetch_chunk(
                symbol, period, start - timedelta(days=60), start - timedelta(days=1)
            )
        except Exception as e:
            logger.error(
                "回填源探针取数异常（按源不可用处理，空结果保持可重试）: %s", e
            )
            return PROBE_DOWN
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

    def _apply_gap_attempts(
        self,
        journal: _BackfillJournal,
        candidates: Sequence[tuple[str, str, bool]],
        result: BackfillResult,
        max_attempts: int,
    ) -> None:
        """把本轮「该标的自身覆盖不全」的失败按标的计次，到上限的转 ``gapped`` 终态。

        调用点在轮末，不在取数循环里 —— 市场级占比兜底要等本轮总数出来才知道。

        **只对 ``countable=True`` 计次**（即逐块「块内覆盖不足」）。截断与整体覆盖不足
        都是源侧条件，保持可重试：``truncated`` 是纯墙钟判据，慢但在线的镜像每块数据
        完整也会命中；``_coverage_short`` 是源端深度。把这两类计次等于「源一慢/一浅，
        全市场就收敛到有缺口完成」—— 源恢复后取不回来，正是本模块要防的事
        （VEW-65 三轮评审）。

        市场级兜底（与 ``_classify_empty`` 同一机制、同一门槛）：即便只对逐块判据计次，
        单标的判据仍分不开「这个标的停牌」与「源整体变浅」—— 东财备源降级只给最近几天
        时，每个标的的最新一块都会块首缺失。因此本轮计次标的占**全市场**过半时，视作
        源侧事件，整轮不计次（全部保持可重试 ``failed``）。

        兜底**只在全市场口径下生效**（``universe_source != "explicit"``）：占比要回答的
        是市场级问题，分母必须是全市场。显式给了标的清单（自选池 / 单标的验证）时占比
        没有市场含义 —— 1 只标的报覆盖问题会算出 100%，把「该股停牌」误判成源故障，
        让它永远无法记终态。

        分母是 ``universe_size`` 而**不是** ``requested``（VEW-65 四轮评审）。两者看着
        只差一个 ``skipped``，语义却相反，且 ``_classify_empty`` 用 ``requested`` 是对的
        —— 不要「统一」它们：

        - ``_classify_empty`` 的输出是**当轮分类**，误判代价 = 一次重试（``failed`` 会
          重试），分母偏小只会让它更保守，安全。
        - 这里的输出喂给**跨轮累加器**（``journal.attempts``）。分母取 ``requested`` 时，
          续跑场景会「吸收」：待采集合按构造就等于「有缺口的标的」，于是第 2 轮起占比
          恒为 100%、兜底每轮触发 → 计数不前进 → 状态不变 → 下一轮输入完全相同 →
          永远出不去。实测 11 只标的（3 只停牌）跑 4 轮：``attempts`` 全程停在 1、
          ``gapped`` 恒为 0、``skipped`` 永远填不满 —— 正是计次机制当初要修的症状，
          只是从标的口径换到了市场口径。

        残余风险（已知、可接受）：分母放大到全市场后，若源侧事件只影响**剩下的少数
        待采标的**（待采本身已经很小），占比不过半、兜底不触发，那几只可能被计次。
        代价有界 —— 就那几只，且它们本来就是有缺口的标的，``errors`` 里有原因，
        ``retry_gaps=True`` 可重取。要盖住它得再加绝对数量下限，但会同时挡掉小股票池
        的兜底用例，不划算。
        """
        countable = [(s, r) for s, r, ok in candidates if ok]
        if not countable:
            return

        limit = float(settings.MINUTE_COLLECT_EMPTY_RATIO_LIMIT)
        market_scale = result.universe_source != "explicit"
        # 分母是**全市场**：待采集合按构造被「有缺口的标的」选择过，不是市场的随机
        # 抽样，拿它当分母会让兜底在续跑时每轮都触发、把跨轮计数器冻住。理由详见
        # ``_apply_gap_attempts`` 的同名段落 —— 那两处的分母刻意不同，别统一。
        attempted = max(1, result.universe_size)
        ratio = len(countable) / attempted
        if market_scale and ratio > limit:
            message = (
                f"覆盖问题占比 {ratio:.0%} 超过阈值 {limit:.0%}"
                f"（本轮覆盖问题 {len(countable)}/{attempted}，全市场 {result.universe_size}）"
                f"，疑似源侧事件，本轮 {len(countable)} 个标的均不计次、保持可重试"
            )
            logger.error("分钟回填源异常: %s", message)
            if len(result.errors) < 10:
                result.errors.append(message)
            return

        promoted = 0
        for symbol, reason in countable:
            if journal.record_gap_failure(symbol, reason, max_attempts):
                # 本轮它已记 failed，改判终态：两个计数器互斥，要一起挪。
                result.failed -= 1
                result.gapped += 1
                promoted += 1
        if promoted:
            logger.info(
                "分钟回填: %d 个标的连续 %d 轮覆盖不足，记有缺口完成（不再重试）",
                promoted,
                max_attempts,
            )
            if len(result.errors) < 10:
                result.errors.append(
                    f"{promoted} 个标的连续 {max_attempts} 轮覆盖不足，"
                    f"记有缺口完成、不再重试（retry_gaps=True 可重取）"
                )

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

        分母用 ``result.requested``（**本轮实际尝试数**）而不是 ``universe_size``：
        续跑时后者是扣掉 ``skipped`` 之前的全量，会把分母放大、占比系统性低估，让
        「本轮待采的标的全部取空」这种最该兜底的情形反而漏过（VEW-65 评审②）。
        方向上也偏安全：续跑批次小、误判成源故障的代价只是一次重试（``failed`` 会重试），
        而漏判的代价是这批标的历史**永久缺失**（``empty`` 是终态）。

        **注意**：``_apply_gap_attempts`` 的市场级兜底用的是 ``universe_size``，与这里
        刻意不同 —— 那边的输出喂给跨轮累加器，用 ``requested`` 会在续跑时形成吸收态
        （计数被兜底冻住、永远收敛不了，VEW-65 四轮评审）。判断依据是「误判的代价是
        一次重试，还是一个被冻住的计数器」，不是「两处长得像不像」。别统一它们。
        """
        if not candidates:
            return
        evidence = PROBE_OK if result.fetched > 0 else probe
        limit = float(settings.MINUTE_COLLECT_EMPTY_RATIO_LIMIT)
        market_scale = result.universe_source != "explicit"
        attempted = max(1, result.requested)
        ratio = len(candidates) / attempted

        if evidence == PROBE_OK and (not market_scale or ratio <= limit):
            result.empty += len(candidates)
            for symbol in candidates:
                journal.mark(symbol, "empty")
            return

        if evidence == PROBE_OK and market_scale:
            # 源可用但空结果占了大半 —— 源只坏了一部分，探针恰好落在好的那部分
            reason = (
                f"空结果占比 {ratio:.0%} 超过阈值 {limit:.0%}"
                f"（本轮尝试 {len(candidates)}/{attempted}，全市场 {result.universe_size}）"
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

        第二源（腾讯 ``ifzq``）由 VEW-63 接入。模块不可用时报告里标
        ``secondary_unavailable``（降级而非抛错），比较逻辑本身可离线单测。
        """
        start = _coerce_date(start_date)
        end = _coerce_date(end_date)
        report = CrossCheckReport(
            period=str(period), start_date=start.isoformat(), end_date=end.isoformat()
        )
        for symbol in symbols:
            symbol = str(symbol).zfill(6)
            primary, _, _ = self._fetch_chunk(symbol, str(period), start, end)
            secondary = self._fetch_secondary(symbol, str(period), start, end)
            if secondary is None:
                # 第二源模块不可用 → 整份报告标不可用
                report.secondary_unavailable = True
                return report
            if primary is None or primary.empty:
                continue
            report.compared_symbols += 1
            if secondary.empty:
                # 源在、但这个窗口没数据（如窗口早于其历史深度）：计入 secondary_empty，
                # 不当作「比对通过」。空表走不进 compare_frames，也不会污染 mismatch 计数。
                report.secondary_empty += 1
                continue
            compare_frames(primary, secondary, report)
        return report

    def _fetch_secondary(
        self, symbol: str, period: str, start: date, end: date
    ) -> Optional[pd.DataFrame]:
        """取第二源（腾讯 ``ifzq``）**整段区间**的数据，成交量统一为「股」。

        返回 ``None`` 表示第二源模块不可用（import 失败）；返回空表表示源在、但这个
        窗口没数据。两者语义不同，调用方据此分别记 ``secondary_unavailable`` /
        ``secondary_empty`` —— 不抛异常：交叉校验是**验证**步骤，缺源时应如实报告而不是
        让回填流程崩掉。

        **按区间翻页**：单页上限 800 根（``_SECONDARY_PAGE_BARS``），只取最新一页的话
        1min 只覆盖约 3.3 个交易日、5min 约 16 个交易日，拿它去「校验」4 个月的窗口等于
        只比了尾巴（VEW-65 评审④）。翻页用 ``start_time`` 游标（VEW-63 的契约：传上一页
        最旧的时间戳取更旧的一页）。

        对未知契约的防御：游标**没有推进**（源忽略了 ``start_time``）或页数/时间超限时
        立即停手 —— 宁可少比几页让 ``coverage`` 把它判成不通过，也不能原地打转或死循环。
        """
        try:
            from app.providers.astock_data import (
                tencent_minute_bars,
                tencent_minute_frame,
            )
        except ImportError:
            logger.warning(
                "腾讯 ifzq 分钟源不可用（app.providers.astock_data 导入失败），本次跳过交叉校验"
            )
            return None

        api_period = {"1": "m1", "5": "m5", "15": "m15", "30": "m30", "60": "m60"}.get(
            str(period)
        )
        if api_period is None:
            return None

        deadline = time.monotonic() + float(settings.MINUTE_BACKFILL_FETCH_BUDGET)
        pages: list[pd.DataFrame] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _ in range(_SECONDARY_MAX_PAGES):
            if time.monotonic() >= deadline:
                logger.warning(
                    "交叉校验第二源翻页超预算，按已取页比对: symbol=%s period=%s",
                    symbol,
                    period,
                )
                break
            bars = tencent_minute_bars(
                symbol,
                api_period,
                start_time=cursor,
                count=_SECONDARY_PAGE_BARS,
                _record=False,
            )
            page = tencent_minute_frame(bars)
            if page is None or page.empty:
                break
            page = page.drop_duplicates(subset=["datetime"], keep="last")
            oldest = pd.to_datetime(page["datetime"]).min()
            pages.append(page)
            # 已经够到区间起点 → 不用再往旧翻
            if oldest.date() <= start:
                break
            next_cursor = oldest.strftime("%Y-%m-%d %H:%M:%S")
            if next_cursor in seen_cursors:
                # 游标没推进：源忽略了 start_time。停手，别原地打转。
                logger.warning(
                    "交叉校验第二源游标未推进（start_time 未生效？），停止翻页: %s",
                    symbol,
                )
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        if not pages:
            return pd.DataFrame()
        frame = pd.concat(pages, ignore_index=True)
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
