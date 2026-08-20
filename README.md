# NSE Index Options — FREE Paper Trading & Strategy Research System

**PAPER TRADING ONLY. No real-money orders are ever placed. There is no
broker order API integrated anywhere in this codebase.**

This is a research and paper-trading system for NIFTY/BANKNIFTY/FINNIFTY
index options (BUY CE / BUY PE only — no option selling, no naked shorts),
starting from a virtual capital of ₹20,000.

---

## 0. Read this first: what "free" actually gets you

Be realistic about free data before trusting any backtest number this
system produces:

| Data need | Free source used | Real limitation |
|---|---|---|
| Historical index OHLC (for signal backtesting) | Yahoo Finance via `yfinance` | 1-minute bars: last ~7 days only. Other intraday intervals: last ~60 days only. No free source of *deep* intraday NSE history exists that we're aware of. |
| Live option chain (strike, LTP, bid/ask, OI) for actual paper trading | NSE's public, **unofficial** option-chain JSON endpoint | Undocumented, no SLA, can rate-limit or change format without notice. The bot **stops generating new trades** if it fails — it never guesses. |
| Historical option premiums (for backtesting) | **Does not exist for free.** | The backtester reconstructs option premiums synthetically with Black-Scholes, using a configurable assumed IV (`config.BACKTEST_ASSUMED_IV`). This is a **model assumption**, clearly labeled everywhere in the code and output — not a claim that these were real historical option prices. Backtest P&L numbers should be read as "what this strategy would have done under this IV assumption," not gospel. |

If you need a rigorous, trustworthy backtest, you should eventually plug in
a paid historical options data vendor. This system is built so you can do
that later by replacing `options/selector.py`'s `select_backtest_contract`
with a real data lookup — nothing else needs to change.

---

## 1. Windows Installation

1. Install **Python 3.10+** from python.org (check "Add Python to PATH" during install).
2. Open Command Prompt / PowerShell and navigate to the project folder:
   ```
   cd path\to\nifty_paper_trading
   ```
3. (Recommended) Create a virtual environment:
   ```
   python -m venv venv
   venv\Scripts\activate
   ```
4. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
5. Copy the Telegram credentials template and fill it in:
   ```
   copy .env.example .env
   ```
   Edit `.env` with a text editor and paste your real `TELEGRAM_BOT_TOKEN`
   and `TELEGRAM_CHAT_ID` (see section 6 below for how to get these).
