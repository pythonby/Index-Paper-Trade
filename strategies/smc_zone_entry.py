"""
strategies/smc_zone_entry.py
==============================
Strategy F -- SMC Zone Entry.

ONLY intended for 15-minute and higher timeframes (see
config.SMC_MIN_TIMEFRAME_MIN) -- this is enforced by main.py's
build_strategy_set(), not inside this file, since a Strategy doesn't
otherwise know which timeframe it's being run on.

Trades in the direction of the EMA trend only. Entry "zone" = an active
Order Block (the most recent opposite-colored candle, per
indicators.smc.order_blocks) OR a recent unfilled Fair Value Gap (per
indicators.smc.fair_value_gaps). When price returns into either zone
type with a confirmation candle in the trend direction, that's the entry.
"""

from strategies.base import Strategy, Signal


class SMCZoneEntry(Strategy):
    name = "smc_zone_entry"
    preferred_regimes = {"strong_bullish", "strong_bearish"}

    def __init__(self, zone_tolerance_pct: float = 0.0008, fvg_lookback: int = 20):
        self.zone_tolerance_pct = zone_tolerance_pct
        self.fvg_lookback = fvg_lookback

    def _nearest_fvg(self, df, i, low_col, high_col):
        """Most recent non-NaN FVG within the lookback window, or (None, None)."""
        start = max(0, i - self.fvg_lookback)
        window = df.iloc[start:i + 1]
        valid = window[low_col].notna()
        if not valid.any():
            return None, None
        last_idx = window.index[valid][-1]
        return window.loc[last_idx, low_col], window.loc[last_idx, high_col]

    def _in_zone(self, bar_low, bar_high, zone_low, zone_high):
        if zone_low is None or zone_low != zone_low:  # None or NaN
            return False
        tol = self.zone_tolerance_pct
        return bar_low <= zone_high * (1 + tol) and bar_high >= zone_low * (1 - tol)

    def generate_signal(self, df, i: int):
        if i < 20:
            return None

        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        ema_fast = row.get("ema_fast")
        ema_slow = row.get("ema_slow")
        if any(v is None or v != v for v in [ema_fast, ema_slow]):
            return None

        uptrend = ema_fast > ema_slow
        downtrend = ema_fast < ema_slow
        if not uptrend and not downtrend:
            return None

        close = row["close"]
        open_ = row["open"]
        low = row["low"]
        high = row["high"]
        prev_close = prev_row["close"]

        bull_ob_low = row.get("bull_ob_low")
        bull_ob_high = row.get("bull_ob_high")
        bear_ob_low = row.get("bear_ob_low")
        bear_ob_high = row.get("bear_ob_high")

        bull_fvg_low, bull_fvg_high = self._nearest_fvg(df, i, "bull_fvg_low", "bull_fvg_high")
        bear_fvg_low, bear_fvg_high = self._nearest_fvg(df, i, "bear_fvg_low", "bear_fvg_high")

        in_demand_zone = (self._in_zone(low, high, bull_ob_low, bull_ob_high)
                           or self._in_zone(low, high, bull_fvg_low, bull_fvg_high))
        in_supply_zone = (self._in_zone(low, high, bear_ob_low, bear_ob_high)
                           or self._in_zone(low, high, bear_fvg_low, bear_fvg_high))

        bullish_confirm = close > open_ and close > prev_close
        bearish_confirm = close < open_ and close < prev_close

        if uptrend and in_demand_zone and bullish_confirm:
            zone_desc = "order block" if self._in_zone(low, high, bull_ob_low, bull_ob_high) else "fair value gap"
            reasons = [
                "Uptrend confirmed (EMA fast > slow)",
                f"Price returned into a bullish {zone_desc} (SMC demand zone)",
                "Bullish confirmation candle from the zone",
            ]
            return Signal(
                strategy=self.name,
                direction="CE",
                reasons=reasons,
                score_components={
                    "trend": 1.0,
                    "vwap": 0.6,
                    "ema_alignment": 1.0,
                    "momentum": 0.6,
                    "breakout_quality": 0.8,
                },
            )

        if downtrend and in_supply_zone and bearish_confirm:
            zone_desc = "order block" if self._in_zone(low, high, bear_ob_low, bear_ob_high) else "fair value gap"
            reasons = [
                "Downtrend confirmed (EMA fast < slow)",
                f"Price returned into a bearish {zone_desc} (SMC supply zone)",
                "Bearish confirmation candle from the zone",
            ]
            return Signal(
                strategy=self.name,
                direction="PE",
                reasons=reasons,
                score_components={
                    "trend": 1.0,
                    "vwap": 0.6,
                    "ema_alignment": 1.0,
                    "momentum": 0.6,
                    "breakout_quality": 0.8,
                },
            )

        return None
