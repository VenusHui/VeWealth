"""本地分钟行情库：Parquet 按 ``period`` / ``trade_date`` 分区（VEW-64 P0）。

存储选型（为什么是 Parquet，而不是继续堆在 PG 行存 / TimescaleDB）
------------------------------------------------------------------
1. 容量：全市场 1min ≈ 240 根/日 × 244 日 × 5921 只 ≈ **3.47 亿行/年**。PG 行存下
   这个量级是百 GB 级 + 索引维护与 VACUUM 成本；同一份数据列存压缩后 ≈ 4–6 GB/年，
   单机磁盘完全可承受。
2. 扫描形态：回测按「周期 + 日期区间」扫全市场，Parquet 靠目录裁剪（只开区间内的
   分区）+ 列裁剪（只读用到的列），`pd.read_parquet` 直接产出 DataFrame，与现有
   pandas 回测链路同构；PG 需要为每根 bar 付行存 + 索引代价，跨年区间扫描要走大范围
   索引扫描再拼装。
3. 幂等：一个 ``(period, trade_date)`` 对应一个文件，「重跑当天采集」= 读旧文件 +
   去重合并 + 原子替换，天然可重入，不依赖数据库事务与冲突键；断点续采只需知道
   哪些标的已落盘。
4. 成本：无需新增数据库实例 / 扩展，纯文件系统，迁移与备份都是文件级操作。

职责边界：**本库是归档与回测的事实源**；在线查询路径（分时图 / 告警 / 选股信号）
继续走 PG ``stock_minute_data`` 的近期窗口（行数小、需要按标的点查）。全市场跨年
数据**不写 PG** —— 那是行存扛不住的量级，也是本模块存在的理由。

实测容量基线（本机合成全市场 1min 单日：5921 标的 × 240 根 = 142 万行）
--------------------------------------------------------------------
- 单分区文件 **53.4 MB**（zstd，≈ 39 B/row）→ 244 交易日 ≈ **12.7 GB/年**。
  这比立项时的「4–6 GB/年」估算高 —— 后者按 ~15–20 B/row 假设，实际 OHLCV 全为
  float64 时约 39 B/row。12.7 GB/年 对单机仍是可承受量级（PG 行存同规模要百 GB 级
  + 索引维护），但容量规划应按 13 GB/年/周期 而不是 5 GB/年。
- 写盘（一天全量、按 500 标的 flush 12 次）**4.0 s**；读回整天 **0.13 s**。
- 与取数相比写盘可忽略：真正的成本在采集（见 `minute_collector` 的耗时统计）。

目录布局::

    {root}/period=1/trade_date=2026-10-02/bars.parquet
    {root}/period=5/trade_date=2026-10-02/bars.parquet

即 Hive 风格的 ``period=`` / ``trade_date=`` 分区，一个分区内是**当日全市场**的
bar（而不是每标的一个文件 —— 全市场 1min 单日 5921 个小文件会把文件系统元数据
压垮，也让回测扫描变成 5921 次 open）。

写入契约（与 P1 回填、P2 引擎共用）
----------------------------------
- 规范化列：``stock_code, period, trade_date, trade_time, open, high, low, close,
  volume``（可选 ``amount``）。
- 幂等键：``(stock_code, period, trade_time)``；重复写入同一 key 时**新数据胜出**
  （``keep="last"``），因此「重采某日」是覆盖而不是追加。
- 写盘原子：先写同目录临时文件再 ``os.replace``，读者永远看不到半个文件。
- 分区损坏（存在但读不出来）**一律抛 :class:`MinutePartitionError`**，读写两侧都不
  按「空分区」糊过去：写路径拒绝覆盖（否则该分区里其它标的的 bar 会被静默抹掉且无法
  恢复），读路径拒绝静默跳过（否则缺了一整天会被当成完整结果）。恢复方式：人工检查
  后移走坏文件，再对该日 ``force=True`` 重采。
- 单写者假设：同一分区的并发写入由模块级锁串行化（同进程内安全）；跨进程并发写
  同一分区不在支持范围内（采集是单一调度任务，P1 回填同理）。
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

import pandas as pd

from app.core.config import settings

# 规范化列顺序。``amount`` 为可选列（部分源不返回），缺失时补 NaN 保持 schema 稳定。
CANONICAL_COLUMNS: tuple[str, ...] = (
    "stock_code",
    "period",
    "trade_date",
    "trade_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
OPTIONAL_COLUMNS: tuple[str, ...] = ("amount",)
ALL_COLUMNS: tuple[str, ...] = CANONICAL_COLUMNS + OPTIONAL_COLUMNS

# 幂等键：同一标的、同一周期、同一时间点只保留一行。
DEDUPE_KEYS: tuple[str, ...] = ("stock_code", "period", "trade_time")

PARTITION_FILENAME = "bars.parquet"

# 压缩编解码器。实测（全市场 1min 单日 142 万行合成数据）：snappy 57.0 MB / zstd
# 53.4 MB / brotli 51.1 MB / 不压缩 58.3 MB。zstd 比默认 snappy 小 ~6%，写盘只多
# ~0.1s，读回同样快；brotli 再小 4% 但写盘慢 4 倍，不值得。价格列保持 float64 ——
# float32 能再省 ~30% 空间，但会引入精度误差，破坏 P1 的跨源 OHLC 严格比对。
PARQUET_COMPRESSION = "zstd"

# 同一分区内的写入串行化（同进程）。跨进程单写者由调用方保证（见模块 docstring）。
_write_lock = threading.Lock()


class MinutePartitionError(RuntimeError):
    """分区文件存在但不可读（损坏 / 截断 / 编解码失败）。

    读写两侧都必须把它当错误抛出，不能按「空分区」糊过去：

    - **写路径**：合并退化成「只用本批数据重建分区」，会把该分区里其它标的的 bar
      静默抹掉；而断点日志里它们已被标成 ``completed``，重跑也不会再采回来 ——
      不可恢复的数据丢失。因此宁可失败，也不覆盖。
    - **读路径**：静默跳过等于把「缺了一整天」的结果当成完整结果交给回测。
    """


def _partition_error(path: Path, exc: Exception) -> MinutePartitionError:
    """构造带恢复步骤的分区损坏错误（读写两侧共用同一措辞）。"""
    return MinutePartitionError(
        f"分钟库分区不可读: {path} ({type(exc).__name__}: {exc})；"
        "请人工检查后移走该文件再重采该日（force=True），不要直接覆盖"
    )


def _coerce_trade_date(value: date | datetime | str) -> date:
    """把 ``date`` / ``datetime`` / ``YYYY-MM-DD`` 统一成 ``date``。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _normalize_codes(codes: Iterable[str]) -> list[str]:
    return [str(c).zfill(6) for c in codes if str(c).strip()]


