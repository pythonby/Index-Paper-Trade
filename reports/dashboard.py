"""
reports/dashboard.py
======================
Generates reports/dashboard.xlsx from reports/trade_log.csv, SPLIT BY INDEX:

    Summary        one row per index (NIFTY / BANKNIFTY / FINNIFTY / ALL)
    NIFTY          monthly-returns grid + full statistics box, NIFTY trades only
    BANKNIFTY      same, BANKNIFTY trades only
    FINNIFTY       same, FINNIFTY trades only
    ALL INDICES    same, everything combined

Each index sheet has (1) a Year x Month % return grid (green/red, with a
yearly total) and (2) a statistics box: trades, winners/losers, averages,
largest win/loss, consecutive wins/losses, drawdown, profit factor, payoff
ratio, recovery factor, Sharpe, transaction costs.

Regenerated every time the bot runs (main.py -> after the daily report) and
committed back to the repo by the GitHub Actions workflow. Needs many
trades over many weeks to fill the grid; with a handful of trades the
numbers are NOT statistically reliable, and the file says so.
"""

import csv
import os
import statistics
from collections import defaultdict
from datetime import datetime

import config

_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.csv")
_DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "dashboard.xlsx")
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ----------------------------------------------------------------- reading
def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _read_trades(path: str):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        try:
            r["_date"] = datetime.strptime(r["Date"], "%Y-%m-%d").date()
        except (ValueError, KeyError, TypeError):
            continue
        r["_net"] = _f(r.get("Net P&L"))
        r["_costs"] = _f(r.get("Costs"))
        r["_capital_used"] = _f(r.get("Entry")) * _f(r.get("Qty"))
        r["_pct"] = (r["_net"] / r["_capital_used"] * 100) if r["_capital_used"] else 0.0
        r["_sort"] = (r["_date"], r.get("Time", ""))
        out.append(r)
    out.sort(key=lambda r: r["_sort"])
    return out


# ------------------------------------------------------------------- maths
def _daily_pnl(trades):
    d = defaultdict(float)
    for t in trades:
        d[t["_date"]] += t["_net"]
    return dict(sorted(d.items()))


def _monthly_grid(daily_pnl, start_cap):
    """{year: {month: pct}} -- each month's P&L / capital at that month's start."""
    cap = start_cap
    month_start, month_pnl = {}, defaultdict(float)
    for d, pnl in daily_pnl.items():
        k = (d.year, d.month)
        month_start.setdefault(k, cap)
        month_pnl[k] += pnl
        cap += pnl
    grid = defaultdict(dict)
    for (y, m), pnl in month_pnl.items():
        grid[y][m] = (pnl / month_start[(y, m)] * 100) if month_start[(y, m)] else 0.0
    return grid


def _max_run(flags, target):
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f == target else 0
        best = max(best, cur)
    return best


def _drawdown(trades, start_cap):
    """Trade-by-trade equity curve -> (max_dd_rs, max_dd_pct)."""
    cap = peak = start_cap
    dd_rs = dd_pct = 0.0
    for t in trades:
        cap += t["_net"]
        peak = max(peak, cap)
        cur = peak - cap
        dd_rs = max(dd_rs, cur)
        dd_pct = max(dd_pct, cur / peak * 100 if peak else 0.0)
    return dd_rs, dd_pct


def _sharpe(daily_pnl, start_cap):
    cap, rets = start_cap, []
    for pnl in daily_pnl.values():
        rets.append(pnl / cap * 100 if cap else 0.0)
        cap += pnl
    if len(rets) < 2:
        return None
    sd = statistics.pstdev(rets)
    return round(statistics.mean(rets) / sd * (252 ** 0.5), 2) if sd else None


