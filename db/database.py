"""
db/database.py
===============
SQLite persistence layer for paper trades, signals, and daily summaries.
No external DB server required -- one local file.
"""

import sqlite3
import os
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_uid TEXT UNIQUE,
    mode TEXT NOT NULL,              -- 'backtest' or 'paper_live'
    index_name TEXT NOT NULL,
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,         -- CE / PE
    strike REAL,
    expiry TEXT,
    timeframe_min INTEGER,
    signal_score REAL,
    entry_time TEXT,
    entry_signal_price REAL,
    entry_execution_price REAL,
    exit_time TEXT,
    exit_signal_price REAL,
    exit_execution_price REAL,
    quantity INTEGER,
    stop_loss REAL,
    target REAL,
    exit_reason TEXT,
    gross_pnl REAL,
    slippage_cost REAL,
    charges REAL,
    net_pnl REAL,
    capital_used REAL,
    risk_amount REAL,
    reasons TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signals_rejected (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    index_name TEXT NOT NULL,
    strategy TEXT,
    direction TEXT,
    signal_time TEXT,
    signal_score REAL,
    rejection_reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_summary (
    trading_date TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    starting_capital REAL,
    ending_capital REAL,
    trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate REAL,
    gross_pnl REAL,
    total_costs REAL,
    net_pnl REAL,
    max_drawdown REAL,
    best_trade REAL,
    worst_trade REAL,
    strategy_breakdown TEXT
);
"""


def get_connection():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


def insert_trade(trade: dict):
    cols = ", ".join(trade.keys())
    placeholders = ", ".join(["?"] * len(trade))
    with db_cursor() as cur:
        cur.execute(f"INSERT OR REPLACE INTO trades ({cols}) VALUES ({placeholders})",
                    list(trade.values()))


def insert_rejected_signal(row: dict):
    cols = ", ".join(row.keys())
    placeholders = ", ".join(["?"] * len(row))
    with db_cursor() as cur:
        cur.execute(f"INSERT INTO signals_rejected ({cols}) VALUES ({placeholders})",
                    list(row.values()))


def upsert_daily_summary(summary: dict):
    cols = ", ".join(summary.keys())
    placeholders = ", ".join(["?"] * len(summary))
    with db_cursor() as cur:
        cur.execute(f"INSERT OR REPLACE INTO daily_summary ({cols}) VALUES ({placeholders})",
                    list(summary.values()))


def fetch_trades(mode: str = None):
    with db_cursor() as cur:
        if mode:
            cur.execute("SELECT * FROM trades WHERE mode = ? ORDER BY entry_time", (mode,))
        else:
            cur.execute("SELECT * FROM trades ORDER BY entry_time")
        return [dict(row) for row in cur.fetchall()]
