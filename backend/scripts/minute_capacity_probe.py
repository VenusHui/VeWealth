"""分钟库容量实测脚本（VEW-65）。

复现 ``docs/plans/2026-10-05-minute-backfill-p1-capacity.md`` 的全部数字。

两个模式：

- ``--mode synth``：复现 P0 文档的估算口径 —— 5921 只 × 240 根、OHLCV 为**均匀随机**
  float64、无 amount。这个口径是物理上界（随机数几乎不可压缩），用来确认 P0 文档的
  「53.4 MB/日 / 39 B/row / 12.7 GB/年」确实来自这里。
- ``--mode real``：抓**真实**分钟 bar（腾讯 ``ifzq`` ``mkline``，本机可达），经
  ``minute_store.normalize_bars`` 规范化后用 ``MinuteLibrary.write_bars`` 落盘，统计
  ``分区文件字节 / 行``。写盘路径、压缩算法（zstd）、列顺序与生产完全一致 —— 这是规划
  该用的数字。

用法::

    cd backend
    python scripts/minute_capacity_probe.py --mode synth
    python scripts/minute_capacity_probe.py --mode real --symbols 300
    python scripts/minute_capacity_probe.py --mode all

注意：本脚本只做**测量**，写到 ``--workdir`` 指定的临时目录（默认 ``/tmp``），
不碰 ``MINUTE_LIBRARY_DIR``，也不碰任何生产卷。它不是回填工具；回填见
``app/services/minute_backfill.py``（默认不执行，需双开关）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.minute_store import MinuteLibrary  # noqa: E402

# 全市场标的数（2026-10-05 生产 security_universe 实测：5491 只在市 + 退市历史）。
MARKET_SYMBOLS = 5921
TRADING_DAYS_PER_YEAR = 244

# 每个周期每日的理论根数（A 股：上午 09:30-11:30 + 下午 13:00-15:00）。
BARS_PER_DAY = {"1": 240, "5": 48, "15": 16, "30": 8, "60": 4}

TENCENT_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
_TENCENT_PERIOD = {"1": "m1", "5": "m5", "15": "m15", "30": "m30", "60": "m60"}
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def sample_codes(n: int) -> list[str]:
    """按板块配比生成待抽样代码（不依赖外部代码表，保证可复现）。

    配比近似 A 股实际结构：深主板 000/002、创业板 300、沪主板 600/601/603、
    科创板 688、北交所 4xx/8xx。只是取数样本，不要求是真实在市标的 —— 取不到的
    会被跳过，统计只按实际拿到的 bar 计算。
    """
    pools = [
        ("000{:03d}", 1000),
        ("002{:03d}", 700),
        ("300{:03d}", 900),
        ("600{:03d}", 1000),
        ("601{:03d}", 500),
        ("603{:03d}", 700),
        ("688{:03d}", 400),
        ("430{:03d}", 200),
        ("830{:03d}", 200),
    ]
    codes: list[str] = []
    for fmt, count in pools:
        codes.extend(fmt.format(i) for i in range(1, count + 1))
    step = max(1, len(codes) // max(1, n))
    return codes[::step][:n]


def tencent_symbol(code: str) -> str:
    if code.startswith(("60", "68", "9")):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code


def fetch_tencent(code: str, period: str, count: int = 800) -> list:
    api_period = _TENCENT_PERIOD[period]
    url = f"{TENCENT_URL}?param={tencent_symbol(code)},{api_period},,{count}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _UA)
    req.add_header("Referer", "https://gu.qq.com/")
    try:
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
        data = json.loads(raw)
    except Exception:
        return []
    node = (data.get("data") or {}).get(tencent_symbol(code)) or {}
    return node.get(api_period) or []


def tencent_frame(code: str, period: str, count: int = 800) -> pd.DataFrame | None:
    """腾讯 bar → 库的规范列。成交量按 100 手→股换算（VEW-63 口径）。"""
    rows = []
    for bar in fetch_tencent(code, period, count):
        if not isinstance(bar, (list, tuple)) or len(bar) < 6:
            continue
        try:
            ts = pd.to_datetime(str(bar[0]), format="%Y%m%d%H%M")
            rows.append(
                [
                    ts,
                    float(bar[1]),
                    float(bar[2]),
                    float(bar[3]),
                    float(bar[4]),
                    float(bar[5]) * 100,
                ]
            )
        except (ValueError, TypeError):
            continue
    if not rows:
        return None
    frame = pd.DataFrame(
        rows, columns=["datetime", "open", "close", "high", "low", "volume"]
    ).drop_duplicates(subset=["datetime"], keep="last")
    frame["stock_code"] = code
    return frame


def measure(
    lib_root: Path, period: str, day: date, frame: pd.DataFrame, label: str
) -> float:
    """按库的真实写入路径落盘，返回 B/row。"""
    lib = MinuteLibrary(lib_root / label)
    lib.write_bars(period, day, frame)
    size = lib.partition_path(period, day).stat().st_size
    return size / max(1, len(frame))


def probe_synth(workdir: Path) -> None:
    """复现 P0 口径：均匀随机 float64、无 amount（物理上界，非真实行情）。"""
    print("=== 合成口径（复现 P0 文档的 12.7 GB/年）===")
    n_bar = BARS_PER_DAY["1"]
    rows = MARKET_SYMBOLS * n_bar
    rng = np.random.default_rng(7)
    stamps = pd.date_range("2026-09-30 09:30:00", periods=n_bar, freq="1min")
    frame = pd.DataFrame(
        {
            "stock_code": np.repeat(
                [f"{i:06d}" for i in range(1, MARKET_SYMBOLS + 1)], n_bar
            ),
            "trade_time": np.tile(stamps.values, MARKET_SYMBOLS),
            "open": rng.uniform(3, 200, rows),
            "high": rng.uniform(3, 200, rows),
            "low": rng.uniform(3, 200, rows),
            "close": rng.uniform(3, 200, rows),
            "volume": rng.uniform(0, 1e6, rows),
        }
    )
    bpr = measure(workdir, "1", date(2026, 9, 30), frame, "synth_1min")
    print(
        f"  1min 全市场单日: {bpr * rows / 1e6:.2f} MB  {bpr:.2f} B/row  "
        f"→ {bpr * rows * TRADING_DAYS_PER_YEAR / 1e9:.2f} GB/年"
    )
    print("  （P0 文档记：53.4 MB/日 / 39 B/row / 12.7 GB/年 —— 应接近）\n")


def probe_real(workdir: Path, period: str, n_symbols: int) -> None:
    """抓真实 bar 并按库的写入路径测量。"""
    codes = sample_codes(n_symbols)
    frames = []
    for i, code in enumerate(codes):
        frame = tencent_frame(code, period)
        if frame is not None:
            frames.append(frame)
        if (i + 1) % 100 == 0:
            print(f"  取数 {i + 1}/{len(codes)}（已拿到 {len(frames)} 只）", flush=True)
    if not frames:
        print(f"  [{period}] 未取到任何数据（网络不可达？），跳过")
        return

    allbars = pd.concat(frames, ignore_index=True)
    allbars["_d"] = pd.to_datetime(allbars["datetime"]).dt.date
    # 取「覆盖标的数最多」的那一天做基准日：腾讯只给最近 800 根，
    # 越近的交易日标的越全，避免用半截日期低估。
    counts = allbars.groupby("_d")["stock_code"].nunique().sort_index()
    day = counts[counts >= counts.max()].index[-1]
    day_df = allbars[allbars["_d"] == day]
    n_sym = day_df["stock_code"].nunique()

    print(
        f"\n=== 真实 bar / {period}min（基准日 {day}）===\n"
        f"  样本 {n_sym} 只 / {len(day_df):,} 行（{len(day_df) / n_sym:.1f} 根/只；"
        f"理论 {BARS_PER_DAY[period]}）"
    )
    for mode in ("nan", "amount"):
        chunk = day_df.copy()
        chunk["amount"] = (
            float("nan") if mode == "nan" else chunk["volume"] * chunk["close"]
        )
        bpr = measure(workdir, period, day, chunk, f"real_{period}min_{mode}")
        per_day = bpr * MARKET_SYMBOLS * BARS_PER_DAY[period]
        print(
            f"  amount={mode:6s}  {bpr:6.2f} B/row  "
            f"全市场单日 {per_day / 1e6:6.2f} MB  年 {per_day * TRADING_DAYS_PER_YEAR / 1e9:5.2f} GB"
        )
    print()


def probe_watchlist(workdir: Path, n_symbols: int) -> None:
    """自选池档：以「标的·日」为单位成本给出估算（自选池规模远小于全市场）。"""
    codes = sample_codes(n_symbols)
    print(f"=== 自选池（单位成本，样本 {n_symbols} 只）===")
    measured = False
    for period in ("1", "5"):
        bpr = _measure_rows(workdir, period, codes)
        if bpr <= 0:
            continue
        measured = True
        kb = bpr * BARS_PER_DAY[period] / 1024
        print(
            f"  {period}min: {kb:.2f} KB/标的/日  "
            f"→ 1 只 {kb * TRADING_DAYS_PER_YEAR / 1024:.1f} MB/年  "
            f"100 只 {kb * 100 * TRADING_DAYS_PER_YEAR / 1024:.0f} MB/年  "
            f"500 只 {kb * 500 * TRADING_DAYS_PER_YEAR / 1024:.0f} MB/年"
        )
    if not measured:
        print("  未取到数据，跳过")
    print()


def _measure_rows(workdir: Path, period: str, codes: list[str]) -> float:
    """基准日全样本落盘，返回 B/row（取覆盖标的数最多的那天）。"""
    frames = []
    for code in codes:
        frame = tencent_frame(code, period)
        if frame is not None:
            frame = frame.copy()
            frame["amount"] = frame["volume"] * frame["close"]
            frames.append(frame)
    if not frames:
        return 0.0
    allbars = pd.concat(frames, ignore_index=True)
    allbars["_d"] = pd.to_datetime(allbars["datetime"]).dt.date
    counts = allbars.groupby("_d")["stock_code"].nunique().sort_index()
    day = counts[counts >= counts.max()].index[-1]
    return measure(
        workdir, period, day, allbars[allbars["_d"] == day], f"wl_{period}min"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="分钟库容量实测（VEW-65）")
    parser.add_argument("--mode", choices=["synth", "real", "all"], default="all")
    parser.add_argument("--periods", default="1,5,15,30,60", help="real 模式测量的周期")
    parser.add_argument("--symbols", type=int, default=300, help="抽样标的数")
    parser.add_argument("--workdir", default="/tmp/vew65_capacity", help="临时落盘目录")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"临时落盘目录：{workdir}（测量完可整个删除）\n")

    if args.mode in ("synth", "all"):
        probe_synth(workdir)
    if args.mode in ("real", "all"):
        for period in args.periods.split(","):
            period = period.strip()
            if period in BARS_PER_DAY:
                probe_real(workdir, period, args.symbols)
        probe_watchlist(workdir, min(args.symbols, 200))

    print(f"完成。临时文件在 {workdir}，确认不需要后可 rm -rf。")


if __name__ == "__main__":
    main()
