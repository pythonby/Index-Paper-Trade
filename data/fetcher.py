"""
data/fetcher.py
================
Free data sources only. Two very different data problems are solved here,
and it's important to understand the difference:

1. HISTORICAL INDEX OHLC (for backtesting signal generation)
   -> yfinance (Yahoo Finance), completely free, no API key.
   LIMITATION (real, not hypothetical): Yahoo only serves intraday data for a
   limited lookback window:
     - 1m candles: last ~7 days only
     - 2m/5m/15m/30m/60m candles: last ~60 days only
   This is a Yahoo-side restriction on the free tier. There is no free source
   of deep intraday NSE index history that we are aware of. If you need a
   longer backtest window, you must either (a) pay for a data vendor, or
   (b) accumulate your own history day by day going forward.

2. LIVE OPTION CHAIN (for actual paper-trading strike/LTP/spread selection)
   -> NSE's public (unofficial, undocumented) option-chain JSON endpoint.
   This works today but NSE can rate-limit or change the response format
   without notice. It is NOT a documented/supported API, has no SLA, and
   must not be treated as institutional-grade data. If it fails, the bot
   must stop generating new trades (see safety/failsafe rules) rather than
   guess.

There is NO free source of *historical* option premium data with minute-level
granularity for NSE options. That's why the backtest engine reconstructs
option premiums synthetically from index OHLC via Black-Scholes -- this is
clearly flagged everywhere as a model assumption.
"""

import time
import logging
import requests
import pandas as pd

import config

logger = logging.getLogger("data.fetcher")


class DataFeedError(Exception):
    """Raised when a data source cannot provide reliable data. Callers must
    treat this as a signal to STOP generating new trades, per the safety
    fail-safe rules -- never guess/fill in missing market data."""
    pass


def fetch_index_history(symbol: str, interval_min: int, period: str = "60d") -> pd.DataFrame:
    """
    Fetch historical OHLCV bars for an index via yfinance.

    symbol: one of config.INSTRUMENTS ("NIFTY", "BANKNIFTY", "FINNIFTY")
    interval_min: one of config.TIMEFRAMES_MIN
    period: yfinance period string, e.g. "7d", "60d". Longer periods will be
            silently truncated by Yahoo for intraday intervals -- see module
            docstring.

    Returns a DataFrame indexed by tz-aware IST timestamp with columns:
    open, high, low, close, volume
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise DataFeedError(
            "yfinance is not installed. Run: pip install yfinance"
        ) from e

    ticker = config.YFINANCE_SYMBOLS.get(symbol)
    if ticker is None:
        raise DataFeedError(f"No Yahoo Finance mapping configured for {symbol}")

    interval_map = {1: "1m", 3: "3m", 5: "5m", 15: "15m", 30: "30m", 60: "60m"}
    yf_interval = interval_map.get(interval_min)
    if yf_interval is None:
        raise DataFeedError(f"Unsupported interval {interval_min}m")

    if yf_interval == "1m" and period not in ("1d", "5d", "7d"):
        logger.warning("Yahoo only allows ~7 days of 1m history; clamping period to 7d.")
        period = "7d"

    try:
        df = yf.download(
            tickers=ticker,
            interval=yf_interval,
            period=period,
            progress=False,
            auto_adjust=False,
        )
    except Exception as e:
        raise DataFeedError(f"yfinance download failed for {symbol}: {e}") from e

    if df is None or df.empty:
        raise DataFeedError(
            f"No data returned for {symbol} ({ticker}) at {interval_min}m. "
            f"This can happen for FINNIFTY, which Yahoo does not always mirror well."
        )

    # yfinance sometimes returns MultiIndex columns for single-ticker downloads
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]

    df = df.rename(columns={"adj close": "adj_close"})
    df = df[["open", "high", "low", "close", "volume"]].dropna()

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
    else:
        df.index = df.index.tz_convert("Asia/Kolkata")

    return df


_NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_live_option_chain(symbol: str, max_retries: int = 2, timeout: int = 8) -> dict:
    """
    Fetch the LIVE option chain snapshot from NSE's public JSON endpoint.
    This is UNOFFICIAL and can break at any time -- callers must handle
    DataFeedError and stop generating new signals rather than guess values.

    Returns the raw parsed JSON (caller extracts CE/PE rows for the desired
    expiry/strike).
    """
    session = requests.Session()
    url = _NSE_OPTION_CHAIN_URL.format(symbol=symbol)

    last_err = None
    for attempt in range(max_retries + 1):
        try:
            # NSE requires a warm-up hit to the homepage to set cookies first.
            session.get("https://www.nseindia.com", headers=_NSE_HEADERS, timeout=timeout)
            resp = session.get(url, headers=_NSE_HEADERS, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if not data or "records" not in data:
                raise DataFeedError("Unexpected option-chain response shape from NSE.")
            return data
        except Exception as e:
            last_err = e
            logger.warning("Option chain fetch attempt %d failed: %s", attempt + 1, e)
            time.sleep(1.5)

    raise DataFeedError(
        f"Could not fetch live option chain for {symbol} after {max_retries + 1} attempts: {last_err}"
    )


def is_data_stale(last_tick_time, max_staleness_seconds: int = 90) -> bool:
    """Used by the live engine's fail-safe check."""
    import datetime as dt
    if last_tick_time is None:
        return True
    now = dt.datetime.now(last_tick_time.tzinfo) if last_tick_time.tzinfo else dt.datetime.now()
    return (now - last_tick_time).total_seconds() > max_staleness_seconds
