"""绩效指标计算"""

import math


def calc_summary(equity_curve: list[dict], trades: list[dict], initial_cash: float) -> dict:
    if not equity_curve:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "profit_loss_ratio": 0.0,
            "turnover": 0.0,
            "total_trades": 0,
        }

    start_value = equity_curve[0]["equity"]
    end_value = equity_curve[-1]["equity"]
    total_return = (end_value - start_value) / start_value if start_value else 0.0

    daily_returns = []
    for i in range(1, len(equity_curve)):
        prev_val = equity_curve[i - 1]["equity"]
        curr_val = equity_curve[i]["equity"]
        if prev_val > 0:
            daily_returns.append((curr_val - prev_val) / prev_val)

    if daily_returns:
        avg = sum(daily_returns) / len(daily_returns)
        variance = sum((r - avg) ** 2 for r in daily_returns) / len(daily_returns)
        std = math.sqrt(variance)
        sharpe = (avg / std) * math.sqrt(252) if std > 1e-12 else 0.0
    else:
        sharpe = 0.0

    n_periods = max(1, len(equity_curve) - 1)
    annual_return = (1 + total_return) ** (252 / n_periods) - 1 if n_periods > 0 else 0.0

    peak = equity_curve[0]["equity"]
    max_drawdown = 0.0
    for p in equity_curve:
        peak = max(peak, p["equity"])
        if peak > 0:
            drawdown = (peak - p["equity"]) / peak
            max_drawdown = max(max_drawdown, drawdown)

    closed_trades = [t for t in trades if t.get("side") == "sell"]
    wins = [t for t in closed_trades if t.get("pnl", 0) > 0]
    losses = [t for t in closed_trades if t.get("pnl", 0) < 0]
    win_rate = len(wins) / len(closed_trades) if closed_trades else 0.0

    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.0
    profit_loss_ratio = (avg_win / avg_loss) if avg_loss > 1e-12 else 0.0

    turnover = sum(abs(t.get("amount", 0.0)) for t in trades) / initial_cash if initial_cash > 0 else 0.0

    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "max_drawdown": round(max_drawdown, 6),
        "sharpe": round(sharpe, 6),
        "win_rate": round(win_rate, 6),
        "profit_loss_ratio": round(profit_loss_ratio, 6),
        "turnover": round(turnover, 6),
        "total_trades": len(trades),
    }