def compute_stats(trades, start_cap):
    """Returns an ordered list of (label, value, fmt, tone). fmt: 'int', 'money',
    'pct', 'ratio', 'text'.  tone: 'good' | 'bad' | None (colors the value)."""
    if not trades:
        return []
    nets = [t["_net"] for t in trades]
    wins = [t for t in trades if t["_net"] > 0]
    losses = [t for t in trades if t["_net"] <= 0]
    win_pnl = sum(t["_net"] for t in wins)
    loss_pnl = sum(t["_net"] for t in losses)          # negative number
    dpnl = _daily_pnl(trades)
    dd_rs, dd_pct = _drawdown(trades, start_cap)
    net = sum(nets)
    pf = (win_pnl / abs(loss_pnl)) if loss_pnl else (None if not win_pnl else float("inf"))
    payoff = ((win_pnl / len(wins)) / abs(loss_pnl / len(losses))) if wins and losses and loss_pnl else None
    recov = (net / dd_rs) if dd_rs > 0 else None
    flags = [t["_net"] > 0 for t in trades]

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0.0

    return [
        ("All trades", len(trades), "int", None),
        ("Winners", f"{len(wins)} ({len(wins) / len(trades) * 100:.1f}%)", "text", None),
        ("Losers", f"{len(losses)} ({len(losses) / len(trades) * 100:.1f}%)", "text", None),
        ("Net P&L (Rs)", round(net, 2), "money", "good" if net >= 0 else "bad"),
        ("Total Profit (Rs)", round(win_pnl, 2), "money", "good"),
        ("Total Loss (Rs)", round(loss_pnl, 2), "money", "bad"),
        ("Transaction costs (Rs)", round(sum(t["_costs"] for t in trades), 2), "money", None),
        ("Avg Profit/Loss per trade (Rs)", round(net / len(trades), 2), "money", "good" if net >= 0 else "bad"),
        ("Avg Profit/Loss per trade (% of premium)", round(avg([t["_pct"] for t in trades]), 2), "pct", None),
        ("Avg Win (Rs)", round(avg([t["_net"] for t in wins]), 2), "money", "good"),
        ("Avg Win (% of premium)", round(avg([t["_pct"] for t in wins]), 2), "pct", "good"),
        ("Avg Loss (Rs)", round(avg([t["_net"] for t in losses]), 2), "money", "bad"),
        ("Avg Loss (% of premium)", round(avg([t["_pct"] for t in losses]), 2), "pct", "bad"),
        ("Largest Win (Rs)", round(max(nets), 2), "money", "good"),
        ("Largest Loss (Rs)", round(min(nets), 2), "money", "bad"),
        ("Max Consecutive Wins", _max_run(flags, True), "int", None),
        ("Max Consecutive Losses", _max_run(flags, False), "int", None),
        ("Max trade drawdown (Rs)", round(min(nets), 2) if min(nets) < 0 else 0, "money", "bad"),
        ("Max system drawdown (Rs)", round(-dd_rs, 2), "money", "bad"),
        ("Max system drawdown (%)", round(-dd_pct, 2), "pct", "bad"),
        ("Recovery Factor", round(recov, 2) if recov is not None else "N/A", "ratio", None),
        ("Profit Factor", ("inf" if pf == float("inf") else round(pf, 2)) if pf is not None else "N/A", "ratio", None),
        ("Payoff Ratio (avg win / avg loss)", round(payoff, 2) if payoff is not None else "N/A", "ratio", None),
        ("Sharpe Ratio (annualized)", _sharpe(dpnl, start_cap) if _sharpe(dpnl, start_cap) is not None
         else "N/A (needs 2+ trading days)", "ratio", None),
    ]


# ------------------------------------------------------------------ writing
def _styles():
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    thin = Side(style="thin", color="B7B7B7")
    return {
        "hfill": PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid"),
        "hfont": Font(name="Arial", bold=True, color="FFFFFF"),
        "bold": Font(name="Arial", bold=True),
        "norm": Font(name="Arial"),
        "good": Font(name="Arial", color="0B8A00"),
        "bad": Font(name="Arial", color="C00000"),
        "note": Font(name="Arial", italic=True, size=9, color="666666"),
        "title": Font(name="Arial", bold=True, size=13),
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
        "center": Alignment(horizontal="center"),
    }


