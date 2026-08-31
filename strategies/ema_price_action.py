"""
strategies/ema_price_action.py
================================
Combo 8 -- EMA + Price Action.

EMA fast/slow alignment defines the trend direction; a recognizable
candlestick price-action pattern (engulfing or hammer/shooting-star) on
the current bar is the entry trigger. Both are checked once per bar, so
they're naturally frequency-compatible -- no lag mismatch between them.
"""

from strategies.base import Strategy, Signal


class EmaPriceAction(Strategy):
    name = "ema_price_action"
    preferred_regimes = {"strong_bullish", "strong_bearish"}
    market_type = "trending"
    speed = "slow"

    def generate_signal(self, df, i: int):
        if i < 10:
            return None

        row = df.iloc[i]

        ema_fast = row.get("ema_fast")
        ema_slow = row.get("ema_slow")
        bullish_engulf = row.get("bullish_engulfing")
        bearish_engulf = row.get("bearish_engulfing")
        hammer = row.get("hammer")
        shooting_star = row.get("shooting_star")

        if any(v is None or v != v for v in [ema_fast, ema_slow]):
            return None

        uptrend = ema_fast > ema_slow
        downtrend = ema_fast < ema_slow

        if uptrend and (bullish_engulf or hammer):
            pattern = "bullish engulfing" if bullish_engulf else "hammer"
            return Signal(
                strategy=self.name,
                direction="CE",
                reasons=[
                    "EMA uptrend confirmed (fast > slow)",
                    f"Bullish price-action pattern: {pattern}",
                ],
                score_components={"trend": 1.0, "ema_alignment": 1.0, "momentum": 0.6, "vwap": 0.5},
            )

        if downtrend and (bearish_engulf or shooting_star):
            pattern = "bearish engulfing" if bearish_engulf else "shooting star"
            return Signal(
                strategy=self.name,
                direction="PE",
                reasons=[
                    "EMA downtrend confirmed (fast < slow)",
                    f"Bearish price-action pattern: {pattern}",
                ],
                score_components={"trend": 1.0, "ema_alignment": 1.0, "momentum": 0.6, "vwap": 0.5},
            )

        return None
