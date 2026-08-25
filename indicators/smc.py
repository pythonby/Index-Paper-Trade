"""
indicators/smc.py
==================
Lightweight Smart Money Concepts (SMC) zone detection: Order Blocks (OB)
and Fair Value Gaps (FVG). These are used as dynamic "institutional"
entry zones, restricted to 15-minute and higher timeframes -- on very
short timeframes (1m/5m) these patterns tend to be noisy micro-gaps that
don't hold as meaningful zones, which is why this module is only wired
into the strategy for timeframe_min >= config.SMC_MIN_TIMEFRAME_MIN.

Both functions are single-pass, causal (no look-ahead): each bar's zone
value only depends on bars at or before that index.

NOTE: this is a simplified, practical approximation of full SMC theory
(which also includes concepts like liquidity sweeps, break of structure,
change of character, premium/discount zones, etc). Implementing the full
theory is a much larger undertaking; this covers the two most commonly
traded zone types (order blocks and fair value gaps) in a way that's
fast to compute and safe for backtesting.
"""

import numpy as np
import pandas as pd


def order_blocks(df: pd.DataFrame):
    """
    Simplified order block detection: the most recent OPPOSITE-colored
    candle is treated as a zone (a bearish candle marks a potential demand
    zone, a bullish candle marks a potential supply zone). The zone stays
    "active" until price CLOSES through it (i.e. the zone is invalidated),
    at which point tracking resets until the next opposite candle forms.

    Returns 4 Series: bull_ob_low, bull_ob_high (demand zone bounds),
    bear_ob_low, bear_ob_high (supply zone bounds). NaN where no zone is
    currently active.
    """
    n = len(df)
    opens = df["open"].values
    closes = df["close"].values
    lows = df["low"].values
    highs = df["high"].values

    bull_ob_low = np.full(n, np.nan)
    bull_ob_high = np.full(n, np.nan)
    bear_ob_low = np.full(n, np.nan)
    bear_ob_high = np.full(n, np.nan)

    cur_bull_low, cur_bull_high = np.nan, np.nan   # demand zone (from last bearish candle)
    cur_bear_low, cur_bear_high = np.nan, np.nan   # supply zone (from last bullish candle)

    for i in range(n):
        # Record the zone as it stood BEFORE this bar (so a bar can't use
        # a zone formed by itself -- avoids any same-bar look-ahead).
        bull_ob_low[i] = cur_bull_low
        bull_ob_high[i] = cur_bull_high
        bear_ob_low[i] = cur_bear_low
        bear_ob_high[i] = cur_bear_high

        # Invalidate zones this bar's CLOSE has traded through.
        if not np.isnan(cur_bull_low) and closes[i] < cur_bull_low:
            cur_bull_low, cur_bull_high = np.nan, np.nan
        if not np.isnan(cur_bear_high) and closes[i] > cur_bear_high:
            cur_bear_low, cur_bear_high = np.nan, np.nan

        # Update trailing state with this bar's candle, for future bars.
        if closes[i] < opens[i]:
            cur_bull_low, cur_bull_high = lows[i], highs[i]
        elif closes[i] > opens[i]:
            cur_bear_low, cur_bear_high = lows[i], highs[i]

    idx = df.index
    return (pd.Series(bull_ob_low, index=idx), pd.Series(bull_ob_high, index=idx),
            pd.Series(bear_ob_low, index=idx), pd.Series(bear_ob_high, index=idx))


def fair_value_gaps(df: pd.DataFrame):
    """
    Classic 3-candle Fair Value Gap detection.
    Bullish FVG (formed at bar i, using candles i-2, i-1, i):
        candle[i-2].high < candle[i].low  -> imbalance zone [c[i-2].high, c[i].low]
    Bearish FVG:
        candle[i-2].low > candle[i].high  -> imbalance zone [c[i].high, c[i-2].low]

    Returns 4 Series (bull_fvg_low, bull_fvg_high, bear_fvg_low, bear_fvg_high),
    with a value ONLY on the bar where that gap formed (NaN elsewhere) --
    the calling strategy looks back a short window to find the most recent
    one, so this stays cheap to compute (single vectorized-ish pass).
    """
    n = len(df)
    high = df["high"].values
    low = df["low"].values

    bull_low = np.full(n, np.nan)
    bull_high = np.full(n, np.nan)
    bear_low = np.full(n, np.nan)
    bear_high = np.full(n, np.nan)

    for i in range(2, n):
        if high[i - 2] < low[i]:
            bull_low[i] = high[i - 2]
            bull_high[i] = low[i]
        elif low[i - 2] > high[i]:
            bear_low[i] = high[i]
            bear_high[i] = low[i - 2]

    idx = df.index
    return (pd.Series(bull_low, index=idx), pd.Series(bull_high, index=idx),
            pd.Series(bear_low, index=idx), pd.Series(bear_high, index=idx))