def _period_data(daily_pnl, start_cap):
    """Builds daily / weekly / monthly P&L buckets off one running equity curve.
    Every bucket keeps (pnl_rs, capital_at_its_start) so return % = pnl / capital."""
    from datetime import timedelta
    cap_before, cap = {}, start_cap
    for d, p in daily_pnl.items():
        cap_before[d] = cap
        cap += p
    daily = {d: (p, cap_before[d]) for d, p in daily_pnl.items()}

    def bucket(keyfn):
        out = {}
        for d, p in daily_pnl.items():
            k = keyfn(d)
            if k in out:
                out[k][0] += p
            else:
                out[k] = [p, cap_before[d]]
        return out

    monday = lambda d: d - timedelta(days=d.weekday())
    return {
        "daily": daily,                                              # date -> (pnl, cap)
        "week": bucket(monday),                                      # monday -> [pnl, cap]  (full Mon-Fri week)
        "week_in_month": bucket(lambda d: (d.year, d.month, monday(d))),  # weeks split at month edges
        "month": bucket(lambda d: (d.year, d.month)),
    }


def _write_index_sheet(ws, title, trades, start_cap, S):
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import PatternFill, Alignment
    GREEN_BG = PatternFill(start_color="E6F4EA", end_color="E6F4EA", fill_type="solid")
    RED_BG = PatternFill(start_color="FDE8E8", end_color="FDE8E8", fill_type="solid")

    ws["A1"] = f"{title} -- PAPER TRADE, NOT REAL MONEY"
    ws["A1"].font = S["title"]
    ws.column_dimensions["A"].width = 40
    for i in range(2, 16):
        ws.column_dimensions[get_column_letter(i)].width = 10
    for col in ("H", "I", "O"):
        ws.column_dimensions[col].width = 15   # "Month P&L (Rs)" / "Week P&L (Rs)" headers

    if not trades:
        ws["A3"] = "No trades yet for this index -- this fills in automatically as the bot trades."
        ws["A3"].font = S["note"]
        return

    def head(row, labels):
        for c, h in enumerate(labels, start=1):
            cell = ws.cell(row=row, column=c, value=h)
            cell.fill, cell.font, cell.alignment, cell.border = S["hfill"], S["hfont"], S["center"], S["border"]

    def put_pct(row, col, pnl, cap, bold=False):
        cell = ws.cell(row=row, column=col)
        cell.border = S["border"]
        if pnl is None:
            return
        pct = pnl / cap if cap else 0.0
        cell.value = pct
        cell.number_format = "0.0%"
        cell.font = S["good"] if pct >= 0 else S["bad"]
        cell.fill = GREEN_BG if pct >= 0 else RED_BG
        if bold:
            from openpyxl.styles import Font
            cell.font = Font(name="Arial", bold=True, color="0B8A00" if pct >= 0 else "C00000")

    def put_rs(row, col, pnl):
        cell = ws.cell(row=row, column=col)
        cell.border = S["border"]
        if pnl is None:
            return
        cell.value = round(pnl, 2)
        cell.number_format = "#,##0;-#,##0"
        cell.font = S["good"] if pnl >= 0 else S["bad"]

    def label(row, text):
        c = ws.cell(row=row, column=1, value=text)
        c.font, c.border = S["bold"], S["border"]

    pd_ = _period_data(_daily_pnl(trades), start_cap)
    r = 3

    # ================= MONTHLY (Year x Month) =================
    ws.cell(row=r, column=1, value="MONTHLY returns (% of capital at start of month)").font = S["bold"]
    r += 1
    head(r, ["Year"] + MONTH_NAMES + ["Yr %", "Yr P&L (Rs)"])
    r += 1
    years = sorted({y for (y, m) in pd_["month"]})
    col_pcts = defaultdict(list)
    for y in years:
        label(r, y)
        growth, year_rs = 1.0, 0.0
        for m in range(1, 13):
            b = pd_["month"].get((y, m))
            if b:
                put_pct(r, 1 + m, b[0], b[1])
                growth *= 1 + b[0] / b[1] if b[1] else 1
                year_rs += b[0]
                col_pcts[m].append(b[0] / b[1] if b[1] else 0.0)
            else:
                ws.cell(row=r, column=1 + m).border = S["border"]
        c = ws.cell(row=r, column=14, value=growth - 1)
        c.number_format, c.border = "0.0%", S["border"]
        c.font = S["good"] if growth >= 1 else S["bad"]
        put_rs(r, 15, year_rs)
        r += 1
    label(r, "Avg")
    for m in range(1, 13):
        c = ws.cell(row=r, column=1 + m)
        c.border = S["border"]
        if col_pcts[m]:
            v = sum(col_pcts[m]) / len(col_pcts[m])
            c.value, c.number_format = v, "0.0%"
            c.font = S["good"] if v >= 0 else S["bad"]
    r += 2

    # ================= WEEKLY (Month x W1..W6) =================
    ws.cell(row=r, column=1,
            value="WEEKLY returns (% of capital at start of that week; weeks split at month edges)").font = S["bold"]
    r += 1
    head(r, ["Month"] + [f"W{i}" for i in range(1, 7)] + ["Month %", "Month P&L (Rs)"])
    r += 1
    for (y, m) in sorted(pd_["month"]):
        label(r, f"{y}-{MONTH_NAMES[m - 1]}")
        weeks = sorted(k[2] for k in pd_["week_in_month"] if k[0] == y and k[1] == m)
        for i, wk in enumerate(weeks[:6]):
            b = pd_["week_in_month"][(y, m, wk)]
            put_pct(r, 2 + i, b[0], b[1])
        for i in range(len(weeks), 6):
            ws.cell(row=r, column=2 + i).border = S["border"]
        mb = pd_["month"][(y, m)]
        put_pct(r, 8, mb[0], mb[1], bold=True)
        put_rs(r, 9, mb[0])
        r += 1
    r += 1

    # ================= DAILY (Week x Mon..Fri) =================
    ws.cell(row=r, column=1,
            value="DAILY returns (% of capital at start of that day; blank = no trade that day)").font = S["bold"]
    r += 1
    head(r, ["Week starting (Mon)", "Mon", "Tue", "Wed", "Thu", "Fri", "Week %", "Week P&L (Rs)"])
    r += 1
    from datetime import timedelta
    for wk in sorted(pd_["week"]):
        label(r, wk.strftime("%d-%b-%Y"))
        for i in range(5):
            day = wk + timedelta(days=i)
            if day in pd_["daily"]:
                put_pct(r, 2 + i, *pd_["daily"][day])
            else:
                ws.cell(row=r, column=2 + i).border = S["border"]
        wb_ = pd_["week"][wk]
        put_pct(r, 7, wb_[0], wb_[1], bold=True)
        put_rs(r, 8, wb_[0])
        r += 1
    r += 1

    # ================= STATISTICS =================
    ws.cell(row=r, column=1, value="STATISTICS (all trades for this index)").font = S["bold"]
    r += 1
    for lab, val, fmt, tone in compute_stats(trades, start_cap):
        lc = ws.cell(row=r, column=1, value=lab)
        vc = ws.cell(row=r, column=2, value=val)
        lc.font, lc.border, vc.border = S["bold"], S["border"], S["border"]
        vc.font = S.get(tone, S["norm"]) if tone else S["norm"]
        vc.alignment = Alignment(horizontal="right")
        if isinstance(val, (int, float)):
            vc.number_format = {"money": "#,##0.00;-#,##0.00", "pct": '0.00"%"', "ratio": "0.00", "int": "0"}.get(fmt, "General")
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        r += 1
    r += 1
    ws.cell(row=r, column=1,
            value="Caveat: with only a handful of trades these numbers (Sharpe, profit factor, drawdown %) "
                  "are not statistically reliable. Watch this over weeks/months before drawing conclusions.").font = S["note"]


