"""
indicators/__init__.py
=======================
Pure pandas indicator implementations. No TA-Lib dependency required
(TA-Lib is annoying to install on Windows without a compiler / wheel),
so everything here is hand-rolled and vectorized with pandas/numpy.

All functions take a DataFrame with columns: open, high, low, close, volume
indexed by timestamp, and are careful not to use any future information
(no look-ahead bias) -- every value at row i only uses rows <= i.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Session-anchored VWAP. Resets at the start of each trading day.
    Requires df.index to be a DatetimeIndex.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    tpv = typical_price * df["volume"]

    day = df.index.date
    cum_tpv = pd.Series(tpv, index=df.index).groupby(day).cumsum()
    cum_vol = pd.Series(df["volume"], index=df.index).groupby(day).cumsum()

    return cum_tpv / cum_vol.replace(0, np.nan)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def volume_avg(df: pd.DataFrame, window: int = 20) -> pd.Series:
    return df["volume"].rolling(window=window).mean()


def realized_volatility(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Annualized realized volatility from log returns, using intraday bars."""
    log_ret = np.log(df["close"] / df["close"].shift(1))
    # crude annualization assuming ~375 one-minute bars/day * 252 days scaled to bar frequency
    bars_per_day = 375
    return log_ret.rolling(window=window).std() * np.sqrt(bars_per_day * 252)


def is_bullish_candle(df: pd.DataFrame) -> pd.Series:
    return df["close"] > df["open"]


def is_bearish_candle(df: pd.DataFrame) -> pd.Series:
    return df["close"] < df["open"]


def opening_range(df: pd.DataFrame, minutes: int = 15):
    """
    Returns per-day (high, low) of the opening range, forward-filled for the rest
    of that day only (never using future days), keyed by the bar timestamp.
    Assumes df is intraday bars for potentially multiple days.
    """
    df = df.copy()
    df["date"] = df.index.date
    df["minute_of_day"] = (df.index.hour * 60 + df.index.minute)

    results_high = pd.Series(index=df.index, dtype=float)
    results_low = pd.Series(index=df.index, dtype=float)

    market_open_minutes = 9 * 60 + 15
    range_end_minutes = market_open_minutes + minutes

    for day, day_df in df.groupby("date"):
        or_mask = (day_df["minute_of_day"] >= market_open_minutes) & (day_df["minute_of_day"] < range_end_minutes)
        or_bars = day_df[or_mask]
        if or_bars.empty:
            continue
        or_high = or_bars["high"].max()
        or_low = or_bars["low"].min()
        results_high.loc[day_df.index] = or_high
        results_low.loc[day_df.index] = or_low

    return results_high, results_low
