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

2. reports/latest_backtest_summary.csv -- the FULL strategy-comparison
   table (every combination, not just Telegram's top 15), OVERWRITTEN each
   time a backtest runs (it reflects the latest test, not a history).
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