def build_workbook(by_index: dict, start_cap: float, title_note: str = ""):
    """Shared by both the LIVE dashboard (reads reports/trade_log.csv) and
    the BACKTEST dashboard (reads backtest trade records instead) -- same
    Summary + per-index Monthly/Weekly/Daily-grid + Statistics layout
    either way. `by_index` maps INDEX NAME -> list of trade dicts, each
    with (at least) _date, _net, _costs, _capital_used, _pct, Index keys."""
    import openpyxl
    S = _styles()
    all_trades = [t for tr in by_index.values() for t in tr]

    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = "Summary"
    ws0["A1"] = f"Summary by index -- PAPER TRADE, NOT REAL MONEY{title_note}"
    ws0["A1"].font = S["title"]
    heads = ["Index", "Trades", "Win %", "Net P&L (Rs)", "Profit Factor", "Max DD (%)", "Avg P/L per trade (Rs)"]
    for c, h in enumerate(heads, start=1):
        cell = ws0.cell(row=3, column=c, value=h)
        cell.fill, cell.font, cell.alignment, cell.border = S["hfill"], S["hfont"], S["center"], S["border"]
        ws0.column_dimensions[chr(64 + c)].width = 22 if c == 7 else 15

    groups = [(name, by_index.get(name, [])) for name in config.INSTRUMENTS]
    groups.append(("ALL INDICES", all_trades))
    for i, (name, tr) in enumerate(groups):
        r = 4 + i
        ws0.cell(row=r, column=1, value=name).font = S["bold"]
        if tr:
            nets = [t["_net"] for t in tr]
            wins = [n for n in nets if n > 0]
            loss = abs(sum(n for n in nets if n <= 0))
            pf = (sum(wins) / loss) if loss else ("inf" if wins else "N/A")
            _, dd_pct = _drawdown(tr, start_cap)
            vals = [len(tr), len(wins) / len(tr), round(sum(nets), 2), pf if isinstance(pf, str) else round(pf, 2),
                    -dd_pct / 100, round(sum(nets) / len(tr), 2)]
            fmts = ["0", "0.0%", "#,##0.00;-#,##0.00", "0.00", "0.00%", "#,##0.00;-#,##0.00"]
        else:
            vals, fmts = ["-"] * 6, ["General"] * 6
            vals[0] = 0
        for c, (v, f) in enumerate(zip(vals, fmts), start=2):
            cell = ws0.cell(row=r, column=c, value=v)
            cell.number_format, cell.border, cell.font = f, S["border"], S["norm"]
            if c == 4 and isinstance(v, (int, float)):
                cell.font = S["good"] if v >= 0 else S["bad"]
        ws0.cell(row=r, column=1).border = S["border"]
    ws0.cell(row=4 + len(groups) + 1, column=1,
             value="Each index has its own sheet with the monthly-returns grid and full statistics.").font = S["note"]

    for name, tr in groups:
        ws = wb.create_sheet(name)
        _write_index_sheet(ws, name + title_note, tr, start_cap, S)

    from openpyxl.worksheet.properties import PageSetupProperties
    for ws in wb.worksheets:                      # tidy printing / PDF export
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    return wb


