"""
reports/dashboard.py
======================
Generates reports/dashboard.xlsx from reports/trade_log.csv -- a
professional-style performance dashboard with two sheets:

1. "Monthly Returns" -- a Year x Month grid of % return, colored
   green/red, with a yearly total column (the same style as a typical
   strategy-tester monthly-returns table).
2. "Statistics" -- trade count, win/loss breakdown, averages, largest
   win/loss, max consecutive wins/losses, max drawdown ($ and %), profit
   factor, payoff ratio, Sharpe ratio, and recovery factor.

Regenerated every time the bot runs (called from main.py after the daily
report) and committed back to the repo by the GitHub Actions workflow
alongside trade_log.csv, so it's always in sync with the latest trades --
no manual steps needed.

Needs at least a few trades to be meaningful; with very few trades, most
of the monthly grid will just be blank and the statistics will carry the
same "too small a sample" caveat as everywhere else in this codebase.
"""

import csv
import os
from collections import defaultdict
from datetime import datetime

import config

_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.csv")
_DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "dashboard.xlsx")

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _read_trades(path: str):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["_net"] = float(r["Net P&L"]) if r.get("Net P&L") not in (None, "") else 0.0
        try:
            r["_date"] = datetime.strptime(r["Date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            r["_date"] = None
    return [r for r in rows if r["_date"] is not None]


def _daily_pnl(trades):
    """{date: net_pnl_sum} across all trades that day, sorted by date."""
    by_day = defaultdict(float)
    for t in trades:
        by_day[t["_date"]] += t["_net"]
    return dict(sorted(by_day.items()))


def _monthly_returns_pct(daily_pnl: dict, starting_capital: float):
    """Returns {year: {month: pct_return}} using an equity curve that
    carries forward day to day (so a month's % return is relative to the
    capital level at the START of that month, compounding -- same
    convention as the example screenshot)."""
    capital = starting_capital
    month_start_capital = {}
    month_pnl = defaultdict(float)
    for d, pnl in daily_pnl.items():
        key = (d.year, d.month)
        if key not in month_start_capital:
            month_start_capital[key] = capital
        month_pnl[key] += pnl
        capital += pnl

    grid = defaultdict(dict)
    for (year, month), pnl in month_pnl.items():
        start_cap = month_start_capital[(year, month)]
        grid[year][month] = (pnl / start_cap * 100) if start_cap else 0.0
    return grid, capital


def _max_consecutive(results: list, target: bool) -> int:
    """results: list of bool (True=win). Longest run of `target`."""
    best = cur = 0
    for r in results:
        if r == target:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _max_drawdown(daily_pnl: dict, starting_capital: float):
    """Returns (max_dd_rupees, max_dd_pct) off the running equity curve."""
    capital = starting_capital
    peak = starting_capital
    max_dd_rs, max_dd_pct = 0.0, 0.0
    for _, pnl in daily_pnl.items():
        capital += pnl
        peak = max(peak, capital)
        dd_rs = peak - capital
        dd_pct = (dd_rs / peak * 100) if peak else 0.0
        max_dd_rs = max(max_dd_rs, dd_rs)
        max_dd_pct = max(max_dd_pct, dd_pct)
    return max_dd_rs, max_dd_pct


def _sharpe(daily_returns_pct: list) -> float:
    """Annualized Sharpe on daily %returns, 0% risk-free rate, ~252
    trading days/year. Returns None if fewer than 2 data points."""
    if len(daily_returns_pct) < 2:
        return None
    import statistics
    mean = statistics.mean(daily_returns_pct)
    stdev = statistics.pstdev(daily_returns_pct)
    if stdev == 0:
        return None
    return round((mean / stdev) * (252 ** 0.5), 2)


def generate_dashboard(trade_log_path: str = None, out_path: str = None, starting_capital: float = None):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import ColorScaleRule

    trade_log_path = trade_log_path or _TRADE_LOG_PATH
    out_path = out_path or _DASHBOARD_PATH
    starting_capital = starting_capital or config.STARTING_CAPITAL

    trades = _read_trades(trade_log_path)

    ARIAL = "Arial"
    HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    HEADER_FONT = Font(name=ARIAL, bold=True, color="FFFFFF")
    THIN = Side(style="thin", color="B7B7B7")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    GREEN = Font(name=ARIAL, color="0B8A00")
    RED = Font(name=ARIAL, color="C00000")

    wb = openpyxl.Workbook()

    # ---------------- Sheet 1: Monthly Returns ----------------
    ws1 = wb.active
    ws1.title = "Monthly Returns"
    ws1["A1"] = "Monthly Returns (%) -- PAPER TRADE, NOT REAL MONEY"
    ws1["A1"].font = Font(name=ARIAL, bold=True, size=13)

    daily_pnl = _daily_pnl(trades)
    if not daily_pnl:
        ws1["A3"] = "No trades yet -- this fills in automatically as the bot trades."
        ws1["A3"].font = Font(name=ARIAL, italic=True)
    else:
        grid, _ = _monthly_returns_pct(daily_pnl, starting_capital)
        header_row = 3
        ws1.cell(row=header_row, column=1, value="Year")
        for m in range(12):
            ws1.cell(row=header_row, column=2 + m, value=MONTH_NAMES[m])
        ws1.cell(row=header_row, column=14, value="Year %")
        for c in range(1, 15):
            ws1.cell(row=header_row, column=c).fill = HEADER_FILL
            ws1.cell(row=header_row, column=c).font = HEADER_FONT
            ws1.cell(row=header_row, column=c).alignment = Alignment(horizontal="center")
            ws1.cell(row=header_row, column=c).border = BORDER

        for i, year in enumerate(sorted(grid.keys())):
            r = header_row + 1 + i
            ws1.cell(row=r, column=1, value=year).font = Font(name=ARIAL, bold=True)
            year_total = 1.0
            for m in range(1, 13):
                pct = grid[year].get(m)
                cell = ws1.cell(row=r, column=1 + m)
                cell.border = BORDER
                if pct is not None:
                    cell.value = round(pct, 1) / 100
                    cell.number_format = "0.0%"
                    cell.font = GREEN if pct >= 0 else RED
                    year_total *= (1 + pct / 100)
            yr_cell = ws1.cell(row=r, column=14, value=year_total - 1)
            yr_cell.number_format = "0.0%"
            yr_cell.font = Font(name=ARIAL, bold=True)
            yr_cell.border = BORDER
            ws1.cell(row=r, column=1).border = BORDER

        widths = [8] + [8] * 12 + [10]
        for i, w in enumerate(widths, start=1):
            ws1.column_dimensions[get_column_letter(i)].width = w

    # ---------------- Sheet 2: Statistics ----------------
    ws2 = wb.create_sheet("Statistics")
    ws2["A1"] = "Trade Statistics -- PAPER TRADE, NOT REAL MONEY"
    ws2["A1"].font = Font(name=ARIAL, bold=True, size=13)

    if not trades:
        ws2["A3"] = "No trades yet."
        ws2["A3"].font = Font(name=ARIAL, italic=True)
    else:
        nets = [t["_net"] for t in trades]
        wins = [n for n in nets if n > 0]
        losses = [n for n in nets if n <= 0]
        results_bool = [n > 0 for n in nets]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0)
        payoff_ratio = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else None
        max_dd_rs, max_dd_pct = _max_drawdown(daily_pnl, starting_capital)
        net_total = sum(nets)
        recovery_factor = (net_total / max_dd_rs) if max_dd_rs > 0 else None

        daily_returns_pct = []
        cap = starting_capital
        for _, pnl in daily_pnl.items():
            daily_returns_pct.append(pnl / cap * 100 if cap else 0.0)
            cap += pnl
        sharpe = _sharpe(daily_returns_pct)

        rows = [
            ("All trades", len(trades)),
            ("Winners", f"{len(wins)} ({len(wins)/len(trades)*100:.1f}%)"),
            ("Losers", f"{len(losses)} ({len(losses)/len(trades)*100:.1f}%)"),
            ("Avg Win (Rs)", round(sum(wins)/len(wins), 2) if wins else 0),
            ("Avg Loss (Rs)", round(sum(losses)/len(losses), 2) if losses else 0),
            ("Largest Win (Rs)", round(max(nets), 2)),
            ("Largest Loss (Rs)", round(min(nets), 2)),
            ("Max Consecutive Wins", _max_consecutive(results_bool, True)),
            ("Max Consecutive Losses", _max_consecutive(results_bool, False)),
            ("Net P&L (Rs)", round(net_total, 2)),
            ("Max Drawdown (Rs)", round(max_dd_rs, 2)),
            ("Max Drawdown (%)", f"{max_dd_pct:.2f}%"),
            ("Profit Factor", round(profit_factor, 2) if profit_factor != float("inf") else "inf"),
            ("Payoff Ratio", round(payoff_ratio, 2) if payoff_ratio is not None else "N/A"),
            ("Recovery Factor", round(recovery_factor, 2) if recovery_factor is not None else "N/A"),
            ("Sharpe Ratio (annualized)", sharpe if sharpe is not None else "N/A (need 2+ trading days)"),
        ]
        r0 = 3
        for i, (label, val) in enumerate(rows):
            lr = r0 + i
            ws2.cell(row=lr, column=1, value=label).font = Font(name=ARIAL, bold=True)
            vcell = ws2.cell(row=lr, column=2, value=val)
            vcell.font = Font(name=ARIAL)
            if isinstance(val, (int, float)) and label in ("Net P&L (Rs)", "Avg Win (Rs)", "Largest Win (Rs)"):
                vcell.font = GREEN
            elif isinstance(val, (int, float)) and label in ("Avg Loss (Rs)", "Largest Loss (Rs)", "Max Drawdown (Rs)"):
                vcell.font = RED
            ws2.cell(row=lr, column=1).border = BORDER
            ws2.cell(row=lr, column=2).border = BORDER

        note_row = r0 + len(rows) + 2
        ws2.cell(row=note_row, column=1,
            value="Caveat: with only a handful of trades, these numbers (especially Sharpe, "
                  "profit factor, drawdown %) are not statistically reliable yet. Treat this as "
                  "a live-updating dashboard to watch over weeks, not a verdict today.")
        ws2.cell(row=note_row, column=1).font = Font(name=ARIAL, italic=True, size=9, color="666666")

        ws2.column_dimensions["A"].width = 26
        ws2.column_dimensions["B"].width = 22

    wb.save(out_path)
    return out_path


if __name__ == "__main__":
    path = generate_dashboard()
    print(f"Dashboard written to {path}")
