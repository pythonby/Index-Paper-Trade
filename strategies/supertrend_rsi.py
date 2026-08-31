"""
strategies/supertrend_rsi.py
==============================
Combo 6 -- SuperTrend + RSI.

SuperTrend flipping direction is the entry trigger. RSI is used as a
MOMENTUM FILTER here (just checking which side of 50 it's on), not an
extreme-value trigger -- this matches SuperTrend's steadier signal
frequency. Using RSI extremes (like 70/30) here would rarely coincide
with a SuperTrend flip, since SuperTrend flips are less frequent events.
"""

from strategies.base import Strategy, Signal


class SupertrendRsi(Strategy):
    name = "supertrend_rsi"
    preferred_regimes = {"strong_bullish", "strong_bearish"}
    market_type = "trending"
    speed = "slow"   # SuperTrend is a slower, steadier trend-following indicator

    def generate_signal(self, df, i: int):
        if i < 5:
            return None

        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        st_dir = row.get("supertrend_dir")
        prev_st_dir = prev_row.get("supertrend_dir")
        rsi_val = row.get("rsi")

        if any(v is None or v != v for v in [st_dir, prev_st_dir, rsi_val]):
            return None

        close = row["close"]
        open_ = row["open"]

        flipped_bullish = prev_st_dir == -1 and st_dir == 1
        flipped_bearish = prev_st_dir == 1 and st_dir == -1

        if flipped_bullish and rsi_val > 50 and close > open_:
            return Signal(
                strategy=self.name,
                direction="CE",
                reasons=[
                    "SuperTrend flipped bullish",
                    f"RSI momentum filter confirms bullish ({rsi_val:.1f} > 50)",
                    "Bullish confirmation candle",
                ],
                score_components={"trend": 1.0, "momentum": min((rsi_val - 50) / 30, 1.0), "ema_alignment": 0.7},
            )

        if flipped_bearish and rsi_val < 50 and close < open_:
            return Signal(
                strategy=self.name,
                direction="PE",
                reasons=[
                    "SuperTrend flipped bearish",
                    f"RSI momentum filter confirms bearish ({rsi_val:.1f} < 50)",
                    "Bearish confirmation candle",
                ],
                score_components={"trend": 1.0, "momentum": min((50 - rsi_val) / 30, 1.0), "ema_alignment": 0.7},
            )

        return None
