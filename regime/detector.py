"""
regime/detector.py
===================
Classifies the current market regime from already-computed indicator columns.
Used to suppress strategies outside their preferred regime (e.g. don't take
ORB breakout trades in a sideways/range-bound market).

Regimes: strong_bullish, strong_bearish, sideways, high_volatility, low_volatility

Note: high/low_volatility are an orthogonal axis to the trend regimes above
(a market can be "sideways" AND "high_volatility"). We return both a primary
trend regime and a volatility flag.
"""

from dataclasses import dataclass


@dataclass
class RegimeState:
    trend_regime: str        # "strong_bullish" | "strong_bearish" | "sideways"
    volatility_regime: str   # "high_volatility" | "low_volatility" | "normal"

    def allows(self, preferred_regimes: set) -> bool:
        """SOFT check -- used only for the signal SCORE (regime_fit component),
        not for strict on/off gating. See strategy_allowed() for the strict gate."""
        if not preferred_regimes:
            return True
        return self.trend_regime in preferred_regimes or self.volatility_regime in preferred_regimes

    def strategy_allowed(self, strategy) -> bool:
        """
        STRICT gate: a strategy is only allowed to fire at all if its
        market_type and speed classification match the CURRENT regime.
        This is a hard AND of both axes -- e.g. a "trending + fast"
        strategy is blocked unless the market is BOTH trending AND
        high-volatility right now. "any" on either axis means no
        restriction on that axis.

        market_type:
            "trending" -> requires trend_regime in {strong_bullish, strong_bearish}
            "sideways" -> requires trend_regime == "sideways"
            "any"      -> no restriction (e.g. rsi_adx_nw self-selects internally)

        speed:
            "slow" -> blocked when volatility_regime == "high_volatility"
            "fast" -> blocked when volatility_regime == "low_volatility"
            "any"  -> no restriction (both "normal" volatility bars are
                      compatible with either slow or fast strategies)
        """
        market_type = getattr(strategy, "market_type", "any")
        speed = getattr(strategy, "speed", "any")

        if market_type == "trending" and self.trend_regime not in ("strong_bullish", "strong_bearish"):
            return False
        if market_type == "sideways" and self.trend_regime != "sideways":
            return False

        if speed == "slow" and self.volatility_regime == "high_volatility":
            return False
        if speed == "fast" and self.volatility_regime == "low_volatility":
            return False

        return True


def classify_regime(df, i: int, adx_threshold: float = 22.0,
                     vol_high_pctile: float = 0.75, vol_low_pctile: float = 0.25) -> RegimeState:
    """
    df must already contain 'ema_fast', 'ema_slow', 'close', 'realized_vol' columns.
    Uses a simple EMA-slope + separation heuristic in place of ADX (kept
    dependency-free), plus a rolling percentile of realized volatility.
    """
    if i < 30:
        return RegimeState("sideways", "normal")

    window = df.iloc[max(0, i - 30):i + 1]
    ema_fast = window["ema_fast"]
    ema_slow = window["ema_slow"]
    close = window["close"]

    separation_pct = (ema_fast.iloc[-1] - ema_slow.iloc[-1]) / close.iloc[-1]
    slope = ema_fast.iloc[-1] - ema_fast.iloc[0]
    slope_pct = slope / close.iloc[0]

    if separation_pct > 0.001 and slope_pct > 0.0015:
        trend_regime = "strong_bullish"
    elif separation_pct < -0.001 and slope_pct < -0.0015:
        trend_regime = "strong_bearish"
    else:
        trend_regime = "sideways"

    vol_series = df["realized_vol"].iloc[max(0, i - 100):i + 1].dropna()
    current_vol = df["realized_vol"].iloc[i]
    volatility_regime = "normal"
    if len(vol_series) >= 20 and current_vol == current_vol:  # not NaN
        hi = vol_series.quantile(vol_high_pctile)
        lo = vol_series.quantile(vol_low_pctile)
        if current_vol >= hi:
            volatility_regime = "high_volatility"
        elif current_vol <= lo:
            volatility_regime = "low_volatility"

    return RegimeState(trend_regime, volatility_regime)