def generate_dashboard(trade_log_path: str = None, out_path: str = None, starting_capital: float = None):
    """Builds ONE dashboard.xlsx with BOTH live-trading sheets (prefixed
    plain, e.g. "NIFTY") AND backtest sheets (prefixed "BT-", e.g.
    "BT-NIFTY") -- whichever data is currently checked out in the repo.
    Backtest data comes from reports/backtest_best_trades.csv (written by
    the backtest job; see reports/csv_log.py) so EITHER the backtest job or
    the paper-trade job can produce a fully up-to-date combined file,
    regardless of which one happens to run -- both check out the same repo
    state, so whichever ran most recently on each side is what shows up."""
    from reports.csv_log import read_backtest_best_trades_csv

    trade_log_path = trade_log_path or _TRADE_LOG_PATH
    out_path = out_path or _DASHBOARD_PATH
    start_cap = starting_capital or config.STARTING_CAPITAL

    trades = _read_trades(trade_log_path)
    by_index = defaultdict(list)
    for t in trades:
        by_index[(t.get("Index") or "").upper()].append(t)

    bt_trades_by_index, bt_labels_by_index = read_backtest_best_trades_csv()

    wb = build_workbook(by_index, start_cap)

    if bt_trades_by_index:
        S = _styles()
        bt_all = [t for tr in bt_trades_by_index.values() for t in tr]
        ws_bt_sum = wb.create_sheet("BT-Summary")
        ws_bt_sum["A1"] = "BACKTEST summary by index (best strategy per index) -- PAPER TRADE, NOT REAL MONEY"
        ws_bt_sum["A1"].font = S["title"]
        heads = ["Index", "Best Strategy", "Trades", "Win %", "Net P&L (Rs)", "Profit Factor", "Max DD (%)"]
        for c, h in enumerate(heads, start=1):
            cell = ws_bt_sum.cell(row=3, column=c, value=h)
            cell.fill, cell.font, cell.alignment, cell.border = S["hfill"], S["hfont"], S["center"], S["border"]
            ws_bt_sum.column_dimensions[chr(64 + c)].width = 22 if c == 2 else 15
        bt_groups = [(name, bt_trades_by_index.get(name, [])) for name in config.INSTRUMENTS]
        bt_groups.append(("ALL INDICES", bt_all))
        for i, (name, tr) in enumerate(bt_groups):
            r = 4 + i
            ws_bt_sum.cell(row=r, column=1, value=name).font = S["bold"]
            ws_bt_sum.cell(row=r, column=2, value=bt_labels_by_index.get(name, "-" if name != "ALL INDICES" else "(combined)"))
            if tr:
                nets = [t["_net"] for t in tr]
                wins = [n for n in nets if n > 0]
                loss = abs(sum(n for n in nets if n <= 0))
                pf = (sum(wins) / loss) if loss else ("inf" if wins else "N/A")
                _, dd_pct = _drawdown(tr, start_cap)
                vals = [len(tr), len(wins) / len(tr), round(sum(nets), 2),
                        pf if isinstance(pf, str) else round(pf, 2), -dd_pct / 100]
                fmts = ["0", "0.0%", "#,##0.00;-#,##0.00", "0.00", "0.00%"]
            else:
                vals, fmts = ["-"] * 5, ["General"] * 5
                vals[0] = 0
            for c, (v, f) in enumerate(zip(vals, fmts), start=3):
                cell = ws_bt_sum.cell(row=r, column=c, value=v)
                cell.number_format, cell.border, cell.font = f, S["border"], S["norm"]
                if c == 5 and isinstance(v, (int, float)):
                    cell.font = S["good"] if v >= 0 else S["bad"]
            ws_bt_sum.cell(row=r, column=1).border = S["border"]
            ws_bt_sum.cell(row=r, column=2).border = S["border"]
        note_row = 4 + len(bt_groups) + 1
        ws_bt_sum.cell(row=note_row, column=1,
                 value="BT- sheets are from the BACKTEST (each index's single best combo, so time periods "
                       "aren't double-counted across competing strategies). Plain-named sheets are LIVE paper "
                       "trades. See latest_backtest_summary.xlsx for every combo tested.").font = S["note"]

        for name, tr in bt_groups:
            ws = wb.create_sheet(f"BT-{name}")
            label_note = f" -- best: {bt_labels_by_index.get(name, '')}" if name != "ALL INDICES" else ""
            _write_index_sheet(ws, f"BACKTEST {name}{label_note}", tr, start_cap, S)

    from openpyxl.worksheet.properties import PageSetupProperties
    for ws in wb.worksheets:
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    wb.save(out_path)
    return out_path


