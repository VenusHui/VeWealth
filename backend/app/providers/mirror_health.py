"""mootdx 镜像的**分周期**健康档案（VEW-70）。

镜像校验（``astock_provider._mirror_serves_bars``）、取数路径的空返回确认
（``_fetch_kline_mootdx``）与源级探针（``probes.probe_mootdx``）都是**按周期**取数
校验的，但健康度此前只按**源**聚合：日线端点抖动与 5min 端点故障在
``/api/health/sources`` 里都表现为同一个 ``mootdx down``，运维无法区分「分钟可用、
日线抖动」与「整源不可用」，也就无法判断该换镜像还是该等抖动过去。

本模块按 ``(镜像, 周期)`` 记录尝试数、成功数、成功率、连续失败数与最近成功 / 失败
时间，并给出按周期的汇总。它**只做观测，不参与裁决** —— 裁决在 astock_provider 里，
写入点与裁决点重合，保证「判定依据」与「对外呈现」是同一批样本。

「不参与裁决」是刻意的：裁决只看**本轮**的样本（``_mirror_serves_bars`` 里的
``confirmed``）。跨轮的历史成功不适合当正面证据 —— 一个刚刚结构性失效的镜像会因为
几十秒前的成功记录被继续采用。要区分「抖动」与「故障」，靠的是本轮内的有界重试
（重连是有效单位，见 ``_probe_period_with_retry``），不是历史窗口。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

# mootdx frequency → 可读周期名。取值来自 mootdx.consts：
# KLINE_5MIN=0, KLINE_15MIN=1, KLINE_30MIN=2, KLINE_1HOUR=3, KLINE_DAILY=4, KLINE_1MIN=8。
PERIOD_LABELS: dict[int, str] = {
    0: "5min",
    1: "15min",
    2: "30min",
    3: "60min",
    4: "daily",
    8: "1min",
}

# 记录上限。镜像池本身是有界的（内置 38 个 + curated + 配置追加），但配置可能写错，
# 这里兜一个上界避免观测数据无界增长；超出时淘汰最久未更新的那条。
_MAX_CELLS = 256


def period_label(freq: int) -> str:
    """周期的可读名；未知 frequency 退化为 ``freq=<n>``，不丢信息。"""
    return PERIOD_LABELS.get(int(freq), f"freq={int(freq)}")


def _now_iso() -> str:
    """当前 UTC 时间 ISO8601 字符串（与 source_health 的时间口径一致）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PeriodCell:
    """单个 ``(镜像, 周期)`` 的探测样本。"""

    server: str
    freq: int
    total: int = 0
    success: int = 0
    failure: int = 0
    consecutive_failures: int = 0
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_error: Optional[str] = None
    last_latency_ms: Optional[float] = None
    last_seen: float = 0.0

    @property
    def success_rate(self) -> Optional[float]:
        if self.total == 0:
            return None
        return round(self.success / self.total, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": period_label(self.freq),
            "freq": self.freq,
            "total": self.total,
            "success": self.success,
            "failure": self.failure,
            "success_rate": self.success_rate,
            "consecutive_failures": self.consecutive_failures,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error": self.last_error,
            "last_latency_ms": self.last_latency_ms,
        }


class MirrorHealthRegistry:
    """按 ``(镜像, 周期)`` 累积探测样本的进程内档案（线程安全）。"""

    def __init__(self, max_cells: int = _MAX_CELLS) -> None:
        self._lock = threading.Lock()
        self._cells: dict[tuple[str, int], PeriodCell] = {}
        self._max_cells = max(int(max_cells), 1)

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------

    def record(
        self,
        server: Optional[str],
        freq: int,
        ok: bool,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """记录一次 ``(镜像, 周期)`` 探测结果。"""
        key = (str(server or "unknown"), int(freq))
        now = _now_iso()
        with self._lock:
            cell = self._cells.get(key)
            if cell is None:
                if len(self._cells) >= self._max_cells:
                    self._evict_oldest_locked()
                cell = PeriodCell(server=key[0], freq=key[1])
                self._cells[key] = cell
            cell.total += 1
            cell.last_seen = time.monotonic()
            cell.last_latency_ms = duration_ms
            if ok:
                cell.success += 1
                cell.consecutive_failures = 0
                cell.last_success_at = now
                cell.last_error = None
            else:
                cell.failure += 1
                cell.consecutive_failures += 1
                cell.last_failure_at = now
                cell.last_error = error

    def _evict_oldest_locked(self) -> None:
        """淘汰最久未更新的单元（调用方须持锁）。"""
        oldest = min(self._cells, key=lambda k: self._cells[k].last_seen)
        self._cells.pop(oldest, None)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """按周期汇总 + 按镜像明细的健康快照。"""
        with self._lock:
            cells = list(self._cells.values())
        periods: dict[str, dict[str, Any]] = {}
        mirrors: dict[str, dict[str, Any]] = {}
        for cell in sorted(cells, key=lambda c: (c.freq, c.server)):
            label = period_label(cell.freq)
            entry = cell.to_dict()
            mirrors.setdefault(cell.server, {})[label] = entry
            agg = periods.setdefault(
                label,
                {
                    "period": label,
                    "freq": cell.freq,
                    "total": 0,
                    "success": 0,
                    "failure": 0,
                    "consecutive_failures": 0,
                    "last_success_at": None,
                    "last_failure_at": None,
                    "mirrors": 0,
                },
            )
            agg["mirrors"] += 1
            for field in ("total", "success", "failure"):
                agg[field] += entry[field]
            # 连续失败取各镜像里最差的一条：该周期只要有一个镜像连续失败，就值得
            # 在汇总里看见，取平均会把单点故障抹平。
            agg["consecutive_failures"] = max(
                agg["consecutive_failures"], entry["consecutive_failures"]
            )
            for field in ("last_success_at", "last_failure_at"):
                if entry[field] and (agg[field] is None or entry[field] > agg[field]):
                    agg[field] = entry[field]
        for agg in periods.values():
            agg["success_rate"] = (
                round(agg["success"] / agg["total"], 4) if agg["total"] else None
            )
        return {"periods": periods, "mirrors": mirrors}

    def reset(self) -> None:
        """清空全部样本（测试用）。"""
        with self._lock:
            self._cells.clear()


# 全局档案实例（镜像校验 / 取数确认 / 源级探针共用）
mirror_health = MirrorHealthRegistry()
