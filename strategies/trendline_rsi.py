"""
strategies/trendline_rsi.py
=============================
Combo 9 -- Trendlines + RSI.

Builds a simple rising trendline from the last two CONFIRMED swing lows
(for uptrends) or a falling trendline from the last two confirmed swing
highs (for downtrends), using indicators.swing_points (causal, with a
confirmation lag so nothing here uses future bars). When price comes back
to test the extrapolated trendline value with an RSI-momentum-confirmed
candle, that's the entry -- both react at the same per-bar frequency.
"""

from strategies.base import Strategy, Signal


class TrendlineRsi(Strategy):
    name = "trendline_rsi"
    preferred_regimes = {"strong_bullish", "strong_bearish"}
    market_type = "trending"
    speed = "slow"

    def __init__(self, touch_tolerance_pct: float = 0.0015, lookback_bars: int = 60):
        self.touch_tolerance_pct = touch_tolerance_pct
        self.lookback_bars = lookback_bars

    def _recent_confirmed_points(self, df, i, val_col, age_col):
        """Returns up to the last 2 confirmed swing points (bar_index, value)
        within the lookback window, oldest first."""
        start = max(0, i - self.lookback_bars)
        window = df.iloc[start:i + 1]
        valid = window[window[val_col].notna()]
        if len(valid) < 2:
            return None
        points = []
        for idx_pos in range(len(valid)):
            row_idx = valid.index[idx_pos]
            bar_pos = df.index.get_loc(row_idx)
            age = valid.iloc[idx_pos][age_col]
            formed_at = bar_pos - int(age)
            points.append((formed_at, valid.iloc[idx_pos][val_col]))
        return points[-2:]  # last two confirmed points

    def generate_signal(self, df, i: int):
        if i < 30:
            return None

        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        ema_fast = row.get("ema_fast")
        ema_slow = row.get("ema_slow")
        rsi_val = row.get("rsi")
        if any(v is None or v != v for v in [ema_fast, ema_slow, rsi_val]):
            return None

        close = row["close"]
        open_ = row["open"]
        low = row["low"]
        high = row["high"]
        prev_close = prev_row["close"]

        uptrend = ema_fast > ema_slow
        downtrend = ema_fast < ema_slow
        if not uptrend and not downtrend:
            return None

        if uptrend:
            points = self._recent_confirmed_points(df, i, "swing_low_val", "swing_low_age")
            if not points:
                return None
            (x1, y1), (x2, y2) = points
            if x2 == x1:
                return None
            slope = (y2 - y1) / (x2 - x1)
            if slope <= 0:
                return None  # not actually a rising trendline
            trendline_value = y2 + slope * (i - x2)

            touched = low <= trendline_value * (1 + self.touch_tolerance_pct)
            bullish_confirm = close > open_ and close > prev_close and rsi_val > 45

            if touched and bullish_confirm:
                return Signal(
                    strategy=self.name,
                    direction="CE",
                    reasons=[
                        "Rising trendline drawn from last 2 confirmed swing lows",
                        f"Price bounced off trendline (~{trendline_value:.1f})",
                        f"RSI momentum supportive ({rsi_val:.1f})",
                    ],
                    score_components={"trend": 1.0, "momentum": min((rsi_val - 45) / 25, 1.0), "ema_alignment": 0.8},
                )

        if downtrend:
            points = self._recent_confirmed_points(df, i, "swing_high_val", "swing_high_age")
            if not points:
                return None
            (x1, y1), (x2, y2) = points
            if x2 == x1:
                return None
            slope = (y2 - y1) / (x2 - x1)
            if slope >= 0:
                return None  # not actually a falling trendline
            trendline_value = y2 + slope * (i - x2)

            touched = high >= trendline_value * (1 - self.touch_tolerance_pct)
            bearish_confirm = close < open_ and close < prev_close and rsi_val < 55

            if touched and bearish_confirm:
                return Signal(
                    strategy=self.name,
                    direction="PE",
                    reasons=[
                        "Falling trendline drawn from last 2 confirmed swing highs",
                        f"Price rejected at trendline (~{trendline_value:.1f})",
                        f"RSI momentum supportive ({rsi_val:.1f})",
                    ],
                    score_components={"trend": 1.0, "momentum": min((55 - rsi_val) / 25, 1.0), "ema_alignment": 0.8},
                )

        return None
