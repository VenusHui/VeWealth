"""Policy 与 Profile 注册表（P0 可运行版）"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.services.backtest.policies.base import (
    AllocationPolicy,
    ExecutionPolicy,
    PolicyContext,
    RankingPolicy,
    RiskPolicy,
    SelectionPolicy,
)
from app.services.backtest.policies.profiles import POLICY_PROFILES, PolicyProfile
from app.services.evaluator import TIEBREAK_ASCENDING, TIEBREAK_COLUMNS


class SignalThenLiquidityRanking(RankingPolicy):
    policy_id = "signal_then_liquidity_v1"

    def rank(self, candidates_df: pd.DataFrame, context: PolicyContext) -> pd.DataFrame:
        if candidates_df.empty:
            return candidates_df

        ranked = candidates_df.copy()
        if "signal_strength" not in ranked.columns:
            ranked["signal_strength"] = 0.0
        if "liquidity" not in ranked.columns:
            ranked["liquidity"] = 0.0
        if "symbol" not in ranked.columns:
            ranked["symbol"] = ""

        # 先按 trade_date 升序（回测需逐交易日推进），再按 score → liquidity → symbol
        # 的同分 tie-break。第二段排序直接复用共享评估器的 TIEBREAK_* 常量，保证与选股端
        # rank_candidates 的同分语义完全一致（单一事实来源）。
        ranked = ranked.sort_values(
            by=["trade_date", *TIEBREAK_COLUMNS],
            ascending=[True, *TIEBREAK_ASCENDING],
        ).reset_index(drop=True)
        return ranked


class TopKSelection(SelectionPolicy):
    policy_id = "top_k_v1"

    def select(
        self,
        ranked_df: pd.DataFrame,
        portfolio_state: dict[str, Any],
        context: PolicyContext,
    ) -> pd.DataFrame:
        if ranked_df.empty:
            return ranked_df

        top_k = int(context.params.get("top_k_per_day", 1) or 1)
        if top_k <= 0:
            top_k = 1

        selected = (
            ranked_df.groupby("trade_date", as_index=False, group_keys=False)
            .head(top_k)
            .reset_index(drop=True)
        )
        return selected


class EqualWeightAllocation(AllocationPolicy):
    policy_id = "equal_weight_v1"

    def allocate(
        self,
        selected_df: pd.DataFrame,
        equity: float,
        risk_state: dict[str, Any],
        context: PolicyContext,
    ) -> pd.DataFrame:
        if selected_df.empty:
            return selected_df

        allocated = selected_df.copy()
        position_size_pct = float(context.params.get("position_size_pct", 0.1) or 0.1)
        position_size_pct = max(0.0, min(1.0, position_size_pct))
        allocated["position_size_pct"] = position_size_pct
        allocated["alloc_amount"] = equity * position_size_pct
        return allocated


class CnABasicRisk(RiskPolicy):
    policy_id = "cn_a_basic_risk_v1"

    def check_pre_trade(
        self,
        orders_df: pd.DataFrame,
        portfolio_state: dict[str, Any],
        context: PolicyContext,
    ) -> pd.DataFrame:
        # P0: 风控占位，后续扩展涨跌停、黑名单、行业暴露等规则
        return orders_df


class CnAT1OpenFill(ExecutionPolicy):
    policy_id = "cn_a_t1_open_fill_v1"

    def simulate_fill(
        self,
        orders_df: pd.DataFrame,
        bar_df: pd.DataFrame,
        cost_model: Any,
        context: PolicyContext,
    ) -> list[dict[str, Any]]:
        if orders_df.empty:
            return []

        hold_days = int(context.params.get("hold_days", 5) or 5)
        if hold_days < 1:
            hold_days = 1

        market_data_map: dict[str, pd.DataFrame] = (
            context.extras.get("market_data_map") or {}
        )
        events: list[dict[str, Any]] = []

        for _, row in orders_df.sort_values(by=["trade_date", "symbol"]).iterrows():
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue

            symbol_df = market_data_map.get(symbol)
            if symbol_df is None or symbol_df.empty:
                continue

            work = symbol_df.copy().reset_index(drop=True)
            if "datetime" not in work.columns or "open" not in work.columns:
                continue

            work["trade_date"] = pd.to_datetime(work["datetime"]).dt.normalize()
            target_date = pd.to_datetime(row.get("trade_date")).normalize()

            matched = work.index[work["trade_date"] == target_date].tolist()
            if not matched:
                continue

            buy_idx = matched[0]
            sell_idx = buy_idx + hold_days
            if sell_idx >= len(work):
                continue

            buy_row = work.iloc[buy_idx]
            sell_row = work.iloc[sell_idx]
            buy_price = float(buy_row.get("open") or 0)
            sell_price = float(sell_row.get("open") or 0)
            if buy_price <= 0:
                continue

            ret = (sell_price - buy_price) / buy_price
            events.append(
                {
                    "symbol": symbol,
                    "buy_datetime": pd.to_datetime(buy_row["datetime"]).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "sell_datetime": pd.to_datetime(sell_row["datetime"]).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "buy_price": round(buy_price, 4),
                    "sell_price": round(sell_price, 4),
                    "return": round(ret, 6),
                    "reason": row.get("reason") or "policy_select",
                    "position_size_pct": float(
                        row.get("position_size_pct")
                        or context.params.get("position_size_pct", 0.1)
                        or 0.1
                    ),
                }
            )

        return events


POLICY_REGISTRY: dict[str, type] = {
    SignalThenLiquidityRanking.policy_id: SignalThenLiquidityRanking,
    TopKSelection.policy_id: TopKSelection,
    EqualWeightAllocation.policy_id: EqualWeightAllocation,
    CnABasicRisk.policy_id: CnABasicRisk,
    CnAT1OpenFill.policy_id: CnAT1OpenFill,
}


def get_policy(policy_id: str) -> Any:
    cls = POLICY_REGISTRY.get(policy_id)
    if not cls:
        raise ValueError(f"未知 policy: {policy_id}")
    return cls()


def get_profile(profile_id: str) -> PolicyProfile:
    profile = POLICY_PROFILES.get(profile_id)
    if not profile:
        raise ValueError(f"未知 policy profile: {profile_id}")
    return profile


def resolve_profile(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    return {
        "profile": profile,
        "ranking": get_policy(profile.ranking_policy),
        "selection": get_policy(profile.selection_policy),
        "allocation": get_policy(profile.allocation_policy),
        "risk": get_policy(profile.risk_policy),
        "execution": get_policy(profile.execution_policy),
    }
