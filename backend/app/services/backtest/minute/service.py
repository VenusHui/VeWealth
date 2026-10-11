"""分钟级回测编排：按需取数 → 分钟撮合 → 基准/因子对齐 → 结果与 provenance。

本模块把 P2 的三块拼起来，对外只暴露一个入口 :func:`run_minute_backtest`：

1. **数据入口**（``minute.data``）：逐标的按需取数（mootdx 为主、腾讯 ``ifzq`` 为次），
   不落常驻库；深度不足按 ``strict`` 报错或按 ``truncate`` 截断并注明。深度裁决只针对
   **用户请求区间**，内部向前扩取的缓冲（指标 warmup、回测首日前收盘）被深度下界挡住
   时只夹紧 —— 否则一个完全落在深度内的请求会因为内部多取几天而被 ``strict`` 判死。
2. **分钟撮合**（``minute.engine``）：滑点/费用、涨跌停、停牌、集合竞价、T+1，
   可选止损止盈。
3. **基准与因子对齐**（``minute.alignment``）：日线序列对齐到分钟 bar 时显式区分
   「收盘后才可用」（因子，无前视）与「当日即用」（基准展示，含前视）。

返回结构与日线 ``BacktestService.run_backtest`` 对齐（``summary`` / ``equity_curve`` /
``trades`` / ``positions_snapshot`` / ``warnings`` / ``diagnostics``），便于后续接入
任务与持久化。``diagnostics.data_provenance`` 记录**取数时间**与**各源实际返回的
区间** —— 纯网络取数下同一区间不可复现，不记这些就无法事后判断一次回测跑在什么
数据上。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Optional

import pandas as pd

from app.services.backtest.costs import CostModel
from app.services.backtest.engine import SecurityRule
from app.services.backtest.metrics import calc_summary
from app.services.backtest.minute.alignment import (
    align_benchmark_to_minute,
    align_daily_to_minute,
)
from app.services.backtest.minute.data import (
    MinuteDataLoader,
    MinuteLoadResult,
    estimate_bar_count,
    provenance_summary,
)
from app.services.backtest.minute.depth import (
    depth_report,
    normalize_period,
    period_minutes,
    resolve_request_window,
)
from app.services.backtest.minute.engine import (
    MinuteExecutionConfig,
    run_minute_portfolio,
    session_eod_curve,
)
from app.services.backtest.minute.session import bars_per_day

#: 交易日 / 自然日换算系数（与日线 warmup 回拉同口径：把交易日数换成自然日时**多取**）
_CALENDAR_FACTOR = 1.6

#: 为保证回测首日也能算涨跌停，额外向前多取的自然日（覆盖一个周末 + 一个节假日）。
#: 分钟 bar 没有「昨日收盘」字段，首日的前收盘只能靠帧内的前一个交易日聚合出来 ——
#: 不向前多取，首日就会静默失去涨跌停约束。与 ``warmup_sessions`` 无关，始终生效。
_LIMIT_REFERENCE_DAYS = 7

BenchmarkLoader = Callable[[str, str, str], Optional[pd.DataFrame]]


def _default_benchmark_loader(
    code: str, start_date: str, end_date: str
) -> Optional[pd.DataFrame]:
    """日线基准取数（复用既有日线链路，含 provenance 与降级提示）。"""
    from app.services.stock_service import stock_service

    df, _, _ = stock_service.get_daily_data(
        symbol=code, start_date=start_date, end_date=end_date
    )
    return df


def _load_minute_frames(
    symbols: list[str],
    period: str,
    request_start: date,
    end_date: date,
    policy: str,
    loader: MinuteDataLoader,
    as_of: date | None,
    lead_start: date,
) -> tuple[dict[str, pd.DataFrame], list[MinuteLoadResult], list[str]]:
    """逐标的取数：以**用户区间**做深度裁决，以 ``lead_start`` 作为实际取数起点。

    两个窗口必须分开传 —— 见 :meth:`MinuteDataLoader.load`。
    """
    frames: dict[str, pd.DataFrame] = {}
    results: list[MinuteLoadResult] = []
    warnings: list[str] = []
    for symbol in symbols:
        result = loader.load(
            symbol,
            period,
            request_start,
            end_date,
            policy=policy,
            as_of=as_of,
            lead_start=lead_start,
        )
        results.append(result)
        if result.df is None or result.df.empty:
            warnings.append(
                f"{symbol}: 分钟数据不可用"
                f"（{result.provenance.failure_reason or '区间内无数据'}），已跳过"
            )
            continue
        frames[symbol] = result.df
    return frames, results, warnings


def run_minute_backtest(
    *,
    symbols: list[str],
    orders_df: pd.DataFrame | None,
    period: str | int,
    start_date: date | str,
    end_date: date | str,
    initial_cash: float,
    cost_model: CostModel | None = None,
    hold_days: int = 1,
    depth_policy: str = "truncate",
    security_rules: dict[str, SecurityRule] | None = None,
    warmup_sessions: int = 0,
    execution: MinuteExecutionConfig | None = None,
    loader: MinuteDataLoader | None = None,
    benchmark: str | None = None,
    benchmark_loader: BenchmarkLoader | None = None,
    as_of: date | str | None = None,
) -> dict[str, Any]:
    """跑一次分钟级组合回测。

    Args:
        symbols: 回测标的（6 位代码）。
        orders_df: 订单（信号）。契约与日线一致：``symbol`` 必填，信号时刻取
            ``signal_datetime``（分钟级），缺省回退 ``trade_date``（按当日收盘处理，
            故最早在下一交易日开盘集合竞价成交）。
        period: 分钟周期，``"1"/"5"/"15"/"30"/"60"``。
        start_date / end_date: 回测区间（含首尾）。
        initial_cash: 初始资金。
        hold_days: 持仓交易日数（T+1 强制 >= 1）。
        depth_policy: ``strict``（超深度报错）/ ``truncate``（截断并注明）。只裁决
            **用户请求区间** —— 内部向前扩取的缓冲（warmup 与首日前收盘）被深度下界
            挡住时只夹紧，不会让 ``strict`` 报错。
        warmup_sessions: 为指标预热额外回拉的交易日数（取数区间向前扩，但净值与成交
            仍从 ``start_date`` 起算）。另有始终生效的首日前收盘缓冲（
            ``_LIMIT_REFERENCE_DAYS`` 自然日），与 warmup 无关。
        benchmark: 基准代码；给定时对齐到分钟网格并在 diagnostics 记录口径。
        loader / benchmark_loader: 注入点（测试与替换源用）。

    Returns:
        dict：``summary`` / ``equity_curve``（分钟粒度）/ ``trades`` /
        ``positions_snapshot``（默认按交易日收盘）/ ``warnings`` / ``diagnostics``。
    """
    key = normalize_period(period)
    cost = cost_model or CostModel()
    cfg = execution or MinuteExecutionConfig(hold_days=hold_days)
    loader = loader or MinuteDataLoader()
    benchmark_loader = benchmark_loader or _default_benchmark_loader
    as_of_date = pd.Timestamp(as_of).date() if as_of is not None else None

    symbols = [str(s).strip() for s in symbols if str(s).strip()]
    if not symbols:
        raise ValueError("symbols 不能为空")

    user_start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    if user_start > end:
        raise ValueError("start_date 不能晚于 end_date")

    # warmup 只为指标预热：取数区间向前扩，但引擎的 trade_start 仍钉在用户区间起点。
    # 额外再加 _LIMIT_REFERENCE_DAYS，保证回测首日也有前收盘价可算涨跌停。
    warmup_days = int(max(0, warmup_sessions) * _CALENDAR_FACTOR) + 1
    buffer_days = warmup_days + _LIMIT_REFERENCE_DAYS

    # 深度裁决只对**用户区间**做：strict 报错、截断文案都是对用户说的，内部缓冲不是
    # 用户请求，把它算进来会让完全合法的请求被判死（strict），或让告警声称「结果不
    # 覆盖原始请求起点」而实际上 trade_start 仍钉在用户起点、结果完整覆盖。
    decision = resolve_request_window(
        key, user_start, end, policy=depth_policy, as_of=as_of_date
    )
    load_start = decision.effective_start

    # 内部缓冲在用户有效起点之上再向前扩，被深度下界挡住时**夹紧**（缓冲是内部便利，
    # 不该触发 strict，也不该被描述成用户的请求超出深度）
    desired_lead_start = load_start - timedelta(days=buffer_days)
    lead_start = max(desired_lead_start, decision.depth_floor)
    buffer_clamped = lead_start > desired_lead_start

    # 用户区间（而非内部缓冲）交给 loader 裁决并留痕 —— 深度截断是逐标的的事实，只在
    # 顶层 diagnostics 可见会让逐标的 provenance 的 truncated/depth_note 恒空
    frames, results, warnings = _load_minute_frames(
        symbols, key, user_start, end, depth_policy, loader, as_of_date, lead_start
    )
    if not frames:
        raise ValueError(
            "无可用分钟数据："
            f"{key} 分钟线在 {load_start}~{end} 区间内一只标的都没取到"
            f"（有效深度 {decision.depth_days} 自然日，下界 {decision.depth_floor}）"
        )

    if decision.truncated:
        # 只描述**用户区间**被截断：内部缓冲被夹紧不在此列（那不影响结果覆盖范围，
        # 只可能让回测首日缺前收盘价，由引擎按实际数据告警）
        warnings.append(
            f"分钟取数深度不足：{decision.note}；用户请求区间起点 {user_start} "
            f"超出源深度，实际回测从 {load_start} 起，结果不覆盖 {user_start}"
        )

    provenance = provenance_summary(results)
    if provenance["gap_count"]:
        warnings.append(
            f"{provenance['gap_count']}/{len(results)} 只标的实际返回区间未覆盖请求区间"
            "（源窗口滚动前移，同区间重跑可能不同），已按实际区间回测"
        )
    if provenance["truncated_count"]:
        warnings.append(
            f"{provenance['truncated_count']}/{len(results)} 只标的的取数区间被深度截断"
        )

    portfolio = run_minute_portfolio(
        market_data_map=frames,
        orders_df=orders_df,
        initial_cash=float(initial_cash),
        cost_model=cost,
        config=cfg,
        security_rules=security_rules,
        trade_start=user_start.isoformat(),
    )
    warnings.extend(portfolio.warnings)

    # 绩效指标按日频算（分钟点会被 calc_summary 当交易日，Sharpe 放大 sqrt(bars/day)）
    eod_curve = session_eod_curve(portfolio.equity_curve)
    summary = calc_summary(eod_curve, portfolio.trades, float(initial_cash))
    summary["final_positions"] = portfolio.final_positions

    diagnostics: dict[str, Any] = {
        "period": key,
        "period_minutes": period_minutes(key),
        "bars_per_session": bars_per_day(key),
        "requested_window": {
            "start": user_start.isoformat(),
            "end": end.isoformat(),
        },
        # 实际取数窗口（含内部缓冲）与它的构成。用户请求区间见 ``requested_window``
        "load_window": {
            "start": lead_start.isoformat(),
            "end": end.isoformat(),
            "desired_start": desired_lead_start.isoformat(),
            "buffer_days": buffer_days,
            "buffer_clamped": buffer_clamped,
            "warmup_sessions": int(max(0, warmup_sessions)),
            "limit_reference_days": _LIMIT_REFERENCE_DAYS,
        },
        "depth_decision": {
            "policy": decision.policy,
            "depth_days": decision.depth_days,
            "depth_floor": decision.depth_floor.isoformat(),
            "exceeded": decision.exceeded,
            "truncated": decision.truncated,
            "note": decision.note,
        },
        "depth": depth_report(as_of_date),
        "data_provenance": provenance,
        "metrics_basis": "session_eod",
        "execution": {
            "hold_days": max(1, int(cfg.hold_days)),
            "lot_size": cfg.lot_size,
            "stop_loss_pct": cfg.stop_loss_pct,
            "take_profit_pct": cfg.take_profit_pct,
            "max_total_position_pct": cfg.max_total_position_pct,
            "default_position_size_pct": cfg.default_position_size_pct,
            "defer_to_next_bar": cfg.defer_to_next_bar,
        },
        "symbols_requested": len(symbols),
        "symbols_with_data": len(frames),
        "event_count": len(portfolio.trades),
    }

    benchmark_curve: list[dict[str, Any]] = []
    if benchmark:
        benchmark_meta, benchmark_curve = _align_benchmark(
            benchmark=benchmark,
            equity_curve=portfolio.equity_curve,
            load_start=load_start,
            end=end,
            benchmark_loader=benchmark_loader,
        )
        diagnostics["benchmark"] = benchmark_meta

    return {
        "summary": summary,
        "equity_curve": portfolio.equity_curve,
        "equity_curve_eod": eod_curve,
        "trades": portfolio.trades,
        "positions_snapshot": portfolio.positions_snapshot,
        "warnings": warnings,
        "diagnostics": diagnostics,
        "symbols": symbols,
        "benchmark_curve": benchmark_curve,
    }


def _align_benchmark(
    *,
    benchmark: str,
    equity_curve: list[dict[str, Any]],
    load_start: date,
    end: date,
    benchmark_loader: BenchmarkLoader,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """基准对齐到分钟网格，返回 (口径 meta, 基准曲线)。

    口径（分钟 / 日线、是否含前视）必须写进 meta —— 日线基准在分钟曲线上的对比只能到
    日粒度，不标注会让人误以为拿到了分钟基准。
    """
    grid = pd.DatetimeIndex(
        sorted({pd.Timestamp(point["datetime"]) for point in equity_curve})
    )
    try:
        raw = benchmark_loader(benchmark, load_start.isoformat(), end.isoformat())
    except Exception as exc:  # 基准失败不该让整次回测失败
        return (
            {
                "code": benchmark,
                "resolution": "none",
                "lookahead": False,
                "note": f"基准取数失败: {type(exc).__name__}: {exc}",
            },
            [],
        )

    series, meta = align_benchmark_to_minute(raw, grid)
    curve: list[dict[str, Any]] = []
    base: float | None = None
    for ts, value in series.items():
        if pd.isna(value):
            continue
        if base is None and float(value) != 0:
            base = float(value)
        curve.append(
            {
                "datetime": pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                "value_raw": float(value),
                "value_norm": (
                    round(float(value) / base, 6) if base not in (None, 0) else None
                ),
            }
        )
    return (
        {
            "code": benchmark,
            "resolution": meta.get("resolution"),
            "lookahead": meta.get("lookahead"),
            "note": meta.get("note"),
            "points": len(curve),
        },
        curve,
    )


def align_factor_to_minute(
    factor_frame: Optional[pd.DataFrame],
    minute_index,
    value_col: str = "close",
) -> pd.Series:
    """因子对齐到分钟 bar 的便捷入口：``session_close`` 口径，无前视。

    因子值在第 D 个交易日收盘后才可用，因此只作用于 D 之后的分钟 bar。需要「当日即
    用」口径（含前视，仅展示）时直接用 ``alignment.align_daily_to_minute``。
    """
    return align_daily_to_minute(
        factor_frame, minute_index, value_col=value_col, available_from="session_close"
    )


def estimate_fetch_bars(period: str | int, start_date, end_date) -> int:
    """供调用方预估取数规模（根数），用于评估取数耗时。"""
    return estimate_bar_count(
        pd.Timestamp(start_date).date(),
        pd.Timestamp(end_date).date(),
        normalize_period(period),
    )
