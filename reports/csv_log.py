"""
reports/csv_log.py
====================
Writes two persistent CSV files INTO THE REPO (committed back to git by the
GitHub Actions workflow after each run -- see .github/workflows/paper_trading.yml).
This solves a real usability problem: Telegram sends one message per trade
plus separate daily/weekly/monthly messages, which becomes hard to track by
scrolling. GitHub renders a .csv file as a clean, sortable-by-eye table right
in the browser (no download needed) -- open the file on github.com and it's
just there, always up to date, no screenshots required.

1. reports/trade_log.csv -- ONE ROW PER LIVE PAPER TRADE, appended to over
   time. Never overwritten. Filter/sort by the Date column in GitHub's own
   CSV viewer (or Excel/Google Sheets) to see a single day, a week, or a
   month -- that's the "how do I see the whole week/month together" answer.

2. reports/latest_backtest_summary.csv (and its formatted twin,
   latest_backtest_summary.xlsx) -- the FULL strategy-comparison table
   (every combination, not just Telegram's top 15), OVERWRITTEN each time
   a backtest runs (it reflects the latest test, not a history). The
   .xlsx is colored (green/red Net P&L), sorted best-to-worst, with
   filter/sort dropdowns -- always the same look, no manual reformatting.
"""

import csv
import os

_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.csv")
_BACKTEST_SUMMARY_PATH = os.path.join(os.path.dirname(__file__), "latest_backtest_summary.csv")

_TRADE_LOG_COLUMNS = [
    "Date", "Time", "Index", "Strategy", "Timeframe", "Direction", "Strike",
    "Entry", "Exit", "Qty", "Gross P&L", "Costs", "Net P&L", "Result", "Exit Reason",
]


def append_trade_to_csv(record: dict, path: str = None):
    """Appends one completed live paper trade as a row. Creates the file
    with a header row on first use. `record` is the same dict paper_trading.
    engine._close_position() saves to the database -- see its docstring
    for field names."""
    path = path or _TRADE_LOG_PATH
    entry_time = str(record.get("entry_time", ""))
    date_part = entry_time[:10]
    time_part = entry_time[11:16] if len(entry_time) > 10 else ""
    net = record.get("net_pnl", 0.0)
    row = [
        date_part, time_part, record.get("index_name", ""), record.get("strategy", ""),
        f"{record.get('timeframe_min', '')}m", record.get("direction", ""), record.get("strike", ""),
        round(record.get("entry_execution_price", 0.0), 2), round(record.get("exit_execution_price", 0.0), 2),
        record.get("quantity", ""), round(record.get("gross_pnl", 0.0), 2),
        round(record.get("slippage_cost", 0.0) + record.get("charges", 0.0), 2),
        round(net, 2), "PROFIT" if net >= 0 else "LOSS", record.get("exit_reason", ""),
    ]
    file_exists = os.path.exists(path)
    try:
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(_TRADE_LOG_COLUMNS)
            writer.writerow(row)
    except Exception as e:
        # A logging failure must never take down the trading loop.
        import logging
        logging.getLogger("reports.csv_log").warning("Could not append to trade_log.csv: %s", e)


_BACKTEST_COLUMNS = ["Strategy", "EMA", "Index", "Timeframe", "Trades", "Win Rate %",
                      "Profit Factor", "Net P&L", "Walk-Forward Robust"]


def write_backtest_summary_csv(rows: list, path: str = None):
    """OVERWRITES the full backtest comparison table (every combination
    tested, not just the top 15 that fit in a Telegram message).
    `rows` is a list of dicts, each with keys matching _BACKTEST_COLUMNS
    (case-insensitive-ish; see main.py's call site for the exact shape)."""
    path = path or _BACKTEST_SUMMARY_PATH
    try:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_BACKTEST_COLUMNS)
            writer.writeheader()
            for r in rows:
                writer.writerow({col: r.get(col, "") for col in _BACKTEST_COLUMNS})
    except Exception as e:
        import logging
        logging.getLogger("reports.csv_log").warning("Could not write latest_backtest_summary.csv: %s", e)


_BACKTEST_XLSX_PATH = os.path.join(os.path.dirname(__file__), "latest_backtest_summary.xlsx")


