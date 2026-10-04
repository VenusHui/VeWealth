"""本地分钟库（Parquet 分区）+ 全市场采集器 + 在线表批量 upsert 的单元测试（VEW-64）。

覆盖的契约：
- 分钟库：规范化 / 幂等重写 / 新数据胜出 / 原子落盘 / 分区与标的裁剪 / 坏分区拒绝覆盖；
- 采集器：断点续采跳过已确认标的、失败可重试、跨日 bar 被裁掉、并发取数结果完整、
  空结果三分类（源故障 → failed / 疑似休市 → no_session / 停牌 → empty，前两者可重试）；
- 在线表：批量 upsert 幂等（sqlite 走非 PG 退化路径），唯一键含 period。
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.stock_data import StockMinuteData
from app.services.data_collector import DataCollector
from app.services.minute_collector import (
    PROBE_DOWN,
    PROBE_NO_SESSION,
    PROBE_OK,
    MinuteCollector,
)
from app.services.minute_store import (
    ALL_COLUMNS,
    MinuteLibrary,
    MinutePartitionError,
)

TRADE_DATE = date(2026, 10, 2)


def _bars(symbol: str, day: date = TRADE_DATE, bars: int = 3, close0: float = 10.0):
    """构造一段分钟 bar（列名用数据源的英文命名，验证规范化路径）。"""
    stamps = pd.date_range(f"{day.isoformat()} 09:30:00", periods=bars, freq="1min")
    return pd.DataFrame(
        {
            "datetime": stamps,
            "stock_code": [symbol] * bars,
            "open": [close0] * bars,
            "high": [close0 + 0.1] * bars,
            "low": [close0 - 0.1] * bars,
            "close": [close0 + i * 0.01 for i in range(bars)],
            "volume": [100.0 * (i + 1) for i in range(bars)],
        }
    )


class FakeProvider:
    """记录调用次数、可注入空/异常的取数桩。"""

    def __init__(self, empty=(), fail=()):
        self.empty = set(empty)
        self.fail = set(fail)
        self.calls: list[tuple[str, str, str]] = []

    def fetch_minute_data(
        self,
        stock_code,
        start_datetime,
        end_datetime,
        period="1",
        adjust="",
        deadline=None,
        **kwargs,
    ):
        self.calls.append((stock_code, str(period), str(start_datetime)[:10]))
        if stock_code in self.fail:
            raise RuntimeError("fetch boom")
        if stock_code in self.empty:
            return None
        return _bars(stock_code)


# ---------------------------------------------------------------------------
# 分钟库
# ---------------------------------------------------------------------------


def test_normalize_keeps_last_on_duplicate_key():
    from app.services.minute_store import normalize_bars

    df = _bars("000001", bars=2)
    df.loc[1, "close"] = 99.0
    dup = pd.concat([df, df.iloc[[1]]], ignore_index=True)

    out = normalize_bars(dup, "1", TRADE_DATE)
    assert len(out) == 2
    assert out["close"].tolist()[1] == pytest.approx(99.0)


def test_write_then_read_round_trip(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    written = lib.write_bars("1", TRADE_DATE, _bars("000001"))
    assert written == 3

    out = lib.read_bars("1", TRADE_DATE, TRADE_DATE)
    assert list(out.columns) == list(ALL_COLUMNS)
    assert out["stock_code"].unique().tolist() == ["000001"]
    assert out["period"].unique().tolist() == ["1"]
    assert out["close"].tolist() == pytest.approx([10.0, 10.01, 10.02])
    # 读出的 trade_date 是 datetime64（盘上是 date32），消费方可直接做日期运算
    assert str(out["trade_date"].dtype).startswith("datetime64")
    assert out["trade_date"].dt.date.unique().tolist() == [TRADE_DATE]


def test_write_is_idempotent(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    second = lib.write_bars("1", TRADE_DATE, _bars("000001"))
    assert second == 3
    assert len(lib.read_bars("1", TRADE_DATE, TRADE_DATE)) == 3


def test_rewrite_overwrites_existing_key(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001", close0=10.0))
    lib.write_bars("1", TRADE_DATE, _bars("000001", close0=20.0))

    out = lib.read_bars("1", TRADE_DATE, TRADE_DATE)
    assert len(out) == 3
    assert out["close"].tolist()[0] == pytest.approx(20.0)


def test_write_leaves_no_temp_file(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    leftovers = [p.name for p in lib.partition_dir("1", TRADE_DATE).iterdir()]
    assert leftovers == ["bars.parquet"]


def test_empty_write_does_not_create_partition(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    assert lib.write_bars("1", TRADE_DATE, pd.DataFrame()) == 0
    assert lib.available_dates("1") == []
    assert not lib.partition_path("1", TRADE_DATE).exists()


def test_periods_are_isolated(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    lib.write_bars("5", TRADE_DATE, _bars("000001"))

    assert lib.available_dates("1") == [TRADE_DATE.isoformat()]
    assert lib.available_dates("5") == [TRADE_DATE.isoformat()]
    assert len(lib.read_bars("1", TRADE_DATE, TRADE_DATE)) == 3
    assert len(lib.read_bars("5", TRADE_DATE, TRADE_DATE)) == 3


def test_read_filters_symbols_and_date_range(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    lib.write_bars("1", TRADE_DATE, _bars("600519"))
    other = date(2026, 10, 3)
    lib.write_bars("1", other, _bars("000001", day=other))

    only_600519 = lib.read_bars("1", TRADE_DATE, other, symbols=["600519"])
    assert only_600519["stock_code"].unique().tolist() == ["600519"]

    single_day = lib.read_bars("1", TRADE_DATE, TRADE_DATE)
    assert len(single_day) == 6


def test_covered_symbols_reports_landed_codes(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    assert lib.covered_symbols("1", TRADE_DATE) == set()
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    assert lib.covered_symbols("1", TRADE_DATE) == {"000001"}


def test_stats_summarizes_partitions(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    stats = lib.stats()
    assert stats["periods"]["1"]["dates"] == 1
    assert stats["periods"]["1"]["rows"] == 3
    assert stats["periods"]["1"]["bytes"] > 0
    assert stats["periods"]["1"]["corrupt"] == []


# ---------------------------------------------------------------------------
# 坏分区：拒绝覆盖 / 拒绝静默跳过（M2）
# ---------------------------------------------------------------------------


def _corrupt(lib: MinuteLibrary, period: str = "1", day: date = TRADE_DATE) -> bytes:
    """把分区文件写成不可解析的内容，返回损坏后的字节。"""
    path = lib.partition_path(period, day)
    path.write_bytes(b"this is not a parquet file")
    return path.read_bytes()


def test_write_refuses_to_overwrite_unreadable_partition(tmp_path: Path):
    """坏分区必须拒绝覆盖：覆盖 = 该分区里其它标的的 bar 被静默抹掉且无法恢复。"""
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    damaged = _corrupt(lib)

    with pytest.raises(MinutePartitionError) as exc:
        lib.write_bars("1", TRADE_DATE, _bars("600519"))

    assert "不可读" in str(exc.value)
    assert lib.partition_path("1", TRADE_DATE).read_bytes() == damaged


def test_read_bars_raises_on_corrupt_partition(tmp_path: Path):
    """读路径不能静默跳过坏分区，否则回测会把缺了一天的结果当成完整结果。"""
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    _corrupt(lib)

    with pytest.raises(MinutePartitionError):
        lib.read_bars("1", TRADE_DATE, TRADE_DATE)


def test_covered_symbols_raises_on_corrupt_partition(tmp_path: Path):
    """坏分区不能被当成「一个标的都没采」，否则会白白重采一轮全市场。"""
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    _corrupt(lib)

    with pytest.raises(MinutePartitionError):
        lib.covered_symbols("1", TRADE_DATE)


def test_stats_lists_corrupt_partitions(tmp_path: Path):
    """统计不因坏文件中断，但必须把它列出来，不能静默跳过。"""
    lib = MinuteLibrary(tmp_path)
    lib.write_bars("1", TRADE_DATE, _bars("000001"))
    _corrupt(lib)

    stats = lib.stats()
    assert stats["periods"]["1"]["corrupt"] == [TRADE_DATE.isoformat()]
    assert stats["periods"]["1"]["rows"] == 0


def test_normalize_newest_batch_wins_for_duplicate_key():
    """契约守卫：同 key 去重必须「新批次胜出」，且不依赖排序是否稳定。

    两批同 key 数据按顺序拼接（第二批 = 新数据），每批内 key 重复多次。当前 pandas 对
    多列 ``sort_values`` 走稳定的 ``np.lexsort``，所以「先排序再去重」在本例下也正确
    （已按 1.32M 行验证）——本用例锁的是契约本身：实现换成先去重再排序（或排序不再
    稳定）时，胜者仍必须是新批次。
    """
    from app.services.minute_store import normalize_bars

    old = pd.concat([_bars("000001", close0=1.0) for _ in range(50)], ignore_index=True)
    new = pd.concat([_bars("000001", close0=9.0) for _ in range(50)], ignore_index=True)

    out = normalize_bars(pd.concat([old, new], ignore_index=True), "1", TRADE_DATE)
    assert len(out) == 3
    assert out["close"].tolist() == pytest.approx([9.0, 9.01, 9.02])


# ---------------------------------------------------------------------------
# 采集器
# ---------------------------------------------------------------------------


def test_collect_writes_bars_and_reports_latency(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    provider = FakeProvider()
    collector = MinuteCollector(db=None, provider=provider, library=lib)

    result = collector.collect("1", TRADE_DATE, symbols=["000001", "600519"])

    assert result.fetched == 2
    assert result.bars_written == 6
    assert result.failed == 0 and result.empty == 0
    assert result.avg_fetch_sec >= 0
    assert len(lib.covered_symbols("1", TRADE_DATE)) == 2
    # 每个标的都带上了正确的周期与日期窗口
    assert provider.calls[0][1] == "1"
    assert provider.calls[0][2] == TRADE_DATE.isoformat()


def test_collect_resume_skips_confirmed_symbols(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    provider = FakeProvider(empty={"000002"})
    collector = MinuteCollector(db=None, provider=provider, library=lib)

    first = collector.collect("1", TRADE_DATE, symbols=["000001", "000002"])
    assert first.fetched == 1 and first.empty == 1 and first.skipped == 0
    calls_after_first = len(provider.calls)

    second = collector.collect("1", TRADE_DATE, symbols=["000001", "000002"])
    assert second.skipped == 2
    assert second.requested == 0
    assert len(provider.calls) == calls_after_first  # 断点生效，一次取数都没发生


def test_collect_force_refetches_everything(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    provider = FakeProvider()
    collector = MinuteCollector(db=None, provider=provider, library=lib)

    collector.collect("1", TRADE_DATE, symbols=["000001"])
    forced = collector.collect("1", TRADE_DATE, symbols=["000001"], force=True)

    assert forced.skipped == 0 and forced.fetched == 1
    # 每轮 = 1 次源探针 + 1 次批量取数（探针与批量走同一条 provider 路径）
    assert len(provider.calls) == 4
    assert len(lib.read_bars("1", TRADE_DATE, TRADE_DATE)) == 3


def test_collect_retries_failed_symbols_on_rerun(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    provider = FakeProvider(fail={"000003"})
    collector = MinuteCollector(db=None, provider=provider, library=lib)

    first = collector.collect("1", TRADE_DATE, symbols=["000001", "000003"])
    assert first.failed == 1 and first.fetched == 1
    assert "000003" in first.errors[0]

    provider.fail = set()  # 源恢复
    second = collector.collect("1", TRADE_DATE, symbols=["000001", "000003"])
    assert second.skipped == 1  # 已成功的 000001 仍被跳过
    assert second.fetched == 1  # 失败标的被重试并成功


def test_collect_drops_bars_outside_trade_date(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    provider = FakeProvider()
    collector = MinuteCollector(db=None, provider=provider, library=lib)

    class CrossDayProvider(FakeProvider):
        def fetch_minute_data(self, stock_code, start_datetime, end_datetime, **kw):
            self.calls.append((stock_code, "1", str(start_datetime)[:10]))
            prev = date(2026, 10, 1)
            return pd.concat(
                [_bars(stock_code, day=prev), _bars(stock_code)], ignore_index=True
            )

    collector = MinuteCollector(db=None, provider=CrossDayProvider(), library=lib)
    result = collector.collect("1", TRADE_DATE, symbols=["000001"])

    assert result.bars_written == 3
    out = lib.read_bars("1", TRADE_DATE, TRADE_DATE)
    assert out["trade_time"].dt.date.unique().tolist() == [TRADE_DATE]


def test_collect_with_workers_is_complete(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    provider = FakeProvider()
    collector = MinuteCollector(db=None, provider=provider, library=lib)
    symbols = [f"{i:06d}" for i in range(1, 26)]

    result = collector.collect("1", TRADE_DATE, symbols=symbols, workers=8)

    assert result.fetched == 25
    assert len(lib.covered_symbols("1", TRADE_DATE)) == 25
    assert result.bars_written == 75


def test_collect_flushes_in_batches(tmp_path: Path):
    lib = MinuteLibrary(tmp_path)
    provider = FakeProvider()
    collector = MinuteCollector(db=None, provider=provider, library=lib)
    symbols = [f"{i:06d}" for i in range(1, 11)]

    result = collector.collect("1", TRADE_DATE, symbols=symbols, workers=2)

    assert result.bars_written == 30
    assert len(lib.covered_symbols("1", TRADE_DATE)) == 10


# ---------------------------------------------------------------------------
# 空结果分类：源故障可重试 / 非交易日终态（M1）
# ---------------------------------------------------------------------------


class HolidayProvider(FakeProvider):
    """目标日无行情、更早的交易日有行情（模拟节假日 / 全市场休市）。"""

    def fetch_minute_data(self, stock_code, start_datetime, end_datetime, **kwargs):
        day = date.fromisoformat(str(start_datetime)[:10])
        self.calls.append((stock_code, "1", day.isoformat()))
        if day >= TRADE_DATE:
            return None
        return _bars(stock_code, day=day)


def test_collect_source_down_keeps_empty_retryable(tmp_path: Path):
    """源故障时空结果必须是可重试失败，否则重跑会永久跳过、整天数据静默缺失。"""
    lib = MinuteLibrary(tmp_path)
    # 连探针样本股都取不到 = 源整体不可用（VEW-60 的镜像静默返回空就是这个形态）
    provider = FakeProvider(empty={"000001", "000002"})
    collector = MinuteCollector(db=None, provider=provider, library=lib)

    first = collector.collect("1", TRADE_DATE, symbols=["000001", "000002"])
    assert first.source_probe == PROBE_DOWN
    assert first.empty == 0 and first.failed == 2
    assert any("可重试失败" in e for e in first.errors)

    provider.empty = set()  # 源恢复
    second = collector.collect("1", TRADE_DATE, symbols=["000001", "000002"])
    assert second.skipped == 0  # 没有被写成终态，所以两个标的都会被重试
    assert second.requested == 2 and second.fetched == 2


def test_collect_holiday_keeps_no_session_retryable(tmp_path: Path):
    """非交易日单独一桶且**可重试**：不打 ERROR、不污染 failed，但也不写成终态。

    记终态看似「省掉节假日重取」，实际成本为零：没有自动重跑机制、调度只针对
    date.today()，所以节假日那天的记录永远不会被自动重取；反过来源滞后一天时
    （镜像缓存滞后 + 备源被限流，同样命中 NO_SESSION）记终态就是永久丢一天。
    """
    lib = MinuteLibrary(tmp_path)
    collector = MinuteCollector(db=None, provider=HolidayProvider(), library=lib)

    first = collector.collect("1", TRADE_DATE, symbols=["000001", "000002"])
    assert first.source_probe == PROBE_NO_SESSION
    assert first.no_session == 2
    assert first.empty == 0 and first.failed == 0
    assert first.errors == []  # 不是源故障，不打 ERROR / 不写 errors

    # 可重试：不会被 done() 跳过（这是与 empty 的关键区别）
    second = collector.collect("1", TRADE_DATE, symbols=["000001", "000002"])
    assert second.skipped == 0 and second.requested == 2


def test_collect_source_lag_day_is_recoverable(tmp_path: Path):
    """M3 场景：源只能给历史、给不了今天 —— 疑似休市但其实是交易日，必须能补回。

    镜像缓存滞后一天 + 备源被限流 → 目标日探针失败、回看成功 → NO_SESSION；
    此时批量取数遇到同一个源状态，全市场几乎全空。若这一支是终态，这一天在回测
    事实源里永久缺失且日志讲的是可信的故事（「判定为非交易日」）。
    """
    lib = MinuteLibrary(tmp_path)
    provider = HolidayProvider()
    collector = MinuteCollector(db=None, provider=provider, library=lib)
    symbols = [f"{i:06d}" for i in range(1, 11)]

    lagged = collector.collect("1", TRADE_DATE, symbols=symbols)
    assert lagged.source_probe == PROBE_NO_SESSION
    assert lagged.no_session == 10 and lagged.empty == 0

    # 镜像追上（当天数据上架）后重跑：10 个标的全部补齐，没有被终态挡住
    provider.calls.clear()

    class CaughtUpProvider(FakeProvider):
        def fetch_minute_data(self, stock_code, start_datetime, end_datetime, **kw):
            self.calls.append((stock_code, "1", str(start_datetime)[:10]))
            return _bars(stock_code)

    recovered = MinuteCollector(
        db=None, provider=CaughtUpProvider(), library=lib
    ).collect("1", TRADE_DATE, symbols=symbols)
    assert recovered.requested == 10 and recovered.fetched == 10
    assert len(lib.covered_symbols("1", TRADE_DATE)) == 10


def test_collect_lookback_covers_long_holiday(tmp_path: Path):
    """回看窗口要覆盖含相邻周末的长假（国庆 8 天 / 春节 8–9 天）。"""
    lib = MinuteLibrary(tmp_path)
    long_holiday = date(2026, 10, 9)  # 目标日

    class LongHolidayProvider(FakeProvider):
        """只有 9 天前那个交易日有行情（模拟长假尾部）。"""

        def fetch_minute_data(self, stock_code, start_datetime, end_datetime, **kw):
            day = date.fromisoformat(str(start_datetime)[:10])
            self.calls.append((stock_code, "1", day.isoformat()))
            if (long_holiday - day).days >= 9:
                return _bars(stock_code, day=day)
            return None

    collector = MinuteCollector(db=None, provider=LongHolidayProvider(), library=lib)
    result = collector.collect("1", long_holiday, symbols=["000001", "000002"])

    # 9 天前有 bar → 仍判 NO_SESSION（若窗口是 7 天会误判成 PROBE_DOWN 并打假 ERROR）
    assert result.source_probe == PROBE_NO_SESSION
    assert result.no_session == 2 and result.failed == 0
    assert result.errors == []


def test_collect_flags_anomaly_when_most_symbols_empty(tmp_path: Path):
    """探针健康但空结果占全市场过半 —— 源只坏了一部分，空结果必须可重试。"""
    lib = MinuteLibrary(tmp_path)
    symbols = [f"{i:06d}" for i in range(2, 12)]  # 探针样本股 000001 不在其中
    provider = FakeProvider(empty=set(symbols))
    collector = MinuteCollector(db=None, provider=provider, library=lib)

    result = collector.collect("1", TRADE_DATE, symbols=symbols)

    assert result.source_probe == PROBE_OK  # 样本股 000001 正常
    assert result.empty == 0 and result.failed == 10
    assert any("占比" in e for e in result.errors)


def test_collect_suspended_symbol_stays_terminal(tmp_path: Path):
    """源健康时的空结果 = 该标的当日确实无数据（停牌），记终态且不反复重取。"""
    lib = MinuteLibrary(tmp_path)
    provider = FakeProvider(empty={"600519"})
    collector = MinuteCollector(db=None, provider=provider, library=lib)
    symbols = [f"{i:06d}" for i in range(1, 21)] + ["600519"]

    first = collector.collect("1", TRADE_DATE, symbols=symbols)
    assert first.source_probe == PROBE_OK
    assert first.empty == 1 and first.failed == 0

    second = collector.collect("1", TRADE_DATE, symbols=symbols)
    assert second.skipped == 21 and second.requested == 0


# ---------------------------------------------------------------------------
# 在线表批量 upsert
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_session():
    engine = create_engine("sqlite://")
    StockMinuteData.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _collector_df(symbol: str, period: str = "1"):
    df = _bars(symbol).rename(
        columns={
            "datetime": "trade_time",
            "open": "open_price",
            "high": "high_price",
            "low": "low_price",
            "close": "close_price",
        }
    )
    df["stock_code"] = symbol
    df["period"] = period
    df["trade_date"] = TRADE_DATE
    return df


def test_bulk_upsert_is_idempotent(sqlite_session):
    collector = DataCollector(sqlite_session)
    df = _collector_df("000001")

    first = collector._bulk_upsert(df, "000001", TRADE_DATE, "1")
    second = collector._bulk_upsert(df, "000001", TRADE_DATE, "1")

    assert first == 3
    assert second == 0  # 非 PG 退化路径：已存在的 key 不重复插入
    assert sqlite_session.query(StockMinuteData).count() == 3


def test_bulk_upsert_keeps_periods_separate(sqlite_session):
    collector = DataCollector(sqlite_session)
    collector._bulk_upsert(_collector_df("000001", "1"), "000001", TRADE_DATE, "1")
    collector._bulk_upsert(_collector_df("000001", "5"), "000001", TRADE_DATE, "5")

    assert sqlite_session.query(StockMinuteData).count() == 6


def test_unique_index_includes_period():
    """回归守卫：唯一键必须是 (stock_code, period, trade_time)，否则多周期互相覆盖。"""
    unique_indexes = [idx for idx in StockMinuteData.__table__.indexes if idx.unique]
    assert len(unique_indexes) == 1
    assert [c.name for c in unique_indexes[0].columns] == [
        "stock_code",
        "period",
        "trade_time",
    ]


def test_migration_revision_chain():
    """迁移必须挂在 0003 之后，且 upgrade/downgrade 均可用。"""
    # 迁移文件名以数字开头，不能作为模块名 import，按路径加载
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0004_add_minute_period.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0004", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0004_add_minute_period"
    assert module.down_revision == "0003_add_universe_snapshots"
    assert callable(module.upgrade) and callable(module.downgrade)
