"""
strategies/opening_range_breakout.py
=====================================
Strategy B -- Opening Range Breakout (ORB).

Defines the first N minutes' high/low as the opening range. A confirmed
close beyond that range, with above-average volume and same-direction
trend (EMA alignment), triggers a signal. Explicitly avoids trades where
the breakout candle immediately reverses (checked by requiring the CLOSE,
not just the high/low wick, to be beyond the range).
"""

from strategies.base import Strategy, Signal


class OpeningRangeBreakout(Strategy):
    name = "opening_range_breakout"
    preferred_regimes = {"strong_bullish", "strong_bearish", "high_volatility"}

    def __init__(self, range_minutes: int = 15, volume_mult: float = 1.2):
        self.range_minutes = range_minutes
        self.volume_mult = volume_mult

    def generate_signal(self, df, i: int):
        if i < 2:
            return None

        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        or_high = row.get(f"or_high_{self.range_minutes}")
        or_low = row.get(f"or_low_{self.range_minutes}")
        avg_vol = row.get("volume_avg")
        ema_fast = row.get("ema_fast")
        ema_slow = row.get("ema_slow")

        if any(v is None or v != v for v in [or_high, or_low, avg_vol, ema_fast, ema_slow]):
            return None

        # Don't trade the opening range itself -- only bars after it has formed and
        # price has since moved beyond it.
        minute_of_day = row.name.hour * 60 + row.name.minute
        range_end_minute = 9 * 60 + 15 + self.range_minutes
        if minute_of_day < range_end_minute:
            return None

        close = row["close"]
        prev_close = prev_row["close"]
        volume = row["volume"]

        volume_confirmed = volume > avg_vol * self.volume_mult

        # Require the breakout to hold: previous bar's close was already beyond
        # the range too (avoids single-candle spike reversals), i.e. two
        # consecutive confirmed closes beyond the range.
        if (close > or_high and prev_close > or_high and volume_confirmed
                and ema_fast > ema_slow):
            reasons = [
                f"Price closed above {self.range_minutes}-min opening range high ({or_high:.1f})",
                "Two consecutive closes above the range (avoids immediate reversal)",
                "Volume above average, confirming breakout conviction",
                "EMA trend alignment bullish",
            ]
            return Signal(
                strategy=self.name,
                direction="CE",
                reasons=reasons,
                score_components={
                    "trend": 1.0,
                    "breakout_quality": min((close - or_high) / max(or_high - or_low, 1e-6), 1.0),
                    "volume": min(volume / (avg_vol * self.volume_mult), 2.0) / 2.0,
                },
            )

        if (close < or_low and prev_close < or_low and volume_confirmed
                and ema_fast < ema_slow):
            reasons = [
                f"Price closed below {self.range_minutes}-min opening range low ({or_low:.1f})",
                "Two consecutive closes below the range (avoids immediate reversal)",
                "Volume above average, confirming breakdown conviction",
                "EMA trend alignment bearish",
            ]
            return Signal(
                strategy=self.name,
                direction="PE",
                reasons=reasons,
                score_components={
                    "trend": 1.0,
                    "breakout_quality": min((or_low - close) / max(or_high - or_low, 1e-6), 1.0),
                    "volume": min(volume / (avg_vol * self.volume_mult), 2.0) / 2.0,
                },
            )

        return None
