"""
config.py
=========
Single source of truth for every tunable parameter in the system.

IMPORTANT: This is a PAPER TRADING system. No real orders are ever placed.
All costs/slippage assumptions below are estimates you should review against
your actual broker's tariff sheet before trusting the numbers.
"""

import os
from datetime import time

# ---------------------------------------------------------------------------
# 1. CAPITAL & TRADING RULES
# ---------------------------------------------------------------------------
STARTING_CAPITAL = 20_000.0          # <-- change this to alter starting capital
PAPER_TRADING_ONLY = True            # hard safety flag, must stay True

INSTRUMENTS = ["NIFTY", "BANKNIFTY", "FINNIFTY"]

# Only long options are ever allowed. Do not add "SELL_CE" / "SELL_PE".
ALLOWED_ACTIONS = ["BUY_CE", "BUY_PE"]

TIMEFRAMES_MIN = [1, 3, 5, 15, 30, 60]   # in minutes
DEFAULT_TIMEFRAME_MIN = 5

INTRADAY_ONLY = True
SQUARE_OFF_TIME = time(15, 15)        # force-close all paper positions by this time
MARKET_OPEN_TIME = time(9, 15)
MARKET_CLOSE_TIME = time(15, 30)
# Avoid the first few minutes of chop and last minutes of illiquidity
NO_NEW_ENTRY_AFTER = time(15, 0)
NO_NEW_ENTRY_BEFORE = time(9, 20)

MAX_OPEN_POSITIONS = 1

# ---------------------------------------------------------------------------
# 2. RISK MANAGEMENT
# ---------------------------------------------------------------------------
RISK_PER_TRADE_PCT = 0.015            # <-- 1.5% of available capital risked per trade
MAX_CAPITAL_DEPLOY_PCT = 0.35         # never deploy more than 35% of capital in one trade's premium
STOP_LOSS_PCT_OF_PREMIUM = 0.30       # SL = 30% loss on option premium
TARGET_PCT_OF_PREMIUM = 0.60          # Target = 60% gain on option premium
USE_TRAILING_STOP = True
TRAILING_STOP_TRIGGER_PCT = 0.35      # once premium up 35%, start trailing
TRAILING_STOP_GIVEBACK_PCT = 0.15     # trail 15% behind peak premium

MAX_DAILY_LOSS_PCT = 0.06             # 6% of starting capital -> stop trading for the day
MAX_CONSECUTIVE_LOSSES = 3
MAX_TRADES_PER_DAY = 4

MIN_RISK_REWARD_RATIO = 1.5           # target_risk_reward = TARGET_PCT / SL_PCT must be >= this

# ---------------------------------------------------------------------------
# 3. SLIPPAGE & TRADING COSTS  (kept fully visible, nothing hidden)
# ---------------------------------------------------------------------------
# Slippage expressed in index points is not meaningful for options premiums,
# so we model it as a % of premium on both entry and exit (worse fill than signal).
SLIPPAGE_PCT_OF_PREMIUM = 0.015       # 1.5% adverse slippage per fill (entry AND exit)
MIN_SLIPPAGE_RS_PER_LOT = 0.05        # floor, in Rs per unit premium

# Approximate discount-broker style charges for BUY options (India, 2025-26 era rates).
# These are ESTIMATES. Verify against your broker before relying on them.
BROKERAGE_PER_ORDER = 20.0            # flat Rs per executed order (or min(20, 0.03%*turnover))
BROKERAGE_PCT_OF_TURNOVER = 0.0003    # 0.03%
EXCHANGE_TXN_CHARGE_PCT = 0.00053     # approx NSE F&O options exchange charge on premium turnover
SEBI_CHARGES_PCT = 0.0000010          # Rs 10 / crore
STAMP_DUTY_BUY_PCT = 0.00003          # 0.003% on buy side only
GST_PCT = 0.18                        # 18% GST on (brokerage + exchange txn charge)
STT_SELL_PCT = 0.0625 / 100           # STT applies on SELL side of options (0.0625% of premium) -
                                       # charged when we square off / exercise; buy side = 0

# ---------------------------------------------------------------------------
# 4. OPTION SELECTION
# ---------------------------------------------------------------------------
STRIKE_SELECTION = "ATM"              # "ATM", "ITM1", "OTM1"
MAX_STRIKES_FROM_ATM = 1
MIN_DAYS_TO_EXPIRY = 0
MAX_DAYS_TO_EXPIRY = 7                # avoid far-dated illiquid monthly if a weekly exists
MAX_BID_ASK_SPREAD_PCT = 0.06         # reject if (ask-bid)/ltp > 6%
MIN_OPTION_LTP = 5.0                  # avoid near-worthless far OTM junk
MAX_OPTION_LTP_PCT_OF_CAPITAL = 0.5   # sanity check vs capital

# Simulated IV used ONLY for backtesting premium reconstruction (no free historical
# option data exists). This is clearly a MODEL ASSUMPTION, not real market IV.
BACKTEST_ASSUMED_IV = {
    "NIFTY": 0.13,
    "BANKNIFTY": 0.16,
    "FINNIFTY": 0.15,
}
BACKTEST_RISK_FREE_RATE = 0.07

# ---------------------------------------------------------------------------
# 5. SIGNAL QUALITY
# ---------------------------------------------------------------------------
MIN_SIGNAL_SCORE = 65                 # 0-100 scale, only trade signals scoring >= this

# ---------------------------------------------------------------------------
# 6. STRATEGY TOGGLES
# ---------------------------------------------------------------------------
ENABLED_STRATEGIES = {
    "vwap_ema_momentum": True,
    "opening_range_breakout": True,
    "trend_pullback": True,
    "mean_reversion": True,       # Strategy D -- sideways/range-bound markets only
}

EMA_FAST_CANDIDATES = [5, 8, 9]
EMA_SLOW_CANDIDATES = [13, 21, 26]
ORB_RANGE_MINUTES_CANDIDATES = [15, 30]

BOLLINGER_WINDOW = 20
BOLLINGER_STD = 2.0
MEAN_REVERSION_RSI_OVERSOLD = 35
MEAN_REVERSION_RSI_OVERBOUGHT = 65

# Which timeframes to test in "python main.py backtest". Testing all 6 is
# thorough but slow (many more combinations); trim this list if a full run
# is taking too long in your environment (e.g. on a GitHub Actions runner).
BACKTEST_TIMEFRAMES_MIN = [5, 15]   # add 1, 3, 30, 60 here for a full sweep

# ---------------------------------------------------------------------------
# 7. DATABASE / LOGGING
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), "db", "trading.db")
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LOG_LEVEL = "INFO"

# ---------------------------------------------------------------------------
# 8. TELEGRAM  (never hard-code secrets here — read from environment / .env)
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

# ---------------------------------------------------------------------------
# 9. DATA SOURCES
# ---------------------------------------------------------------------------
# Free historical index OHLC via yfinance. Symbols map to Yahoo tickers.
YFINANCE_SYMBOLS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",   # best-effort; may not always have intraday history
}

# yfinance intraday history is limited by Yahoo (e.g. 1m data only for last ~7 days,
# other intraday intervals for last ~60 days). This is a genuine free-tier limitation,
# not a bug — see README for details.
