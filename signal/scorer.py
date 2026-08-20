"""
signal/scorer.py
=================
Combines a strategy's raw score_components (each roughly 0-1) plus market
regime/liquidity context into a single 0-100 signal score. Only signals
scoring >= config.MIN_SIGNAL_SCORE are eligible to trade.

Weights are configurable and intentionally simple/explainable -- this is
NOT a black-box ML model, per the "simple and explainable" requirement.
"""

import config

# Weights sum to 1.0 across the factors we actually have available.
WEIGHTS = {
    "trend": 0.20,
    "vwap": 0.15,
    "ema_alignment": 0.15,
    "momentum": 0.15,
    "breakout_quality": 0.10,
    "volume": 0.10,
    "regime_fit": 0.10,
    "liquidity": 0.05,
}


def score_signal(score_components: dict, regime_fit: bool, liquidity_ok: bool) -> dict:
    """
    Returns dict with 'score' (0-100) and 'breakdown' for transparency in
    Telegram messages / logs.
    """
    breakdown = {}
    total = 0.0
    weight_used = 0.0

    for factor, weight in WEIGHTS.items():
        if factor == "regime_fit":
            value = 1.0 if regime_fit else 0.0
        elif factor == "liquidity":
            value = 1.0 if liquidity_ok else 0.0
        else:
            value = score_components.get(factor)
            if value is None:
                continue  # factor not provided by this strategy, skip rather than penalize
            value = max(0.0, min(1.0, value))

        contribution = value * weight
        breakdown[factor] = round(contribution * 100, 1)
        total += contribution
        weight_used += weight

    # renormalize by weight actually used so missing optional factors don't
    # unfairly deflate the score
    normalized = (total / weight_used) if weight_used > 0 else 0.0
    score = round(normalized * 100, 1)

    return {
        "score": score,
        "breakdown": breakdown,
        "passes_threshold": score >= config.MIN_SIGNAL_SCORE,
    }
