"""
strategies/mean_reversion.py
=============================
Strategy D -- Bollinger Band Mean Reversion.

Designed specifically for SIDEWAYS / range-bound markets, unlike the other
three strategies which all require a trend. This strategy is intentionally
disabled by the regime detector whenever the market is trending -- taking
mean-reversion trades in a strong trend is a classic way to lose money
("fading the trend"), so it only ever fires in the 'sideways' regime.

CE: price touches/dips below the lower Bollinger Band (oversold extreme),
    then closes back above it with RSI oversold and a bullish confirmation
    candle -- betting on a bounce back toward the mean.
PE: mirror image at the upper band (overbought extreme).
"""

from strategies.base import Strategy, Signal


class MeanReversion(Strategy):
    name = "mean_reversion"
    preferred_regimes = {"sideways"}   # the ONLY strategy that wants sideways markets

    def __init__(self, rsi_oversold: float = 35, rsi_overbought: float = 65):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def generate_signal(self, df, i: int):
        if i < 2:
            return None

        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        bb_upper = row.get("bb_upper")
        bb_lower = row.get("bb_lower")
        bb_mid = row.get("bb_mid")
        rsi_val = row.get("rsi")

        if any(v is None or v != v for v in [bb_upper, bb_lower, bb_mid, rsi_val]):
            return None

        close = row["close"]
        open_ = row["open"]
        low = row["low"]
        high = row["high"]
        prev_low = prev_row["low"]
        prev_high = prev_row["high"]

        bullish_candle = close > open_
        bearish_candle = close < open_

        # CE: touched/pierced the lower band recently, now closing back above it
        touched_lower = (low <= bb_lower) or (prev_low <= bb_lower)
        if (touched_lower and close > bb_lower and rsi_val < self.rsi_oversold
                and bullish_candle):
            reasons = [
                f"Price touched/pierced lower Bollinger Band ({bb_lower:.1f}) -- oversold extreme",
                f"RSI oversold ({rsi_val:.1f} < {self.rsi_oversold})",
                "Bullish confirmation candle closing back above the band",
                "Market regime is sideways -- mean-reversion setup, not a trend trade",
            ]
            band_width = max(bb_upper - bb_lower, 1e-6)
            return Signal(
                strategy=self.name,
                direction="CE",
                reasons=reasons,
                score_components={
                    "trend": 0.5,  # mean reversion isn't a trend play, kept neutral
                    "vwap": 0.5,
                    "ema_alignment": 0.5,
                    "momentum": min((self.rsi_oversold - rsi_val) / self.rsi_oversold, 1.0),
                    "breakout_quality": min((bb_lower - low) / band_width, 1.0),
                },
            )

        # PE: touched/pierced the upper band recently, now closing back below it
        touched_upper = (high >= bb_upper) or (prev_high >= bb_upper)
        if (touched_upper and close < bb_upper and rsi_val > self.rsi_overbought
                and bearish_candle):
            reasons = [
                f"Price touched/pierced upper Bollinger Band ({bb_upper:.1f}) -- overbought extreme",
                f"RSI overbought ({rsi_val:.1f} > {self.rsi_overbought})",
                "Bearish confirmation candle closing back below the band",
                "Market regime is sideways -- mean-reversion setup, not a trend trade",
            ]
            band_width = max(bb_upper - bb_lower, 1e-6)
            return Signal(
                strategy=self.name,
                direction="PE",
                reasons=reasons,
                score_components={
                    "trend": 0.5,
                    "vwap": 0.5,
                    "ema_alignment": 0.5,
                    "momentum": min((rsi_val - self.rsi_overbought) / (100 - self.rsi_overbought), 1.0),
                    "breakout_quality": min((high - bb_upper) / band_width, 1.0),
                },
            )

        return None
