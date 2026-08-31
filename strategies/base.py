"""
strategies/base.py
===================
Common interface every strategy implements. Strategies only ever produce
CE/PE BUY signals (or no signal). They never see future bars -- each
strategy method is called with a DataFrame sliced up to and including the
current bar (df.iloc[:i+1]) so look-ahead bias is structurally prevented
by the backtest engine, not just by convention.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    strategy: str
    direction: str          # "CE" or "PE"
    reasons: list
    score_components: dict  # raw component values feeding the signal scorer
    stop_loss_pct: float = None   # optional override of config.STOP_LOSS_PCT_OF_PREMIUM
    target_pct: float = None      # optional override of config.TARGET_PCT_OF_PREMIUM


class Strategy:
    name = "base"
    # Which regimes this strategy is expected to work in; used for SCORING
    # (a soft signal-quality factor). For STRICT on/off gating (this
    # strategy is not even allowed to fire outside its regime), see
    # market_type / speed below and regime.detector.strategy_allowed().
    preferred_regimes = set()

    # market_type: "trending" (only allowed when regime.trend_regime is
    # strong_bullish/strong_bearish), "sideways" (only when trend_regime is
    # sideways), or "any" (self-selecting internally, or works in both).
    market_type = "any"

    # speed: "slow" (blocked during high_volatility), "fast" (blocked
    # during low_volatility), or "any" (no volatility restriction).
    speed = "any"

    def generate_signal(self, df, i: int) -> Optional[Signal]:
        """
        df: full indicator-enriched DataFrame (index-aligned OHLCV + indicators)
        i:  integer row position of the "current" bar. Implementations must
            only look at df.iloc[:i+1] -- never df.iloc[i+1:].
        Returns a Signal or None.
        """
        raise NotImplementedError
