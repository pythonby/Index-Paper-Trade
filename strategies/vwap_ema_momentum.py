"""
strategies/vwap_ema_momentum.py
================================
Strategy A -- VWAP + EMA Momentum.

CE: price above VWAP, fast EMA above slow EMA, momentum confirmed (RSI > 55),
    confirmed bullish breakout candle closing above the recent swing high.
PE: mirror image on the downside.

Only fires on a CONFIRMED candle close, never mid-candle.
"""

from strategies.base import Strategy, Signal


class VwapEmaMomentum(Strategy):
    name = "vwap_ema_momentum"
    preferred_regimes = {"strong_bullish", "strong_bearish"}

    def __init__(self, ema_fast_col="ema_fast", ema_slow_col="ema_slow",
                 swing_lookback: int = 10):
        self.ema_fast_col = ema_fast_col
        self.ema_slow_col = ema_slow_col
        self.swing_lookback = swing_lookback

    def generate_signal(self, df, i: int):
        if i < self.swing_lookback + 1:
            return None

        row = df.iloc[i]
        window = df.iloc[max(0, i - self.swing_lookback):i]  # excludes current bar -> no look-ahead

        vwap_val = row.get("vwap")
        ema_fast = row.get(self.ema_fast_col)
        ema_slow = row.get(self.ema_slow_col)
        rsi_val = row.get("rsi")
        close = row["close"]
        open_ = row["open"]

        if any(v is None or v != v for v in [vwap_val, ema_fast, ema_slow, rsi_val]):
            return None

        swing_high = window["high"].max()
        swing_low = window["low"].min()

        bullish_candle = close > open_
        bearish_candle = close < open_

        # CE conditions
        if (close > vwap_val and ema_fast > ema_slow and rsi_val > 55
                and bullish_candle and close > swing_high):
            reasons = [
                "Price above session VWAP",
                f"EMA{self.ema_fast_col} above EMA{self.ema_slow_col} (bullish alignment)",
                f"Momentum confirmed (RSI={rsi_val:.1f} > 55)",
                "Confirmed bullish breakout candle above recent swing high",
            ]
            return Signal(
                strategy=self.name,
                direction="CE",
                reasons=reasons,
                score_components={
                    "trend": 1.0,
                    "vwap": 1.0,
                    "ema_alignment": 1.0,
                    "momentum": min((rsi_val - 50) / 30, 1.0),
                    "breakout_quality": min((close - swing_high) / max(swing_high - swing_low, 1e-6), 1.0),
                },
            )

        # PE conditions
        if (close < vwap_val and ema_fast < ema_slow and rsi_val < 45
                and bearish_candle and close < swing_low):
            reasons = [
                "Price below session VWAP",
                f"EMA{self.ema_fast_col} below EMA{self.ema_slow_col} (bearish alignment)",
                f"Momentum confirmed (RSI={rsi_val:.1f} < 45)",
                "Confirmed bearish breakdown candle below recent swing low",
            ]
            return Signal(
                strategy=self.name,
                direction="PE",
                reasons=reasons,
                score_components={
                    "trend": 1.0,
                    "vwap": 1.0,
                    "ema_alignment": 1.0,
                    "momentum": min((50 - rsi_val) / 30, 1.0),
                    "breakout_quality": min((swing_low - close) / max(swing_high - swing_low, 1e-6), 1.0),
                },
            )

        return None
