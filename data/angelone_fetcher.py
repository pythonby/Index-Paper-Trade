"""
data/angelone_fetcher.py
=========================
OPTIONAL live-option-chain data source using Angel One's official SmartAPI,
for people who have a free Angel One demat account.

WHY THIS EXISTS:
The default NSE scraping method (data.fetcher.fetch_live_option_chain_nse)
hits NSE's unofficial, undocumented option-chain endpoint. NSE actively
blocks/rate-limits requests coming from cloud/datacenter IP ranges
(AWS, GCP, Azure, GitHub Actions runners, etc.) -- this is a well-known,
real-world limitation, not a bug in this codebase. If you run this system
on GitHub Actions and the live paper-trading loop halts within seconds of
starting every day, this is almost always why.

Angel One's SmartAPI is an OFFICIAL, AUTHENTICATED API (free for Angel One
clients) and is NOT subject to the same anti-bot blocking, so it works
reliably from GitHub Actions.

This module returns data in the SAME shape as the NSE endpoint
(see data.fetcher.fetch_live_option_chain_nse's docstring / the shape
consumed by options.selector.select_live_contract), so nothing else in
the codebase needs to change -- data.fetcher.fetch_live_option_chain()
picks this module automatically when config.ANGEL_ENABLED is True.

CREDENTIALS NEEDED (see config.py section 8 for where to set these):
    ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD, ANGEL_TOTP_SECRET

HOW IT WORKS:
1. Logs in once per process (session cached in memory) using TOTP 2FA.
2. Downloads Angel's public instrument/scrip master JSON (cached to disk
   for up to ANGEL_SCRIPMASTER_CACHE_HOURS hours -- it's a large file and
   Angel only refreshes it once a day at ~8:30 AM IST, so re-downloading
   every 30-second poll would be wasteful and slow).
3. Finds index-option contracts (OPTIDX) for the requested symbol, picks
   strikes near the current spot, and batch-fetches live quotes (LTP,
   bid/ask, OI, volume) via Angel's Market Data (Quote) API.
4. Assembles a dict shaped exactly like NSE's option-chain JSON so
   options.selector.select_live_contract() can consume it unchanged.

This is UNOFFICIAL glue code (not written/endorsed by Angel One) -- Angel's
API surface can change. Any failure here raises DataFeedError, same
fail-safe contract as the NSE path: never guess, just stop.
"""

import json
import logging
import os
import time
from datetime import datetime, date, timedelta
from typing import Optional

import requests

import config
from data.fetcher import DataFeedError
from utils.timeutils import now_ist

logger = logging.getLogger("data.angelone_fetcher")

_SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
_SCRIP_MASTER_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".angel_scrip_master_cache.json")
_SCRIP_MASTER_CACHE_HOURS = 20  # Angel refreshes their master once/day; well under 24h is safe

# Angel's fixed symbol tokens for the underlying indices themselves
# (confirmed against Angel's own instrument master / support forum -- these
# are NOT option contracts, just the index quote itself, exchange NSE).
_INDEX_TOKENS = {
    "NIFTY": ("NSE", "99926000", "Nifty 50"),
    "BANKNIFTY": ("NSE", "99926009", "Nifty Bank"),
    "FINNIFTY": ("NSE", "99926037", "Nifty Fin Service"),
}

# How many strikes on either side of ATM to pull quotes for. Wider than
# config.MAX_STRIKES_FROM_ATM so the selector's liquidity fallback still has
# a couple of extra candidates to fall back to, same margin the NSE endpoint
# naturally provides (it returns the whole chain; we only pull a slice).
_STRIKE_WINDOW = 5

# Angel's quote API accepts a limited number of tokens per call; chunk to
# stay well under any undocumented limit.
_QUOTE_BATCH_SIZE = 40

STRIKE_STEPS = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}

_session_cache = {"client": None, "logged_in_at": None}


# Angel's historical-candle endpoint caps how many days you can request in a
# SINGLE call (confirmed by Angel's own support forum): 100 days for a
# FIVE_MINUTE interval, 30 days for ONE_MINUTE. Larger windows are fetched by
# looping over multiple chunks. Kept deliberately conservative (used for
# 15m/30m/60m too, since those limits aren't publicly documented) rather than
# risking a rejected request.
_INTERVAL_MAP = {
    1: "ONE_MINUTE", 5: "FIVE_MINUTE", 15: "FIFTEEN_MINUTE",
    30: "THIRTY_MINUTE", 60: "ONE_HOUR",
}
_CHUNK_DAYS = {1: 28, 5: 95, 15: 95, 30: 95, 60: 95}


