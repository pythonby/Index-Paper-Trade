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

    IMPORTANT: NSE index tickers (e.g. ^NSEI, ^NSEBANK) are not tradeable
    instruments, so Yahoo Finance reports volume=0 for every bar. A true
    volume-weighted average is undefined when cumulative volume is 0, so
    we fall back to a simple (unweighted) cumulative average of the
    typical price for any day where volume is entirely zero. This keeps
    VWAP-dependent strategies functional on index data instead of
    silently producing NaN for the whole session.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    tpv = typical_price * df["volume"]

    day = df.index.date
    cum_tpv = pd.Series(tpv, index=df.index).groupby(day).cumsum()
    cum_vol = pd.Series(df["volume"], index=df.index).groupby(day).cumsum()
    cum_count = pd.Series(1, index=df.index).groupby(day).cumsum()
    cum_typical = pd.Series(typical_price, index=df.index).groupby(day).cumsum()

    weighted_vwap = cum_tpv / cum_vol.replace(0, np.nan)
    unweighted_fallback = cum_typical / cum_count

    return weighted_vwap.where(cum_vol > 0, unweighted_fallback)


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


def bollinger_bands(series: pd.Series, window: int = 20, num_std: float = 2.0):
    """Returns (upper, mid, lower) bands. Used by the mean-reversion strategy
    for sideways/range-bound markets."""
    mid = series.rolling(window=window).mean()
    std = series.rolling(window=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def nadaraya_watson_envelope(series: pd.Series, bandwidth: float = 8.0, window: int = 120, mult: float = 2.5):
    """
    Causal (non-repainting) Nadaraya-Watson kernel-regression envelope.

    This is a smoother, more adaptive alternative to a simple moving average
    for identifying dynamic support/resistance "zones". At each bar, only
    bars up to and including that bar are used (never future bars), so this
    is safe for backtesting without look-ahead bias -- unlike many popular
    NW-envelope implementations (e.g. on TradingView) which use the whole
    dataset and "repaint" historical values as new bars arrive.

    bandwidth: controls how smooth the curve is (larger = smoother/slower).
    window: how many trailing bars feed into each point's estimate (larger
            = slower to compute, marginally smoother edges).
    mult: how many mean-absolute-deviations wide the upper/lower bands are.

    Returns (nw_mean, upper_band, lower_band) as pd.Series.
    """
    values = series.values.astype(float)
    n = len(values)
    nw = np.full(n, np.nan)

    for i in range(n):
        start = max(0, i - window + 1)
        span = i - start + 1
        # Gaussian kernel weights: distance 0 (current bar) gets weight 1,
        # older bars in the window get exponentially less weight.
        distances = np.arange(span - 1, -1, -1)
        weights = np.exp(-(distances ** 2) / (2 * bandwidth ** 2))
        weights /= weights.sum()
        nw[i] = np.dot(weights, values[start:i + 1])

    nw_series = pd.Series(nw, index=series.index)
    residual = (series - nw_series).abs()
    mad = residual.rolling(window=window, min_periods=5).mean()

    upper = nw_series + mult * mad
    lower = nw_series - mult * mad
    return nw_series, upper, lower


def adx(df: pd.DataFrame, period: int = 14):
    """
    Average Directional Index (Wilder's method) -- measures TREND STRENGTH
    (not direction). Returns (adx, plus_di, minus_di). ADX > ~25 is
    generally considered "trending"; below that, "weak/no trend".
    Deliberately paired with slower trend indicators (EMA, SuperTrend)
    rather than fast oscillators, since ADX itself changes slowly.
    """
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat([
        (high - low),
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_ = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx_, plus_di, minus_di


def parabolic_sar(df: pd.DataFrame, af_step: float = 0.02, af_max: float = 0.2):
    """
    Classic Parabolic SAR -- a fast-flipping trend/reversal indicator (the
    dot flips above/below price when the trend reverses). Paired with RSI
    (also fast-reacting) for reversal-confirmation strategies, since both
    react on a similar bar-to-bar timescale -- pairing PSAR with a slow
    indicator like ADX would rarely have both trigger together.

    Returns (sar, trend) where trend is +1 (bullish, dot below price) or
    -1 (bearish, dot above price). Causal, single forward pass.
    """
    high = df["high"].values
    low = df["low"].values
    n = len(df)

    sar = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    ep = np.zeros(n)
    af = np.zeros(n)

    trend[0] = 1
    sar[0] = low[0]
    ep[0] = high[0]
    af[0] = af_step

    for i in range(1, n):
        prev_sar, prev_trend, prev_ep, prev_af = sar[i - 1], trend[i - 1], ep[i - 1], af[i - 1]
        candidate_sar = prev_sar + prev_af * (prev_ep - prev_sar)

        if prev_trend == 1:
            candidate_sar = min(candidate_sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < candidate_sar:
                trend[i], sar[i], ep[i], af[i] = -1, prev_ep, low[i], af_step
            else:
                trend[i], sar[i] = 1, candidate_sar
                if high[i] > prev_ep:
                    ep[i], af[i] = high[i], min(prev_af + af_step, af_max)
                else:
                    ep[i], af[i] = prev_ep, prev_af
        else:
            candidate_sar = max(candidate_sar, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > candidate_sar:
                trend[i], sar[i], ep[i], af[i] = 1, prev_ep, high[i], af_step
            else:
                trend[i], sar[i] = -1, candidate_sar
                if low[i] < prev_ep:
                    ep[i], af[i] = low[i], min(prev_af + af_step, af_max)
                else:
                    ep[i], af[i] = prev_ep, prev_af

    return pd.Series(sar, index=df.index), pd.Series(trend, index=df.index)


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """
    SuperTrend -- an ATR-based trend-following band that flips direction
    when price closes through it. Slower/steadier than PSAR, so it's
    paired with RSI used as a MOMENTUM FILTER (checking RSI is above/below
    50) rather than RSI extremes, matching SuperTrend's steadier signal
    frequency instead of RSI's fast oscillation.

    Returns (supertrend_line, direction) where direction is +1 (bullish)
    or -1 (bearish). Causal, single forward pass.
    """
    atr_ = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    n = len(df)

    final_upper = (hl2 + multiplier * atr_).values.copy()
    final_lower = (hl2 - multiplier * atr_).values.copy()
    close = df["close"].values

    st = np.zeros(n)
    direction = np.zeros(n, dtype=int)

    first_valid = np.argmax(~np.isnan(final_upper))
    for i in range(n):
        if i <= first_valid or np.isnan(final_upper[i]):
            direction[i] = 1
            st[i] = final_lower[i] if not np.isnan(final_lower[i]) else close[i]
            continue

        if not (final_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]):
            final_upper[i] = final_upper[i - 1]
        if not (final_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]):
            final_lower[i] = final_lower[i - 1]

        if st[i - 1] == final_upper[i - 1] and close[i] <= final_upper[i]:
            direction[i], st[i] = -1, final_upper[i]
        elif st[i - 1] == final_upper[i - 1] and close[i] > final_upper[i]:
            direction[i], st[i] = 1, final_lower[i]
        elif st[i - 1] == final_lower[i - 1] and close[i] >= final_lower[i]:
            direction[i], st[i] = 1, final_lower[i]
        elif st[i - 1] == final_lower[i - 1] and close[i] < final_lower[i]:
            direction[i], st[i] = -1, final_upper[i]
        else:
            direction[i], st[i] = direction[i - 1], final_lower[i] if direction[i - 1] == 1 else final_upper[i]

    return pd.Series(st, index=df.index), pd.Series(direction, index=df.index)


def is_bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Fast, single-bar-reacting price-action pattern -- pairs well with
    EMA (checked once per bar, same frequency) rather than a slow filter."""
    prev_open, prev_close = df["open"].shift(1), df["close"].shift(1)
    return ((df["close"] > df["open"]) & (prev_close < prev_open) &
            (df["close"] >= prev_open) & (df["open"] <= prev_close))


def is_bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    prev_open, prev_close = df["open"].shift(1), df["close"].shift(1)
    return ((df["close"] < df["open"]) & (prev_close > prev_open) &
            (df["close"] <= prev_open) & (df["open"] >= prev_close))


def is_hammer(df: pd.DataFrame) -> pd.Series:
    body = (df["close"] - df["open"]).abs()
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    return (lower_wick > 2 * body) & (upper_wick < body)


def is_shooting_star(df: pd.DataFrame) -> pd.Series:
    body = (df["close"] - df["open"]).abs()
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    return (upper_wick > 2 * body) & (lower_wick < body)


def swing_points(df: pd.DataFrame, lookback: int = 3):
    """
    Causal swing high/low detection with confirmation lag: a bar at index
    i-lookback is confirmed as a swing low/high only once `lookback` bars
    have passed AFTER it (so we never use future bars relative to "now",
    just a delayed confirmation of a past point -- safe for backtesting).
    Used by the trendline strategy to anchor trendlines on confirmed points.

    Returns (swing_low_val, swing_low_idx_offset, swing_high_val,
    swing_high_idx_offset) as Series -- value and "how many bars ago" it
    formed, aligned to the bar where it becomes CONFIRMED (not when it happened).
    """
    n = len(df)
    low = df["low"].values
    high = df["high"].values

    swing_low_val = np.full(n, np.nan)
    swing_low_age = np.full(n, np.nan)
    swing_high_val = np.full(n, np.nan)
    swing_high_age = np.full(n, np.nan)

    for i in range(2 * lookback, n):
        center = i - lookback
        window_low = low[center - lookback:center + lookback + 1]
        window_high = high[center - lookback:center + lookback + 1]
        if low[center] == window_low.min():
            swing_low_val[i] = low[center]
            swing_low_age[i] = lookback
        if high[center] == window_high.max():
            swing_high_val[i] = high[center]
            swing_high_age[i] = lookback

    idx = df.index
    return (pd.Series(swing_low_val, index=idx), pd.Series(swing_low_age, index=idx),
            pd.Series(swing_high_val, index=idx), pd.Series(swing_high_age, index=idx))


def adx(df: pd.DataFrame, period: int = 14):
    """Returns (adx, plus_di, minus_di) using Wilder's smoothing."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx_val, plus_di, minus_di


def parabolic_sar(df: pd.DataFrame, af_step: float = 0.02, af_max: float = 0.2):
    """Returns (sar, trend) where trend is +1 (bullish/price above SAR) or -1 (bearish).
    Causal single-pass implementation -- standard Wilder PSAR algorithm."""
    high = df["high"].values
    low = df["low"].values
    n = len(df)
    sar = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    ep = np.zeros(n)
    af = np.zeros(n)

    trend[0] = 1
    sar[0] = low[0]
    ep[0] = high[0]
    af[0] = af_step

    for i in range(1, n):
        prev_sar, prev_trend, prev_ep, prev_af = sar[i - 1], trend[i - 1], ep[i - 1], af[i - 1]
        candidate_sar = prev_sar + prev_af * (prev_ep - prev_sar)

        if prev_trend == 1:
            candidate_sar = min(candidate_sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < candidate_sar:
                trend[i], sar[i], ep[i], af[i] = -1, prev_ep, low[i], af_step
            else:
                trend[i], sar[i] = 1, candidate_sar
                if high[i] > prev_ep:
                    ep[i], af[i] = high[i], min(prev_af + af_step, af_max)
                else:
                    ep[i], af[i] = prev_ep, prev_af
        else:
            candidate_sar = max(candidate_sar, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > candidate_sar:
                trend[i], sar[i], ep[i], af[i] = 1, prev_ep, high[i], af_step
            else:
                trend[i], sar[i] = -1, candidate_sar
                if low[i] < prev_ep:
                    ep[i], af[i] = low[i], min(prev_af + af_step, af_max)
                else:
                    ep[i], af[i] = prev_ep, prev_af

    return pd.Series(sar, index=df.index), pd.Series(trend, index=df.index)


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """Returns (supertrend_line, direction) where direction is +1 (bullish) or -1 (bearish).
    NaN for both outputs until the ATR warmup period (first `period` bars) has
    passed -- trying to seed state during the ATR-NaN warmup previously caused
    the whole series to get permanently stuck in a wrong direction."""
    atr_val = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upper_band = (hl2 + multiplier * atr_val).values
    lower_band = (hl2 - multiplier * atr_val).values
    close = df["close"].values
    n = len(df)

    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    st = np.full(n, np.nan)
    direction = np.full(n, np.nan)

    # Find the first bar where ATR (and therefore the bands) are valid --
    # everything before this is left as NaN rather than seeded with garbage.
    valid_mask = ~np.isnan(upper_band) & ~np.isnan(lower_band)
    if not valid_mask.any():
        return pd.Series(st, index=df.index), pd.Series(direction, index=df.index)
    start = np.argmax(valid_mask)

    direction[start] = 1
    st[start] = final_lower[start]

    for i in range(start + 1, n):
        if not (final_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]):
            final_upper[i] = final_upper[i - 1]
        if not (final_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]):
            final_lower[i] = final_lower[i - 1]

        if st[i - 1] == final_upper[i - 1]:
            if close[i] <= final_upper[i]:
                direction[i], st[i] = -1, final_upper[i]
            else:
                direction[i], st[i] = 1, final_lower[i]
        else:
            if close[i] >= final_lower[i]:
                direction[i], st[i] = 1, final_lower[i]
            else:
                direction[i], st[i] = -1, final_upper[i]

    return pd.Series(st, index=df.index), pd.Series(direction, index=df.index)


def is_bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    prev_open = df["open"].shift(1)
    prev_close = df["close"].shift(1)
    return ((df["close"] > df["open"]) & (prev_close < prev_open) &
            (df["close"] >= prev_open) & (df["open"] <= prev_close))


def is_bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    prev_open = df["open"].shift(1)
    prev_close = df["close"].shift(1)
    return ((df["close"] < df["open"]) & (prev_close > prev_open) &
            (df["close"] <= prev_open) & (df["open"] >= prev_close))


def is_hammer(df: pd.DataFrame) -> pd.Series:
    body = (df["close"] - df["open"]).abs()
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    return (lower_wick > 2 * body) & (upper_wick < body)


def is_shooting_star(df: pd.DataFrame) -> pd.Series:
    body = (df["close"] - df["open"]).abs()
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    return (upper_wick > 2 * body) & (lower_wick < body)


def swing_points(df: pd.DataFrame, lookback: int = 3):
    """
    Causal swing high/low detection: a bar at index i-lookback is confirmed
    as a swing low/high once `lookback` bars have passed on both sides with
    higher/lower values. Because confirmation needs `lookback` future bars,
    the swing is only marked at the CURRENT bar once confirmed -- so no
    look-ahead: at bar i we only know about swings confirmed at bars <= i.

    Returns 4 Series: (swing_low_val, swing_low_age, swing_high_val, swing_high_age).
    "val" is the swing price; "age" is how many bars ago (from the
    confirmation bar) the swing candle actually occurred -- always equal to
    `lookback` by construction, stored explicitly so callers can recover the
    original bar index without hard-coding the lookback value themselves.
    NaN everywhere no swing is confirmed on that bar.
    """
    n = len(df)
    low = df["low"].values
    high = df["high"].values
    swing_low_val = np.full(n, np.nan)
    swing_low_age = np.full(n, np.nan)
    swing_high_val = np.full(n, np.nan)
    swing_high_age = np.full(n, np.nan)

    for i in range(2 * lookback, n):
        center = i - lookback
        window_low = low[center - lookback:center + lookback + 1]
        window_high = high[center - lookback:center + lookback + 1]
        if low[center] == window_low.min():
            swing_low_val[i] = low[center]
            swing_low_age[i] = lookback
        if high[center] == window_high.max():
            swing_high_val[i] = high[center]
            swing_high_age[i] = lookback

    idx = df.index
    return (pd.Series(swing_low_val, index=idx), pd.Series(swing_low_age, index=idx),
            pd.Series(swing_high_val, index=idx), pd.Series(swing_high_age, index=idx))


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
