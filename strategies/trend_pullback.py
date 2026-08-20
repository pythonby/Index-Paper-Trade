"""
strategies/trend_pullback.py
=============================
Strategy C -- Trend Pullback.

Identifies the dominant intraday trend using EMA slope + VWAP position,
waits for price to pull back toward VWAP/fast EMA, then enters on a
confirmation candle in the direction of the dominant trend. Counter-trend
trades are explicitly never taken.
"""

from strategies.base import Strategy, Signal


class TrendPullback(Strategy):
    name = "trend_pullback"
    preferred_regimes = {"strong_bullish", "strong_bearish"}

    def __init__(self, pullback_tolerance_pct: float = 0.0015):
        # how close price must come to VWAP/EMA to count as a "pullback"
        self.pullback_tolerance_pct = pullback_tolerance_pct

    def generate_signal(self, df, i: int):
        if i < 20:
            return None

        row = df.iloc[i]
        prev_row = df.iloc[i - 1]
        trend_window = df.iloc[i - 20:i]  # excludes current bar

        vwap_val = row.get("vwap")
        ema_fast = row.get("ema_fast")
        ema_slow = row.get("ema_slow")

        if any(v is None or v != v for v in [vwap_val, ema_fast, ema_slow]):
            return None

        close = row["close"]
        open_ = row["open"]
        prev_close = prev_row["close"]

        # Dominant trend: EMA slope over the trailing window + EMA alignment
        ema_fast_slope = trend_window["ema_fast"].iloc[-1] - trend_window["ema_fast"].iloc[0]
        uptrend = ema_fast > ema_slow and ema_fast_slope > 0 and close > vwap_val
        downtrend = ema_fast < ema_slow and ema_fast_slope < 0 and close < vwap_val

        if not uptrend and not downtrend:
            return None  # sideways / unclear -> no trade, never counter-trend

        near_vwap = abs(close - vwap_val) / vwap_val <= self.pullback_tolerance_pct
        near_ema_fast = abs(close - ema_fast) / ema_fast <= self.pullback_tolerance_pct
        pulled_back = near_vwap or near_ema_fast

        if uptrend and pulled_back:
            # confirmation: bullish candle resuming the trend after touching support
            bullish_confirmation = close > open_ and close > prev_close
            if bullish_confirmation:
                reasons = [
                    "Dominant intraday trend is bullish (EMA fast > slow, rising, price > VWAP)",
                    "Price pulled back to VWAP/fast EMA (support zone)",
                    "Bullish confirmation candle resuming the uptrend",
                    "No counter-trend trade taken",
                ]
                return Signal(
                    strategy=self.name,
                    direction="CE",
                    reasons=reasons,
                    score_components={
                        "trend": 1.0,
                        "vwap": 1.0 if near_vwap else 0.6,
                        "ema_alignment": 1.0,
                        "momentum": 0.7,
                    },
                )

        if downtrend and pulled_back:
            bearish_confirmation = close < open_ and close < prev_close
            if bearish_confirmation:
                reasons = [
                    "Dominant intraday trend is bearish (EMA fast < slow, falling, price < VWAP)",
                    "Price pulled back to VWAP/fast EMA (resistance zone)",
                    "Bearish confirmation candle resuming the downtrend",
                    "No counter-trend trade taken",
                ]
                return Signal(
                    strategy=self.name,
                    direction="PE",
                    reasons=reasons,
                    score_components={
                        "trend": 1.0,
                        "vwap": 1.0 if near_vwap else 0.6,
                        "ema_alignment": 1.0,
                        "momentum": 0.7,
                    },
                )

        return None