def write_backtest_summary_xlsx(rows: list, path: str = None):
    """OVERWRITES a formatted .xlsx twin of latest_backtest_summary.csv --
    same data, but colored (green/red Net P&L, grey zero-trade rows),
    sorted best-to-worst, with a filter/sort dropdown on the header row and
    auto-sized columns. This is the "same format every time" version asked
    for after a one-off manual conversion in chat -- now it's automatic,
    no need to paste the CSV back for reformatting."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    path = path or _BACKTEST_XLSX_PATH
    try:
        ARIAL = "Arial"
        HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        HEADER_FONT = Font(name=ARIAL, bold=True, color="FFFFFF")
        THIN = Side(style="thin", color="B7B7B7")
        BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
        GREEN = Font(name=ARIAL, color="0B8A00")
        RED = Font(name=ARIAL, color="C00000")
        GREY = Font(name=ARIAL, color="999999")

        sorted_rows = sorted(
            rows,
            key=lambda r: r.get("Net P&L") if isinstance(r.get("Net P&L"), (int, float)) else -1e18,
            reverse=True,
        )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Backtest Full Results"
        ws["A1"] = "NSE Paper Trading Bot -- Full Backtest Results"
        ws["A1"].font = Font(name=ARIAL, bold=True, size=13)
        ws["A2"] = "PAPER TRADE -- NOT REAL MONEY. Sorted by Net P&L, highest first."
        ws["A2"].font = Font(name=ARIAL, italic=True, size=9, color="C00000")

        with_trades = [r for r in sorted_rows if r.get("Trades", 0) not in (0, "0")]
        ws["A4"] = "Combinations tested:"
        ws["B4"] = len(sorted_rows)
        ws["A5"] = "Had at least one trade:"
        ws["B5"] = len(with_trades)
        ws["A6"] = "Had zero trades:"
        ws["B6"] = len(sorted_rows) - len(with_trades)
        for rr in (4, 5, 6):
            ws[f"A{rr}"].font = Font(name=ARIAL, bold=True)

        headers = ["Strategy", "EMA", "Index", "Timeframe", "Trades", "Win Rate %",
                   "Profit Factor", "Net P&L (Rs)", "Walk-Forward Robust"]
        hr = 8
        for i, h in enumerate(headers, start=1):
            c = ws.cell(row=hr, column=i, value=h)
            c.fill, c.font, c.border = HEADER_FILL, HEADER_FONT, BORDER
            c.alignment = Alignment(horizontal="center")

        r0 = hr + 1
        for i, row in enumerate(sorted_rows):
            r = r0 + i
            trades = row.get("Trades", 0)
            try:
                trades_n = int(trades)
            except (TypeError, ValueError):
                trades_n = 0
            ws.cell(row=r, column=1, value=row.get("Strategy", ""))
            ws.cell(row=r, column=2, value=row.get("EMA", ""))
            ws.cell(row=r, column=3, value=row.get("Index", ""))
            ws.cell(row=r, column=4, value=row.get("Timeframe", ""))
            ws.cell(row=r, column=5, value=trades_n)
            win = row.get("Win Rate %", 0)
            wc = ws.cell(row=r, column=6, value=(float(win) / 100 if win != "" else 0))
            wc.number_format = "0.0%"
            pf = row.get("Profit Factor", "")
            ws.cell(row=r, column=7, value=pf if pf in ("inf", "") else float(pf))
            net = row.get("Net P&L", 0)
            net_val = float(net) if net != "" else 0.0
            nc = ws.cell(row=r, column=8, value=net_val)
            nc.number_format = '"+Rs"#,##0.00;"-Rs"#,##0.00'
            nc.font = GREY if trades_n == 0 else (GREEN if net_val >= 0 else RED)
            ws.cell(row=r, column=9, value=row.get("Walk-Forward Robust", ""))
            for c in range(1, 10):
                ws.cell(row=r, column=c).border = BORDER
                if c != 8:
                    ws.cell(row=r, column=c).font = GREY if trades_n == 0 else Font(name=ARIAL)

        last = r0 + len(sorted_rows) - 1
        if sorted_rows:
            ws.auto_filter.ref = f"A{hr}:I{last}"
            ws.freeze_panes = f"A{r0}"

        for i, w in enumerate([22, 10, 11, 10, 8, 10, 13, 15, 16], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        note_row = last + 2
        ws.cell(row=note_row, column=1,
                value="Note: small sample sizes (1-4 trades) don't prove a real edge yet. Grey rows = "
                      "zero trades. Click the filter arrows on the header row to sort/filter.")
        ws.cell(row=note_row, column=1).font = Font(name=ARIAL, italic=True, size=9, color="666666")

        wb.save(path)
    except Exception as e:
        import logging
        logging.getLogger("reports.csv_log").warning("Could not write latest_backtest_summary.xlsx: %s", e)
