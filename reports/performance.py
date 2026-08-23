"""
reports/performance.py
=======================
Computes win rate, average P&L, max drawdown, profit factor, risk/reward,
and formats the strategy comparison table + daily Telegram report text.
"""

import pandas as pd


def compute_metrics(trades: list, equity_curve: list, starting_capital: float) -> dict:
    if not trades:
        return {
            "num_trades": 0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": None, "max_drawdown": 0.0, "net_pnl": 0.0,
            "gross_pnl": 0.0, "total_costs": 0.0, "risk_reward": None,
            "best_trade": 0.0, "worst_trade": 0.0,
        }

    df = pd.DataFrame(trades)
    wins = df[df["net_pnl"] > 0]
    losses = df[df["net_pnl"] <= 0]

    win_rate = len(wins) / len(df) * 100
    avg_win = wins["net_pnl"].mean() if len(wins) else 0.0
    avg_loss = losses["net_pnl"].mean() if len(losses) else 0.0

    gross_profit = wins["net_pnl"].sum() if len(wins) else 0.0
    gross_loss = abs(losses["net_pnl"].sum()) if len(losses) else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (None if gross_profit == 0 else float("inf"))

    risk_reward = abs(avg_win / avg_loss) if avg_loss != 0 else None

    max_dd = max((pt["drawdown"] for pt in equity_curve), default=0.0)

    return {
        "num_trades": len(df),
        "win_rate": round(win_rate, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if isinstance(profit_factor, float) and profit_factor != float("inf") else profit_factor,
        "max_drawdown": round(max_dd, 2),
        "net_pnl": round(df["net_pnl"].sum(), 2),
        "gross_pnl": round(df["gross_pnl"].sum(), 2),
        "total_costs": round((df["charges"] + df["slippage_cost"]).sum(), 2),
        "risk_reward": round(risk_reward, 2) if risk_reward is not None else None,
        "best_trade": round(df["net_pnl"].max(), 2),
        "worst_trade": round(df["net_pnl"].min(), 2),
    }


def build_comparison_table(results_by_key: dict) -> str:
    """
    results_by_key: { (strategy, index, timeframe): metrics_dict }
    Returns a markdown table string matching the required format.
    NOTE: this is for the GitHub Actions LOG (which renders markdown fine).
    For Telegram, use build_telegram_summary() instead -- Telegram does not
    render markdown pipe-tables, so this format looks like garbled text there.
    """
    header = "| Strategy | Index | Timeframe | Trades | Win Rate | Profit Factor | Net P&L | Max DD |\n"
    header += "|----------|-------|-----------|-------:|---------:|---------------:|--------:|-------:|\n"
    rows = []
    for (strategy, index_name, tf), m in results_by_key.items():
        pf = m["profit_factor"]
        pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) else "N/A"
        rows.append(
            f"| {strategy} | {index_name} | {tf}m | {m['num_trades']} | "
            f"{m['win_rate']:.1f}% | {pf_str} | Rs{m['net_pnl']:.0f} | Rs{m['max_drawdown']:.0f} |"
        )
    return header + "\n".join(rows)


def build_telegram_summary(results_by_key: dict, max_rows: int = 15) -> str:
    """
    Compact, plain-text summary suitable for Telegram (no markdown tables,
    since Telegram doesn't render them -- they show up as raw '|' text).

    Shows: total combinations tested, how many had zero trades vs some
    trades, and up to `max_rows` of the combinations that actually
    produced trades (sorted by net P&L, best first). If NOTHING produced
    a trade, says so plainly instead of dumping every zero row.
    """
    total = len(results_by_key)
    with_trades = {k: m for k, m in results_by_key.items() if m["num_trades"] > 0}
    zero_trades = total - len(with_trades)

    lines = [f"Tested {total} combinations (strategy x index x timeframe x EMA)."]
    lines.append(f"{len(with_trades)} had at least one trade, {zero_trades} had zero trades.")
    lines.append("")

    if not with_trades:
        lines.append(
            "No combination produced any trade at all. This usually means the "
            "signal-quality filters (or regime filter) were never satisfied in "
            "this data window -- check the GitHub Actions log for the full table, "
            "or loosen config.MIN_SIGNAL_SCORE / test a longer data window."
        )
        return "\n".join(lines)

    sorted_items = sorted(with_trades.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True)
    lines.append(f"Top {min(max_rows, len(sorted_items))} by net P&L:")
    for (strategy, index_name, tf), m in sorted_items[:max_rows]:
        pf = m["profit_factor"]
        pf_str = f"{pf:.2f}" if isinstance(pf, (int, float)) else "N/A"
        lines.append(
            f"• {strategy} | {index_name} {tf}m — {m['num_trades']} trades, "
            f"{m['win_rate']:.0f}% win, PF {pf_str}, net Rs{m['net_pnl']:.0f}"
        )

    if len(sorted_items) > max_rows:
        lines.append(f"... and {len(sorted_items) - max_rows} more (see GitHub Actions log for the full table).")

    return "\n".join(lines)