def _get_client():
    """Return a logged-in SmartConnect client, reusing the session for the
    lifetime of this process. Angel sessions are valid for the trading day,
    which comfortably covers one GitHub Actions run."""
    if _session_cache["client"] is not None:
        return _session_cache["client"]

    try:
        from SmartApi import SmartConnect
        import pyotp
    except ImportError as e:
        raise DataFeedError(
            "Angel One packages not installed. Run: "
            "pip install smartapi-python pyotp logzero websocket-client"
        ) from e

    if not config.ANGEL_ENABLED:
        raise DataFeedError(
            "Angel One credentials not configured (ANGEL_API_KEY / "
            "ANGEL_CLIENT_CODE / ANGEL_PASSWORD / ANGEL_TOTP_SECRET)."
        )

    try:
        totp = pyotp.TOTP(config.ANGEL_TOTP_SECRET).now()
        client = SmartConnect(api_key=config.ANGEL_API_KEY)
        session = client.generateSession(config.ANGEL_CLIENT_CODE, config.ANGEL_PASSWORD, totp)
        if not session or not session.get("status"):
            raise DataFeedError(f"Angel One login failed: {session}")
    except DataFeedError:
        raise
    except Exception as e:
        raise DataFeedError(f"Angel One login error: {e}") from e

    _session_cache["client"] = client
    _session_cache["logged_in_at"] = time.time()
    logger.info("Angel One SmartAPI session established.")
    # Angel's API is known to intermittently reject the very FIRST request
    # right after login with "Access denied because of exceeding access
    # rate" even when nowhere near the documented limit (a widely-reported
    # quirk on Angel's own developer forum, not something under our
    # control) -- a brief pause here measurably reduces how often that
    # first call gets hit.
    time.sleep(1.5)
    return client


def _call_with_retry(fn, *args, max_attempts: int = 4, **kwargs):
    """Retries an Angel One API call with backoff specifically for the
    "exceeding access rate" error, which Angel's own users report happening
    intermittently even on legitimate, well-under-the-limit traffic
    (including the first call of a session). Re-raises immediately for any
    other kind of failure -- this is not a general-purpose retry, only a
    workaround for that one known-flaky error."""
    delay = 2.0
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if "exceeding access rate" not in str(e).lower() or attempt == max_attempts:
                raise
            logger.warning("Angel One rate-limit hiccup (attempt %d/%d), retrying in %.0fs: %s",
                            attempt, max_attempts, delay, e)
            time.sleep(delay)
            delay *= 2
    raise last_err


