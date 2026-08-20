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


class Strategy:
    name = "base"
    # Which regimes this strategy is expected to work in; the regime detector
    # will suppress signals from this strategy outside these regimes.
    preferred_regimes = set()

    def generate_signal(self, df, i: int) -> Optional[Signal]:
        """
        df: full indicator-enriched DataFrame (index-aligned OHLCV + indicators)
        i:  integer row position of the "current" bar. Implementations must
            only look at df.iloc[:i+1] -- never df.iloc[i+1:].
        Returns a Signal or None.
        """
        raise NotImplementedError