def is_robust(metrics_in_sample: dict, metrics_out_of_sample: dict,
              min_trades: int = 15, min_profit_factor: float = 1.2) -> tuple:
    """
    A strategy is only accepted if it is profitable AFTER costs both
    in-sample and out-of-sample, with a reasonable sample size. Returns
    (is_robust: bool, reason: str).
    """
    if metrics_in_sample["num_trades"] < min_trades or metrics_out_of_sample["num_trades"] < 5:
        return False, "Insufficient sample size for a reliable conclusion"

    is_pf = metrics_in_sample["profit_factor"]
    oos_pf = metrics_out_of_sample["profit_factor"]

    if is_pf is None or oos_pf is None:
        return False, "Profit factor undefined (no losing trades to compare against, or no data)"

    if oos_pf < min_profit_factor:
        return False, f"Out-of-sample profit factor {oos_pf} below minimum {min_profit_factor}"

    if metrics_out_of_sample["net_pnl"] <= 0:
        return False, "Out-of-sample net P&L is not positive after costs"

    # Reject if in-sample looks great but out-of-sample collapses (overfitting signal)
    if is_pf > 0 and oos_pf < is_pf * 0.5:
        return False, "Out-of-sample performance degraded >50% vs in-sample -- likely overfit"

    return True, "Passed robustness checks"


def format_period_report(period_label: str, starting_capital: float, ending_capital: float, trades: list,
                          max_drawdown: float, strategy_pnls: dict) -> str:
    """period_label: 'DAILY', 'WEEKLY', or 'MONTHLY' -- controls the header only."""
    df = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["net_pnl", "gross_pnl", "charges", "slippage_cost"])
    wins = df[df["net_pnl"] > 0] if len(df) else df
    losses = df[df["net_pnl"] <= 0] if len(df) else df

    win_rate = (len(wins) / len(df) * 100) if len(df) else 0.0
    gross_pnl = df["gross_pnl"].sum() if len(df) else 0.0
    total_costs = (df["charges"].sum() + df["slippage_cost"].sum()) if len(df) else 0.0
    net_pnl = df["net_pnl"].sum() if len(df) else 0.0
    best = df["net_pnl"].max() if len(df) else 0.0
    worst = df["net_pnl"].min() if len(df) else 0.0

    strategy_lines = "\n".join(
        f"{i+1}. {name}: Rs{pnl:.0f}" for i, (name, pnl) in enumerate(strategy_pnls.items())
    )

    return f"""📈 {period_label} PAPER TRADING REPORT
[PAPER TRADE — NOT REAL MONEY]

Starting Capital: Rs{starting_capital:,.0f}
Ending Capital: Rs{ending_capital:,.0f}

Trades: {len(df)}
Winning Trades: {len(wins)}
Losing Trades: {len(losses)}

Win Rate: {win_rate:.1f}%

Gross P&L: Rs{gross_pnl:.0f}
Total Costs: Rs{total_costs:.0f}
Net P&L: Rs{net_pnl:.0f}

Max Drawdown: Rs{max_drawdown:.0f}

Best Trade: Rs{best:.0f}
Worst Trade: Rs{worst:.0f}

Strategy Performance:
{strategy_lines if strategy_lines else "(no trades this period)"}
"""


def format_daily_report(starting_capital: float, ending_capital: float, trades: list,
                         max_drawdown: float, strategy_pnls: dict) -> str:
    """Kept for backward compatibility -- just calls format_period_report with 'DAILY'."""
    return format_period_report("DAILY", starting_capital, ending_capital, trades, max_drawdown, strategy_pnls)
