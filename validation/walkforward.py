"""
validation/walkforward.py
==========================
Splits historical data into train/validation/out-of-sample windows and
(optionally) rolling walk-forward folds, so we never judge a strategy's
profitability purely on in-sample results.
"""

from dataclasses import dataclass
from typing import List

import pandas as pd

from backtest.engine import run_backtest
from reports.performance import compute_metrics, is_robust


@dataclass
class WalkForwardFold:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    in_sample_metrics: dict
    out_of_sample_metrics: dict
    robust: bool
    robust_reason: str


def simple_train_test_split(df: pd.DataFrame, train_frac: float = 0.7):
    split_idx = int(len(df) * train_frac)
    return df.iloc[:split_idx], df.iloc[split_idx:]


def rolling_walk_forward(df: pd.DataFrame, index_name: str, strategy_factory, timeframe_min: int,
                          n_folds: int = 3, train_frac: float = 0.6) -> List[WalkForwardFold]:
    """
    strategy_factory: callable() -> list[Strategy] (fresh instances per fold,
    since strategies here are stateless but this keeps the pattern extensible
    to strategies with fitted parameters in the future).

    Splits df into n_folds rolling windows, each with a train segment followed
    by a held-out test segment immediately after it in time (never testing on
    data that precedes its own training window in a way that leaks the future
    backwards).
    """
    folds = []
    total_len = len(df)
    fold_len = total_len // n_folds

    for f in range(n_folds):
        start = f * fold_len
        end = start + fold_len if f < n_folds - 1 else total_len
        segment = df.iloc[start:end]
        if len(segment) < 50:
            continue

        train_seg, test_seg = simple_train_test_split(segment, train_frac)
        if len(train_seg) < 20 or len(test_seg) < 20:
            continue

        strategies = strategy_factory()
        train_result = run_backtest(train_seg, index_name, strategies, timeframe_min, mode_label="backtest_train")
        test_result = run_backtest(test_seg, index_name, strategies, timeframe_min, mode_label="backtest_test")

        in_sample = compute_metrics(train_result.trades, train_result.equity_curve, 0)
        out_sample = compute_metrics(test_result.trades, test_result.equity_curve, 0)

        robust, reason = is_robust(in_sample, out_sample)

        folds.append(WalkForwardFold(
            train_start=str(train_seg.index[0]) if len(train_seg) else "",
            train_end=str(train_seg.index[-1]) if len(train_seg) else "",
            test_start=str(test_seg.index[0]) if len(test_seg) else "",
            test_end=str(test_seg.index[-1]) if len(test_seg) else "",
            in_sample_metrics=in_sample,
            out_of_sample_metrics=out_sample,
            robust=robust,
            robust_reason=reason,
        ))

    return folds


def summarize_folds(folds: List[WalkForwardFold]) -> dict:
    if not folds:
        return {"overall_robust": False, "reason": "No valid folds could be constructed (insufficient data)"}

    robust_folds = [f for f in folds if f.robust]
    overall_robust = len(robust_folds) >= max(1, len(folds) // 2 + 1)  # majority of folds must pass

    return {
        "overall_robust": overall_robust,
        "num_folds": len(folds),
        "robust_folds": len(robust_folds),
        "reason": (
            "Majority of walk-forward folds passed robustness checks"
            if overall_robust else
            "Fewer than half of walk-forward folds were robust -- reject strategy"
        ),
    }
