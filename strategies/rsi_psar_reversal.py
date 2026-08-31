"""
strategies/rsi_psar_reversal.py
=================================
Combo 5 -- RSI + Parabolic SAR reversal.

PSAR flipping sides (dot moves from above to below price, or vice versa)
signals a potential trend reversal. RSI at an extreme (oversold/overbought)
at the same time adds confirmation that the reversal has momentum behind
it. Both indicators react on a similar fast, per-bar timescale, so pairing
them (rather than PSAR with a slow filter like ADX) means they can
realistically trigger together.
"""

from strategies.base import Strategy, Signal


class RsiPsarReversal(Strategy):
    name = "rsi_psar_reversal"
    preferred_regimes = set()  # reversal strategy -- not regime-restricted
    market_type = "any"
    speed = "fast"   # PSAR flips are sharp, quick reversal signals

    def __init__(self, rsi_oversold: float = 40, rsi_overbought: float = 60):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def generate_signal(self, df, i: int):
        if i < 5:
            return None

        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        psar_trend = row.get("psar_trend")
        prev_psar_trend = prev_row.get("psar_trend")
        rsi_val = row.get("rsi")

        if any(v is None or v != v for v in [psar_trend, prev_psar_trend, rsi_val]):
            return None

        close = row["close"]
        open_ = row["open"]

        flipped_bullish = prev_psar_trend == -1 and psar_trend == 1
        flipped_bearish = prev_psar_trend == 1 and psar_trend == -1

        if flipped_bullish and rsi_val < self.rsi_oversold + 15 and close > open_:
            return Signal(
                strategy=self.name,
                direction="CE",
                reasons=[
                    "Parabolic SAR flipped bullish (dot moved below price)",
                    f"RSI supportive of reversal ({rsi_val:.1f})",
                    "Bullish confirmation candle",
                ],
                score_components={"trend": 0.8, "momentum": min((60 - rsi_val) / 30, 1.0), "ema_alignment": 0.6},
            )

        if flipped_bearish and rsi_val > self.rsi_overbought - 15 and close < open_:
            return Signal(
                strategy=self.name,
                direction="PE",
                reasons=[
                    "Parabolic SAR flipped bearish (dot moved above price)",
                    f"RSI supportive of reversal ({rsi_val:.1f})",
                    "Bearish confirmation candle",
                ],
                score_components={"trend": 0.8, "momentum": min((rsi_val - 40) / 30, 1.0), "ema_alignment": 0.6},
            )

        return None