_BACKTEST_DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "latest_backtest_dashboard.xlsx")


def generate_backtest_dashboard(best_trades_by_index: dict, labels_by_index: dict,
                                 out_path: str = None, starting_capital: float = None):
    """Same Monthly/Weekly/Daily-grid + Statistics layout as the live
    dashboard, but built from BACKTEST trade records instead -- one sheet
    per index, showing the single BEST-performing strategy/EMA combo found
    for that index (since combining every tested combo's trades together
    would double-count the same time period across competing strategies).

    best_trades_by_index: {index_name: [adapted trade dicts]} (see main.py's
    call site for how raw backtest trade records get adapted).
    labels_by_index: {index_name: "strategy_name (EMAx/y)"} -- shown in each
    sheet's title so it's clear which strategy the grid represents.
    """
    out_path = out_path or _BACKTEST_DASHBOARD_PATH
    start_cap = starting_capital or config.STARTING_CAPITAL
    try:
        # per-index title note so each sheet says which strategy it is
        wb = None
        import openpyxl
        S = _styles()
        wb = openpyxl.Workbook()
        all_trades = [t for tr in best_trades_by_index.values() for t in tr]
        ws0 = wb.active
        ws0.title = "Summary"
        ws0["A1"] = "Backtest Summary by index (best strategy per index) -- PAPER TRADE, NOT REAL MONEY"
        ws0["A1"].font = S["title"]
        heads = ["Index", "Best Strategy", "Trades", "Win %", "Net P&L (Rs)", "Profit Factor", "Max DD (%)"]
        for c, h in enumerate(heads, start=1):
            cell = ws0.cell(row=3, column=c, value=h)
            cell.fill, cell.font, cell.alignment, cell.border = S["hfill"], S["hfont"], S["center"], S["border"]
            ws0.column_dimensions[chr(64 + c)].width = 22 if c == 2 else 15
        groups = [(name, best_trades_by_index.get(name, [])) for name in config.INSTRUMENTS]
        groups.append(("ALL INDICES", all_trades))
        for i, (name, tr) in enumerate(groups):
            r = 4 + i
            ws0.cell(row=r, column=1, value=name).font = S["bold"]
            ws0.cell(row=r, column=2, value=labels_by_index.get(name, "-" if name != "ALL INDICES" else "(combined)"))
            if tr:
                nets = [t["_net"] for t in tr]
                wins = [n for n in nets if n > 0]
                loss = abs(sum(n for n in nets if n <= 0))
                pf = (sum(wins) / loss) if loss else ("inf" if wins else "N/A")
                _, dd_pct = _drawdown(tr, start_cap)
                vals = [len(tr), len(wins) / len(tr), round(sum(nets), 2),
                        pf if isinstance(pf, str) else round(pf, 2), -dd_pct / 100]
                fmts = ["0", "0.0%", "#,##0.00;-#,##0.00", "0.00", "0.00%"]
            else:
                vals, fmts = ["-"] * 5, ["General"] * 5
                vals[0] = 0
            for c, (v, f) in enumerate(zip(vals, fmts), start=3):
                cell = ws0.cell(row=r, column=c, value=v)
                cell.number_format, cell.border, cell.font = f, S["border"], S["norm"]
                if c == 5 and isinstance(v, (int, float)):
                    cell.font = S["good"] if v >= 0 else S["bad"]
            ws0.cell(row=r, column=1).border = S["border"]
            ws0.cell(row=r, column=2).border = S["border"]
        note_row = 4 + len(groups) + 1
        ws0.cell(row=note_row, column=1,
                 value="Each index sheet shows ONLY its single best-performing strategy/EMA combo from this "
                       "backtest -- not all combos combined (that would double-count the same time period "
                       "across competing strategies). See latest_backtest_summary.xlsx for every combo.").font = S["note"]

        for name, tr in groups:
            ws = wb.create_sheet(name)
            title_note = f" -- best: {labels_by_index.get(name, '')}" if name != "ALL INDICES" else ""
            _write_index_sheet(ws, name + title_note, tr, start_cap, S)

        from openpyxl.worksheet.properties import PageSetupProperties
        for ws in wb.worksheets:
            ws.page_setup.orientation = "landscape"
            ws.page_setup.fitToWidth = 1
            ws.page_setup.fitToHeight = 0
            ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

        wb.save(out_path)
        return out_path
    except Exception as e:
        import logging
        logging.getLogger("reports.dashboard").warning("Could not write latest_backtest_dashboard.xlsx: %s", e)
        return None


if __name__ == "__main__":
    print("Dashboard written to", generate_dashboard())