def normalize_bars(
    df: pd.DataFrame,
    period: str,
    trade_date: date | datetime | str,
    stock_code: Optional[str] = None,
) -> pd.DataFrame:
    """把数据源返回的分钟 DataFrame 规范成本库的列与 dtype。

    接受数据源的英文列名（``datetime/open/high/low/close/volume``），也接受库自身的
    规范列名（回读后再次写入的场景）。``stock_code`` / ``trade_date`` 缺失时用入参
    补齐；``amount`` 缺失补 NaN。返回按 ``(stock_code, trade_time)`` 排序去重后的
    DataFrame（幂等：新数据胜出）。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=list(ALL_COLUMNS))

    out = df.copy()
    out.columns = [str(c) for c in out.columns]

    if "trade_time" not in out.columns and "datetime" in out.columns:
        out = out.rename(columns={"datetime": "trade_time"})
    if "trade_time" not in out.columns:
        raise ValueError("分钟数据缺少时间列（trade_time / datetime）")

    if "stock_code" not in out.columns:
        if stock_code is None:
            raise ValueError("分钟数据缺少 stock_code，且未显式传入")
        out["stock_code"] = stock_code

    out["stock_code"] = out["stock_code"].astype(str).str.zfill(6)
    out["trade_time"] = pd.to_datetime(out["trade_time"])
    out["period"] = str(period)
    # 存成 date32（python date 对象）而不是 timestamp：一天一个分区，日期是常量，
    # timestamp64 每天白付 4 B/row（1min 全市场 ≈ 1.4 GB/年）。
    out["trade_date"] = _coerce_trade_date(trade_date)

    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            raise ValueError(f"分钟数据缺少列: {col}")
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")

    if "amount" in out.columns:
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce").astype("float64")
    else:
        out["amount"] = float("nan")

    out = out.loc[:, list(ALL_COLUMNS)]
    out = out.dropna(subset=["open", "high", "low", "close", "volume"])
    # 先去重再排序：把「新数据胜出」写在不依赖排序稳定性的位置上。当前 pandas 对多列
    # ``sort_values`` 走 ``np.lexsort``（稳定），先排序再去重在本例下结果也正确，但那是
    # 实现细节（单列排序走 quicksort，不稳定）；先去重则按原始顺序 ``keep="last"`` 取
    # 最后进来的那批，契约与排序实现无关。
    out = out.drop_duplicates(subset=list(DEDUPE_KEYS), keep="last")
    return out.sort_values(list(DEDUPE_KEYS)).reset_index(drop=True)


class MinuteLibrary:
    """按 ``period`` / ``trade_date`` 分区的本地分钟行情库。"""

    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root if root is not None else settings.MINUTE_LIBRARY_DIR)

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------

    def period_dir(self, period: str) -> Path:
        return self.root / f"period={period}"

    def partition_dir(self, period: str, trade_date: date | datetime | str) -> Path:
        return self.period_dir(period) / f"trade_date={_coerce_trade_date(trade_date)}"

    def partition_path(self, period: str, trade_date: date | datetime | str) -> Path:
        return self.partition_dir(period, trade_date) / PARTITION_FILENAME

    def available_dates(self, period: str) -> list[str]:
        """已落盘的分区日期（升序，``YYYY-MM-DD``）。"""
        base = self.period_dir(period)
        if not base.is_dir():
            return []
        dates: list[str] = []
        for child in base.iterdir():
            if not child.is_dir() or not child.name.startswith("trade_date="):
                continue
            value = child.name.split("=", 1)[1]
            if (child / PARTITION_FILENAME).exists():
                dates.append(value)
        return sorted(dates)

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def write_bars(
        self,
        period: str,
        trade_date: date | datetime | str,
        df: pd.DataFrame,
    ) -> int:
        """把一批 bar 幂等合并进 ``(period, trade_date)`` 分区，返回合并后的总行数。

        与已有分区合并时按 ``(stock_code, period, trade_time)`` 去重且**新数据胜出**，
        因此重跑采集是覆盖而不是追加。写盘原子（临时文件 + ``os.replace``）。

        已有分区不可读时抛 :class:`MinutePartitionError` 而**不覆盖** —— 覆盖会把该
        分区里其它标的的 bar 静默抹掉且无法恢复（见异常类说明）。
        """
        incoming = normalize_bars(df, period, trade_date)
        path = self.partition_path(period, trade_date)

        with _write_lock:
            if incoming.empty:
                # 本次无数据：不落空文件（避免空分区污染 available_dates），也不重写
                # 已有分区，直接回读当前行数。
                if not path.exists():
                    return 0
                return len(self._read_partition(path))

            merged = incoming
            if path.exists():
                existing = self._read_partition(path)
                if not existing.empty:
                    merged = pd.concat([existing, incoming], ignore_index=True)

            if merged.empty:
                return 0

            # 先去重再排序，理由同 normalize_bars（不依赖排序稳定性来保证新数据胜出）。
            merged = merged.drop_duplicates(subset=list(DEDUPE_KEYS), keep="last")
            merged = merged.sort_values(list(DEDUPE_KEYS)).reset_index(drop=True)

            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                merged.to_parquet(
                    tmp_path, index=False, compression=PARQUET_COMPRESSION
                )
                os.replace(tmp_path, path)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)

            return len(merged)

    def _read_partition(self, path: Path) -> pd.DataFrame:
        """读取单个分区文件并补齐 schema（容忍旧分区缺列）。

        读取失败一律抛 :class:`MinutePartitionError`，绝不按空分区处理：返回空会让
        写路径把分区重建成本批数据、让读路径静默少一天，两者都是静默数据丢失。
        """
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            raise _partition_error(path, e) from e
        for col in ALL_COLUMNS:
            if col not in df.columns:
                # 按列类型给默认值：字符串列补空串、数值列补 NaN，避免把 NaN 塞进
                # stock_code 这种字符串列后写出类型不一致的分区。
                df[col] = "" if col in ("stock_code", "period") else float("nan")
        return df.loc[:, list(ALL_COLUMNS)]

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def covered_symbols(
        self, period: str, trade_date: date | datetime | str
    ) -> set[str]:
        """该分区已落盘的标的集合（断点续采的快速判据）。

        分区不可读时抛 :class:`MinutePartitionError`：静默返回空集会让整天的标的
        被当成「未采」而全量重采（浪费一轮 40–60 min 的取数窗口）。
        """
        path = self.partition_path(period, trade_date)
        if not path.exists():
            return set()
        try:
            df = pd.read_parquet(path, columns=["stock_code"])
        except Exception as e:
            raise _partition_error(path, e) from e
        return set(df["stock_code"].astype(str).str.zfill(6).unique())

    def read_bars(
        self,
        period: str,
        start_date: date | datetime | str,
        end_date: date | datetime | str,
        symbols: Optional[Sequence[str]] = None,
    ) -> pd.DataFrame:
        """读取 ``[start_date, end_date]`` 闭区间内某周期的全部 bar。

        ``symbols`` 非空时只返回这些标的（列裁剪后再按标的过滤）。分区**不存在**时
        返回空 DataFrame（而不是抛错）——回测按日期区间扫描时会自然地跨过无数据的日子；
        但分区**存在却读不出来**时抛 :class:`MinutePartitionError`：静默跳过等于把
        缺了一整天的结果当成完整结果返回。

        ⚠️ 本方法把区间内所有分区读进内存（1min 全市场一天 142 万行 ≈ 100 MB）。
        跨年区间（244 天 ≈ 3.5 亿行）不能一次读 —— P2 的引擎应按交易日逐分区迭代
        （``available_dates`` + ``partition_path``），本方法只用于单日 / 短区间。
        """
        start = _coerce_trade_date(start_date)
        end = _coerce_trade_date(end_date)
        if start > end:
            start, end = end, start

        wanted = set(_normalize_codes(symbols)) if symbols else None

        frames: list[pd.DataFrame] = []
        for value in self.available_dates(period):
            try:
                day = date.fromisoformat(value)
            except ValueError:
                continue
            if day < start or day > end:
                continue
            frames.append(self._read_partition(self.partition_path(period, day)))

        if not frames:
            return pd.DataFrame(columns=list(ALL_COLUMNS))

        out = pd.concat(frames, ignore_index=True)
        if wanted is not None:
            out = out[out["stock_code"].astype(str).str.zfill(6).isin(wanted)]
        # 盘上是 date32（常量列，省空间）；读出来转成 datetime64 交给消费方 ——
        # object dtype 的 date 列一旦参与 pandas 运算会退化成逐元素 Python 对象。
        out["trade_date"] = pd.to_datetime(out["trade_date"])
        return out.sort_values(list(DEDUPE_KEYS)).reset_index(drop=True)

    def stats(self) -> dict:
        """库容量概览：分区数、行数、磁盘占用、损坏分区清单。

        预留接口：**尚未接入任何端点**。``/api/health`` 是 docker healthcheck 每 30s
        打一次的路径，而本方法要读全部分区才能统计行数，不适合放进健康检查；等后续
        做运维观测端点或排障脚本时按需调用。
        """
        periods: dict[str, dict] = {}
        period_dirs = sorted(self.root.glob("period=*")) if self.root.is_dir() else []
        for period_dir in period_dirs:
            period = period_dir.name.split("=", 1)[1]
            rows = 0
            size = 0
            corrupt: list[str] = []
            dates = self.available_dates(period)
            for value in dates:
                path = self.partition_path(period, value)
                size += path.stat().st_size
                try:
                    rows += len(pd.read_parquet(path, columns=["stock_code"]))
                except Exception:
                    # 统计不因坏文件中断，但必须把它列出来 —— 静默跳过等于掩盖损坏。
                    corrupt.append(value)
            periods[period] = {
                "dates": len(dates),
                "rows": rows,
                "bytes": size,
                "corrupt": corrupt,
            }
        return {"root": str(self.root), "periods": periods}


# 进程内共享实例（调度任务与 API 复用同一目录配置）
minute_library = MinuteLibrary()
