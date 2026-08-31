"""
strategies/rsi_adx_nw.py
==========================
Combo 4 -- RSI + ADX (trending regime) / Nadaraya-Watson zone (sideways regime).

This is a single regime-switching strategy:
- When ADX shows a real trend (ADX > threshold), it trades RSI momentum
  bursts in the EMA trend direction. ADX and RSI are paired here because
  ADX confirms the *context* (a trend exists) while RSI captures the
  *momentum burst* within it -- compatible timescales, ADX doesn't need
  to move for RSI to trigger.
- When ADX shows no trend (sideways), it switches to Nadaraya-Watson zone
  touches with an RSI extreme for reversal confirmation -- matching the
  mean-reversion strategy's zone+RSI pairing (both react per-bar).
"""

from strategies.base import Strategy, Signal
import config


class RsiAdxNW(Strategy):
    name = "rsi_adx_nw"
    preferred_regimes = set()  # this strategy handles BOTH trending and sideways itself
    market_type = "any"        # self-selects trending vs sideways internally via ADX
    speed = "any"

    def __init__(self, adx_trend_threshold: float = 22.0, zone_tolerance_pct: float = 0.0012):
        self.adx_trend_threshold = adx_trend_threshold
        self.zone_tolerance_pct = zone_tolerance_pct

    def generate_signal(self, df, i: int):
        if i < 30:
            return None

        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        adx_val = row.get("adx")
        ema_fast = row.get("ema_fast")
        ema_slow = row.get("ema_slow")
        rsi_val = row.get("rsi")
        nw_lower = row.get("nw_lower")
        nw_upper = row.get("nw_upper")

        if any(v is None or v != v for v in [adx_val, ema_fast, ema_slow, rsi_val]):
            return None

        close = row["close"]
        open_ = row["open"]
        low = row["low"]
        high = row["high"]
        prev_low = prev_row["low"]
        prev_high = prev_row["high"]
        prev_close = prev_row["close"]

        is_trending = adx_val > self.adx_trend_threshold

        if is_trending:
            uptrend = ema_fast > ema_slow
            downtrend = ema_fast < ema_slow
            if uptrend and rsi_val > 55 and close > open_:
                return Signal(
                    strategy=self.name, direction="CE",
                    reasons=[f"ADX trending ({adx_val:.1f} > {self.adx_trend_threshold})",
                             "EMA uptrend + RSI momentum burst (>55)", "Bullish candle"],
                    score_components={"trend": 1.0, "momentum": min((rsi_val - 50) / 30, 1.0),
                                       "ema_alignment": 1.0, "vwap": 0.5},
                )
            if downtrend and rsi_val < 45 and close < open_:
                return Signal(
                    strategy=self.name, direction="PE",
                    reasons=[f"ADX trending ({adx_val:.1f} > {self.adx_trend_threshold})",
                             "EMA downtrend + RSI momentum burst (<45)", "Bearish candle"],
                    score_components={"trend": 1.0, "momentum": min((50 - rsi_val) / 30, 1.0),
                                       "ema_alignment": 1.0, "vwap": 0.5},
                )
            return None

        # Sideways regime -- Nadaraya-Watson zone + RSI extreme reversal
        if any(v is None or v != v for v in [nw_lower, nw_upper]):
            return None

        touched_lower = (low <= nw_lower * (1 + self.zone_tolerance_pct) or
                          prev_low <= nw_lower * (1 + self.zone_tolerance_pct))
        touched_upper = (high >= nw_upper * (1 - self.zone_tolerance_pct) or
                          prev_high >= nw_upper * (1 - self.zone_tolerance_pct))

        if touched_lower and rsi_val < 35 and close > open_ and close > prev_close:
            return Signal(
                strategy=self.name, direction="CE",
                reasons=[f"ADX sideways ({adx_val:.1f} <= {self.adx_trend_threshold})",
                         "Price touched NW lower zone + RSI oversold", "Bullish reversal candle"],
                score_components={"trend": 0.5, "momentum": min((35 - rsi_val) / 35, 1.0), "vwap": 0.5},
            )
        if touched_upper and rsi_val > 65 and close < open_ and close < prev_close:
            return Signal(
                strategy=self.name, direction="PE",
                reasons=[f"ADX sideways ({adx_val:.1f} <= {self.adx_trend_threshold})",
                         "Price touched NW upper zone + RSI overbought", "Bearish reversal candle"],
                score_components={"trend": 0.5, "momentum": min((rsi_val - 65) / 35, 1.0), "vwap": 0.5},
            )
        return None