6. Load the `.env` file before running (either install `python-dotenv` and
   the app will pick it up automatically if you add `from dotenv import
   load_dotenv; load_dotenv()` at the top of `main.py`, or set the two
   environment variables manually in PowerShell each session:
   ```
   $env:TELEGRAM_BOT_TOKEN="123456:ABC..."
   $env:TELEGRAM_CHAT_ID="987654321"
   ```

---

## 2. How to run a backtest

```
python main.py backtest
```

This will:
1. Pull the free historical index data available for each instrument.
2. Run every enabled strategy across the configured EMA parameter
   candidates (`config.EMA_FAST_CANDIDATES` / `EMA_SLOW_CANDIDATES`).
3. Run **walk-forward validation** (rolling train/test folds) for every
   combination — a strategy is only ever considered "robust" if it passes
   out-of-sample, not just in-sample.
4. Print the full comparison table (trades, win rate, profit factor, net
   P&L, max drawdown) for every strategy/index/timeframe combination.
5. Either print the 2–3 selected robust strategies, **or** print:
   `NO PROFITABLE STRATEGY FOUND UNDER CURRENT TEST CONDITIONS.`
   if nothing survives realistic costs + out-of-sample testing. This is a
   valid, expected, and honestly-reported outcome — the system does not
   manipulate parameters to manufacture profitability.

## 3. How to start live paper trading

```
python main.py paper
```

This starts an infinite polling loop (default: every 30 seconds during
market hours, 09:15–15:30 IST) that:
- Fetches live index data and the live NSE option chain.
- Evaluates the enabled strategies for a signal.
- Applies signal scoring, regime filtering, liquidity/spread checks,
  position sizing, and all daily risk limits before ever "trading."
- Sends Telegram notifications for every entry, exit, and end-of-day report.
- Never carries a position overnight — force-squares-off at 15:15 IST.
- **Never places a real order.** All trades are simulated and recorded
  to the local SQLite database at `db/trading.db`.

## 4. How to stop the bot safely

Press **Ctrl+C** in the terminal running `python main.py paper`. The
engine catches the interrupt, **squares off any open paper positions**
at the last known live price, logs the final trade, and then exits
cleanly. Do not just close the terminal window forcefully if you have an
open position — always use Ctrl+C so the position gets closed out and
logged.

## 5. Changing key settings

All settings live in **`config.py`**. Nothing is hidden or hardcoded
elsewhere.

| Setting | Variable | 
|---|---|
| Starting capital (₹20,000 default) | `STARTING_CAPITAL` in `config.py` |
| Slippage | `SLIPPAGE_PCT_OF_PREMIUM`, `MIN_SLIPPAGE_RS_PER_LOT` |
| Risk per trade | `RISK_PER_TRADE_PCT` (fraction of *current* capital risked per trade) |
| Stop-loss / target | `STOP_LOSS_PCT_OF_PREMIUM`, `TARGET_PCT_OF_PREMIUM` |
| Daily loss limit | `MAX_DAILY_LOSS_PCT` |
| Max trades/day, max consecutive losses | `MAX_TRADES_PER_DAY`, `MAX_CONSECUTIVE_LOSSES` |
| Minimum signal score to trade | `MIN_SIGNAL_SCORE` |
| Brokerage/exchange/GST/STT assumptions | Section 3 of `config.py` — **verify against your real broker's tariff sheet**, these are estimates |

## 6. Adding / removing strategies

- Toggle existing strategies on/off via `config.ENABLED_STRATEGIES`
  (a simple `True`/`False` dict).
- To add a new strategy: create a new file in `strategies/`, subclass
  `strategies.base.Strategy`, implement `generate_signal(self, df, i)`
  returning a `Signal` or `None` (see the three existing strategies for
  the pattern — **never look at `df.iloc[i+1:]`**, only `df.iloc[:i+1]`,
  to avoid look-ahead bias). Then import and add it in `main.py`'s
  `build_strategy_set()` and to `config.ENABLED_STRATEGIES`.
- To remove a strategy: set its entry in `config.ENABLED_STRATEGIES` to
  `False`, or delete its file and references in `main.py`.

## 7. Getting a Telegram bot token & chat ID (free)

1. Open Telegram, message **@BotFather**, send `/newbot`, follow the
   prompts. You'll get a token like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxx`.
2. Message your new bot once (search for it by the username you chose).
3. Message **@userinfobot** to get your numeric chat ID, or call
   `https://api.telegram.org/bot<token>/getUpdates` after messaging your
   bot and read the `chat.id` field from the response.
4. Put both values in your `.env` file (see section 1, step 5).

If Telegram is not configured, the system still runs — it logs every
notification to the console/log file instead of sending it, and clearly
says so on startup.

---

## Project structure

```
nifty_paper_trading/
├── config.py                  # every tunable parameter, fully visible
├── main.py                    # entry point: backtest | paper | status
├── requirements.txt
├── .env.example
├── data/
│   └── fetcher.py              # yfinance (historical) + live NSE option chain
├── indicators/
│   └── __init__.py             # VWAP, EMA, RSI, ATR, opening range, realized vol
├── strategies/
│   ├── base.py
│   ├── vwap_ema_momentum.py    # Strategy A
│   ├── opening_range_breakout.py  # Strategy B
│   └── trend_pullback.py       # Strategy C
├── options/
│   └── selector.py             # Black-Scholes (backtest) + live contract selection
├── risk/
│   └── manager.py              # position sizing, SL/target/trailing stop, daily limits
├── costs/
│   └── model.py                # slippage + brokerage/exchange/GST/STT/SEBI charges
├── regime/
│   └── detector.py             # trend/volatility regime classification
├── signal/
│   └── scorer.py                # 0-100 explainable signal scoring
├── backtest/
│   └── engine.py                # bar-by-bar backtest, no look-ahead bias
├── validation/
│   └── walkforward.py           # train/test + rolling walk-forward folds
├── paper_trading/
│   └── engine.py                 # live (simulated) polling loop
├── notify/
│   └── telegram.py               # entry/exit/daily-report messages
├── reports/
│   └── performance.py            # win rate, profit factor, drawdown, comparison table
├── db/
│   └── database.py               # SQLite schema + access layer
└── logs/                         # runtime logs written here
```

---

## Important final notes

- This is a **research and paper-trading system, not a guaranteed-profit
  trading bot**. Nothing in this codebase promises or implies real-world
  profitability.
- Priority order, always: **capital preservation → realistic simulation →
  robust testing → risk management → profitability.**
- If a backtest run reports `NO PROFITABLE STRATEGY FOUND UNDER CURRENT
  TEST CONDITIONS`, that is the system working correctly — it means
  don't paper (let alone real) trade this configuration yet.
- Every Telegram message is prefixed with **"PAPER TRADE — NOT REAL
  MONEY"** and no code path in this repository submits a real order to
  any broker.
