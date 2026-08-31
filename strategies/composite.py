"""
strategies/composite.py
=========================
CompositeAnd -- wraps 2+ sub-strategies and only produces a signal when ALL
of them independently agree on the same direction (CE or PE) on the same
bar. This is how combo strategies like "Trend Pullback + VWAP Momentum" or
"SMC Zone + Nadaraya-Watson" are built, without duplicating any strategy
logic -- both sub-strategies keep working exactly as they do standalone,
this just requires their signals to line up (higher-confluence entries,
naturally fewer trades but each one has two independent confirmations).
"""

from strategies.base import Strategy, Signal


class CompositeAnd(Strategy):
    def __init__(self, sub_strategies: list, name: str = None):
        if len(sub_strategies) < 2:
            raise ValueError("CompositeAnd needs at least 2 sub-strategies")
        self.sub_strategies = sub_strategies
        self.name = name or "+".join(s.name for s in sub_strategies)
        # preferred_regimes = intersection of all sub-strategies' preferred regimes
        # (a combo entry should only fire where every component wants to trade)
        regimes = [s.preferred_regimes for s in sub_strategies if s.preferred_regimes]
        self.preferred_regimes = set.intersection(*regimes) if regimes else set()

        # market_type / speed: take the first non-"any" value found among
        # sub-strategies (if all are "any", the combo stays "any" too).
        market_types = [s.market_type for s in sub_strategies if s.market_type != "any"]
        speeds = [s.speed for s in sub_strategies if s.speed != "any"]
        self.market_type = market_types[0] if market_types else "any"
        self.speed = speeds[0] if speeds else "any"

    def generate_signal(self, df, i: int):
        signals = [s.generate_signal(df, i) for s in self.sub_strategies]
        if any(sig is None for sig in signals):
            return None

        directions = {sig.direction for sig in signals}
        if len(directions) != 1:
            return None  # sub-strategies disagree on direction -- no trade

        direction = directions.pop()
        combined_reasons = []
        combined_scores = {}
        score_counts = {}

        for sig in signals:
            combined_reasons.append(f"[{sig.strategy}]")
            combined_reasons.extend(sig.reasons)
            for k, v in sig.score_components.items():
                combined_scores[k] = combined_scores.get(k, 0.0) + v
                score_counts[k] = score_counts.get(k, 0) + 1

        averaged_scores = {k: v / score_counts[k] for k, v in combined_scores.items()}
        # Slight confluence bonus: multiple independent strategies agreeing
        # is inherently higher quality than any single one alone.
        for k in averaged_scores:
            averaged_scores[k] = min(averaged_scores[k] * 1.1, 1.0)

        # Use the tightest (most specific) SL/target override present among sub-strategies, if any
        sl_overrides = [sig.stop_loss_pct for sig in signals if sig.stop_loss_pct is not None]
        tgt_overrides = [sig.target_pct for sig in signals if sig.target_pct is not None]

        return Signal(
            strategy=self.name,
            direction=direction,
            reasons=combined_reasons,
            score_components=averaged_scores,
            stop_loss_pct=min(sl_overrides) if sl_overrides else None,
            target_pct=min(tgt_overrides) if tgt_overrides else None,
        )
