"""行情获取的结构化 provenance。

日线接口此前把"证券无数据"与"数据源故障"统一吞成空 DataFrame，调用方无法区分，
也无法知道实际起止、来源与复权口径。本模块定义带 provenance 的结果容器，让调用方
能感知覆盖缺口（gap）与失败原因（failure_reason），从而明确降级而非"看起来成功"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class DataProvenance:
    """一次日线/分钟数据拉取的结构化元信息。"""

    source: Optional[str] = None  # mootdx / eastmoney / tushare / None
    adjustment: str = ""  # "" / "qfq" / "hfq"
    requested_start: Optional[str] = None  # 请求起始（YYYY-MM-DD 或原始入参）
    requested_end: Optional[str] = None
    actual_start: Optional[str] = None  # 实际返回的第一根 bar（字符串）
    actual_end: Optional[str] = None  # 实际返回的最后一根 bar（last_bar）
    bar_count: int = 0
    last_bar: Optional[str] = None  # 时间上最后一根 bar 的日期
    gap: bool = False  # 覆盖缺口：实际范围未覆盖请求范围（起点滞后或终点提前）
    failure_reason: Optional[str] = None  # None 表示成功；否则为失败/无数据原因


@dataclass
class DailyDataResult:
    """日线数据 + 结构化 provenance。df 为 None 表示无数据或失败。"""

    df: Optional[pd.DataFrame]
    provenance: DataProvenance
