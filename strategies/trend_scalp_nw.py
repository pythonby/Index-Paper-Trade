"""
strategies/trend_scalp_nw.py
=============================
Strategy E -- Trend Scalp with Nadaraya-Watson Zones.

This strategy ONLY trades in the direction of the prevailing trend (never
counter-trend), and uses the Nadaraya-Watson envelope's lower/upper bands
as dynamic "zones" -- price pulling back into the zone in an uptrend (or
rallying into the zone in a downtrend) is the entry trigger, similar to
how traders use a rising/falling channel's edge as a bounce zone.

It is a SCALPING strategy: it uses a deliberately tighter stop-loss and
target than the system default (see config.SCALP_STOP_LOSS_PCT /
SCALP_TARGET_PCT), aiming for quick, small moves rather than holding for
a large target -- appropriate for the fast mean-reversion-within-a-trend
behavior the NW zone is designed to catch.

CE: EMA fast > EMA slow AND price above the NW mean line (uptrend), price
    dips into/near the NW lower band (the "zone"), then a bullish
    confirmation candle resumes the trend.
PE: mirror image at the NW upper band in a downtrend.
"""

from strategies.base import Strategy, Signal
import config


class TrendScalpNW(Strategy):
    name = "trend_scalp_nw"
    preferred_regimes = {"strong_bullish", "strong_bearish"}   # trend-only, never counter-trend

    def __init__(self, zone_tolerance_pct: float = 0.0012):
        # how close price must get to the NW band to count as "in the zone"
        self.zone_tolerance_pct = zone_tolerance_pct

    def generate_signal(self, df, i: int):
        if i < 30:
            return None

        row = df.iloc[i]
        prev_row = df.iloc[i - 1]

        nw_mean = row.get("nw_mean")
        nw_upper = row.get("nw_upper")
        nw_lower = row.get("nw_lower")
        ema_fast = row.get("ema_fast")
        ema_slow = row.get("ema_slow")

        if any(v is None or v != v for v in [nw_mean, nw_upper, nw_lower, ema_fast, ema_slow]):
            return None

        close = row["close"]
        open_ = row["open"]
        low = row["low"]
        high = row["high"]
        prev_close = prev_row["close"]
        prev_low = prev_row["low"]
        prev_high = prev_row["high"]

        # Trend filter uses EMA alignment only (not "close vs NW mean") -- during
        # a genuine pullback to the zone, price often dips slightly below the NW
        # mean line too, which would have wrongly disqualified real setups.
        uptrend = ema_fast > ema_slow
        downtrend = ema_fast < ema_slow

        if not uptrend and not downtrend:
            return None  # sideways / unclear -- this strategy only trades established trends

        # "Zone" = price touching or dipping into the NW band. IMPORTANT: the
        # bar that touches the zone is usually itself a down/bearish candle
        # (that's how it got there) -- the confirmation candle is typically
        # the NEXT bar. So we check if EITHER this bar or the previous bar
        # touched the zone, with the current bar acting as confirmation.
        touched_lower_zone = (low <= nw_lower * (1 + self.zone_tolerance_pct) or
                               prev_low <= nw_lower * (1 + self.zone_tolerance_pct))
        touched_upper_zone = (high >= nw_upper * (1 - self.zone_tolerance_pct) or
                               prev_high >= nw_upper * (1 - self.zone_tolerance_pct))

        # Confirmation: bullish candle that closes back above the lower zone
        # (recovering), or bearish candle closing back below the upper zone.
        bullish_confirm = close > open_ and close > prev_close and close > nw_lower
        bearish_confirm = close < open_ and close < prev_close and close < nw_upper

        if uptrend and touched_lower_zone and bullish_confirm:
            reasons = [
                "Uptrend confirmed (EMA fast > slow)",
                f"Price touched the Nadaraya-Watson lower zone ({nw_lower:.1f}) -- dynamic support",
                "Bullish confirmation candle recovering above the zone (scalp entry)",
                f"Tight scalp stop/target: {config.SCALP_STOP_LOSS_PCT*100:.0f}% / {config.SCALP_TARGET_PCT*100:.0f}%",
            ]
            band_width = max(nw_upper - nw_lower, 1e-6)
            return Signal(
                strategy=self.name,
                direction="CE",
                reasons=reasons,
                score_components={
                    "trend": 1.0,
                    "vwap": 0.7,
                    "ema_alignment": 1.0,
                    "momentum": 0.6,
                    "breakout_quality": min((nw_lower - low) / band_width + 0.5, 1.0),
                },
                stop_loss_pct=config.SCALP_STOP_LOSS_PCT,
                target_pct=config.SCALP_TARGET_PCT,
            )

        if downtrend and touched_upper_zone and bearish_confirm:
            reasons = [
                "Downtrend confirmed (EMA fast < slow)",
                f"Price touched the Nadaraya-Watson upper zone ({nw_upper:.1f}) -- dynamic resistance",
                "Bearish confirmation candle resuming the trend (scalp entry)",
                f"Tight scalp stop/target: {config.SCALP_STOP_LOSS_PCT*100:.0f}% / {config.SCALP_TARGET_PCT*100:.0f}%",
            ]
            band_width = max(nw_upper - nw_lower, 1e-6)
            return Signal(
                strategy=self.name,
                direction="PE",
                reasons=reasons,
                score_components={
                    "trend": 1.0,
                    "vwap": 0.7,
                    "ema_alignment": 1.0,
                    "momentum": 0.6,
                    "breakout_quality": min((high - nw_upper) / band_width + 0.5, 1.0),
                },
                stop_loss_pct=config.SCALP_STOP_LOSS_PCT,
                target_pct=config.SCALP_TARGET_PCT,
            )

        return None