def _load_scrip_master() -> list:
    """Download (or reuse a same-day cached copy of) Angel's instrument
    master. This file is large (tens of MB) so we cache it to disk."""
    try:
        if os.path.exists(_SCRIP_MASTER_CACHE_PATH):
            age_hours = (time.time() - os.path.getmtime(_SCRIP_MASTER_CACHE_PATH)) / 3600
            if age_hours < _SCRIP_MASTER_CACHE_HOURS:
                with open(_SCRIP_MASTER_CACHE_PATH, "r") as f:
                    return json.load(f)
    except Exception as e:
        logger.warning("Could not read cached scrip master, re-downloading: %s", e)

    try:
        resp = requests.get(_SCRIP_MASTER_URL, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise DataFeedError(f"Could not download Angel One scrip master: {e}") from e

    try:
        with open(_SCRIP_MASTER_CACHE_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning("Could not cache scrip master to disk (non-fatal): %s", e)

    return data


def _parse_expiry(expiry_str: str) -> Optional[date]:
    for fmt in ("%d%b%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(expiry_str.upper(), fmt).date()
        except ValueError:
            continue
    return None


def _get_spot(client, symbol: str) -> float:
    exch, token, name = _INDEX_TOKENS[symbol]
    try:
        result = _call_with_retry(client.getMarketData, mode="LTP", exchangeTokens={exch: [token]})
        fetched = result.get("data", {}).get("fetched", [])
        if not fetched:
            raise DataFeedError(f"Angel One returned no LTP data for {symbol} index.")
        return float(fetched[0]["ltp"])
    except DataFeedError:
        raise
    except Exception as e:
        raise DataFeedError(f"Angel One spot-price fetch failed for {symbol}: {e}") from e


def _batch_quotes(client, exch: str, tokens: list) -> dict:
    """Returns {token: fetched_row} for every token successfully quoted."""
    out = {}
    for i in range(0, len(tokens), _QUOTE_BATCH_SIZE):
        chunk = tokens[i:i + _QUOTE_BATCH_SIZE]
        try:
            result = _call_with_retry(client.getMarketData, mode="FULL", exchangeTokens={exch: chunk})
        except Exception as e:
            raise DataFeedError(f"Angel One quote fetch failed: {e}") from e
        for row in result.get("data", {}).get("fetched", []):
            out[str(row.get("symbolToken"))] = row
        time.sleep(0.3)  # be gentle with the rate limit across chunks
    return out


def fetch_live_option_chain_angelone(symbol: str) -> dict:
    """
    Drop-in replacement for data.fetcher.fetch_live_option_chain_nse().
    Returns the SAME NSE-shaped dict:
        {"records": {"underlyingValue": <float>,
                      "expiryDates": [<"DD-Mon-YYYY">, ...],
                      "data": [{"expiryDate": ..., "strikePrice": ...,
                                "CE": {...}, "PE": {...}}, ...]}}
    Raises DataFeedError on any failure -- caller must stop generating new
    trades, never guess.
    """
    if symbol not in _INDEX_TOKENS:
        raise DataFeedError(f"No Angel One index-token mapping configured for {symbol}")

    client = _get_client()
    spot = _get_spot(client, symbol)

    instruments = _load_scrip_master()
    step = STRIKE_STEPS.get(symbol, 50)
    atm = round(spot / step) * step

    today = now_ist().date()  # IST date, not the runner's (UTC) local date --
    # same class of bug as above: near midnight IST this could otherwise be
    # off by a day on a UTC-clocked runner.
    candidates_by_expiry = {}
    for inst in instruments:
        try:
            if inst.get("name") != symbol or inst.get("instrumenttype") != "OPTIDX":
                continue
            expiry = _parse_expiry(inst.get("expiry", ""))
            if expiry is None or expiry < today:
                continue
            strike = float(inst.get("strike", "0")) / 100.0
            if abs(strike - atm) / step > _STRIKE_WINDOW:
                continue
            symbol_str = inst.get("symbol", "")
            opt_type = "CE" if symbol_str.endswith("CE") else ("PE" if symbol_str.endswith("PE") else None)
            if opt_type is None:
                continue
            candidates_by_expiry.setdefault(expiry, []).append({
                "token": str(inst["token"]),
                "strike": strike,
                "opt_type": opt_type,
                "exch": inst.get("exch_seg", "NFO"),
            })
        except (KeyError, ValueError, TypeError):
            continue

    if not candidates_by_expiry:
        raise DataFeedError(
            f"No live OPTIDX contracts found for {symbol} in Angel One scrip master "
            f"(near strike {atm}). The scrip master may not have refreshed, or the "
            f"instrument list format may have changed."
        )

    # Keep the nearest couple of expiries only (matches config.MAX_DAYS_TO_EXPIRY
    # window used downstream by options.selector.select_live_contract's rolling logic).
    sorted_expiries = sorted(candidates_by_expiry.keys())[:3]

    all_tokens_by_exch = {}
    for exp in sorted_expiries:
        for row in candidates_by_expiry[exp]:
            all_tokens_by_exch.setdefault(row["exch"], set()).add(row["token"])

    quotes_by_token = {}
    for exch, tokens in all_tokens_by_exch.items():
        quotes_by_token.update(_batch_quotes(client, exch, list(tokens)))

    data_rows = []
    for exp in sorted_expiries:
        exp_str = exp.strftime("%d-%b-%Y")
        by_strike = {}
        for row in candidates_by_expiry[exp]:
            q = quotes_by_token.get(row["token"])
            if q is None:
                continue
            depth = q.get("depth", {}) or {}
            buy = depth.get("buy") or [{}]
            sell = depth.get("sell") or [{}]
            leg = {
                "lastPrice": q.get("ltp", 0.0),
                "bidprice": (buy[0] or {}).get("price"),
                "askPrice": (sell[0] or {}).get("price"),
                "openInterest": q.get("opnInterest"),
                "totalTradedVolume": q.get("tradeVolume"),
            }
            entry = by_strike.setdefault(row["strike"], {"strikePrice": row["strike"], "expiryDate": exp_str})
            entry[row["opt_type"]] = leg
        data_rows.extend(by_strike.values())

    if not data_rows:
        raise DataFeedError(
            f"Angel One quotes came back empty for all {symbol} contracts near strike {atm}."
        )

    return {
        "records": {
            "underlyingValue": spot,
            "expiryDates": [e.strftime("%d-%b-%Y") for e in sorted_expiries],
            "data": data_rows,
        }
    }


def fetch_index_history_angelone(symbol: str, interval_min: int, days: int) -> "pd.DataFrame":
    """
    Fetch historical index OHLCV candles via Angel One's official historical-
    data API (getCandleData) -- NOT subject to Yahoo Finance's free-tier
    ~60-day (5m) / ~7-day (1m) history cap. Angel's own per-request limit is
    ~100 days for 5m / ~30 days for 1m, so requests for longer windows are
    split into multiple chunked calls and concatenated.

    Returns the SAME shape as data.fetcher.fetch_index_history(): a
    DataFrame indexed by tz-aware Asia/Kolkata timestamps, columns
    open/high/low/close/volume. Raises DataFeedError on failure.

    WHY THIS MATTERS: with only ~60 days of free 5-minute data, most
    strategy/EMA/timeframe combinations in a backtest see just 1-4 trades --
    not enough to tell a real edge from noise. Pulling 6-12+ months via this
    function gives the backtest far more trades to judge each strategy on.
    """
    import pandas as pd  # local import: this module is optional/lazy-loaded

    if symbol not in _INDEX_TOKENS:
        raise DataFeedError(f"No Angel One index-token mapping configured for {symbol}")
    interval = _INTERVAL_MAP.get(interval_min)
    if interval is None:
        raise DataFeedError(f"Unsupported interval {interval_min}m for Angel One historical data")

    client = _get_client()
    exch, token, _ = _INDEX_TOKENS[symbol]
    chunk_days = _CHUNK_DAYS.get(interval_min, 28)

    # CRITICAL: must use IST wall-clock time here, not datetime.now() -- on
    # GitHub Actions (and most cloud runners) the container clock is UTC,
    # which is 5.5 hours behind IST. Angel's API interprets fromdate/todate
    # as IST with no timezone conversion, so passing UTC "now" makes every
    # request look like it's asking for data from 5.5 hours in the past --
    # e.g. at 9:57 AM IST (market open ~40 min) this would ask for data as
    # of 4:27 AM IST (before market opens), silently returning only
    # YESTERDAY's last candle and making live data look permanently stale.
    end = now_ist().replace(tzinfo=None)
    start_overall = end - timedelta(days=days)

    all_rows = []
    chunk_end = end
    while chunk_end > start_overall:
        chunk_start = max(start_overall, chunk_end - timedelta(days=chunk_days))
        params = {
            "exchange": exch,
            "symboltoken": token,
            "interval": interval,
            "fromdate": chunk_start.strftime("%Y-%m-%d %H:%M"),
            "todate": chunk_end.strftime("%Y-%m-%d %H:%M"),
        }
        try:
            result = _call_with_retry(client.getCandleData, params)
        except Exception as e:
            raise DataFeedError(f"Angel One getCandleData failed for {symbol} "
                                 f"({params['fromdate']} to {params['todate']}): {e}") from e
        if not result or not result.get("status"):
            raise DataFeedError(f"Angel One getCandleData returned an error for {symbol}: {result}")
        all_rows.extend(result.get("data") or [])
        chunk_end = chunk_start
        time.sleep(0.4)  # stay well under Angel's 3-req/sec limit

    if not all_rows:
        raise DataFeedError(f"Angel One returned no historical candles for {symbol} at {interval_min}m "
                             f"over the last {days} days.")

    df = pd.DataFrame(all_rows, columns=["datetime", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.drop_duplicates(subset="datetime").sort_values("datetime").set_index("datetime")
    if df.index.tz is None:
        df.index = df.index.tz_localize("Asia/Kolkata")
    else:
        df.index = df.index.tz_convert("Asia/Kolkata")
    return df[["open", "high", "low", "close", "volume"]]
