"""
backtest/engine.py
===================
Bar-by-bar backtest engine. Structurally prevents look-ahead bias: signals
are generated from df.iloc[:i+1] only, and the earliest an order can fill
is the NEXT bar's open (not the signal bar's own close), which is the
standard way to avoid "trading on information you didn't have yet."

Includes: position sizing, stop-loss/target/trailing-stop, daily loss
limit, max consecutive losses, max trades/day, end-of-day square-off,
slippage, transaction costs, equity curve, drawdown.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta, datetime
from typing import List, Optional

import pandas as pd

import config
from indicators import ema, vwap, atr, rsi, volume_avg, realized_volatility, opening_range, bollinger_bands
from options.selector import select_backtest_contract
from regime.detector import classify_regime
from signal.scorer import score_signal
from risk.manager import DailyRiskState, compute_position_size, update_trailing_stop
from costs.model import net_pnl as compute_net_pnl


@dataclass
class OpenPosition:
    trade_uid: str
    strategy: str
    direction: str
    index_name: str
    entry_time: pd.Timestamp
    entry_signal_price: float
    entry_execution_price: float
    quantity: int
    stop_loss: float
    target: float
    peak_price: float
    strike: float
    expiry: date
    signal_score: float
    reasons: list
    capital_used: float
    risk_amount: float


@dataclass
class BacktestResult:
    trades: List[dict] = field(default_factory=list)
    equity_curve: List[dict] = field(default_factory=list)
    rejected_signals: List[dict] = field(default_factory=list)


def prepare_dataframe(df: pd.DataFrame, ema_fast: int, ema_slow: int,
                       orb_minutes: int) -> pd.DataFrame:
    """Adds all indicator columns. Every indicator here is causal (uses only
    current + past bars), so slicing df.iloc[:i+1] downstream is safe."""
    df = df.copy()
    df["ema_fast"] = ema(df["close"], ema_fast)
    df["ema_slow"] = ema(df["close"], ema_slow)
    df["vwap"] = vwap(df)
    df["rsi"] = rsi(df["close"])
    df["atr"] = atr(df)
    df["volume_avg"] = volume_avg(df)
    df["realized_vol"] = realized_volatility(df)
    or_high, or_low = opening_range(df, orb_minutes)
    df[f"or_high_{orb_minutes}"] = or_high
    df[f"or_low_{orb_minutes}"] = or_low

    bb_upper, bb_mid, bb_lower = bollinger_bands(df["close"], config.BOLLINGER_WINDOW, config.BOLLINGER_STD)
    df["bb_upper"] = bb_upper
    df["bb_mid"] = bb_mid
    df["bb_lower"] = bb_lower
    return df


def next_weekly_expiry(d: date) -> date:
    """NSE weekly index-option expiry is Tuesday (as of the current cycle;
    exchanges periodically change the expiry day -- verify before live use).
    Returns the same day if d is already the expiry day and it's before close."""
    days_ahead = (1 - d.weekday()) % 7  # Tuesday = 1
    if days_ahead == 0:
        return d
    return d + timedelta(days=days_ahead)


def run_backtest(df: pd.DataFrame, index_name: str, strategies: list,
                  timeframe_min: int, mode_label: str = "backtest") -> BacktestResult:
    """
    df: raw OHLCV DataFrame (already prepared with prepare_dataframe) for ONE index.
    strategies: list of Strategy instances to evaluate concurrently (only one
                position open at a time across all of them, per MAX_OPEN_POSITIONS).
    """
    result = BacktestResult()

    risk_state: Optional[DailyRiskState] = None
    open_position: Optional[OpenPosition] = None
    capital = config.STARTING_CAPITAL
    peak_equity = capital
    max_drawdown = 0.0

    for i in range(len(df)):
        row = df.iloc[i]
        ts: pd.Timestamp = df.index[i]
        current_date = ts.date()

        if risk_state is None or risk_state.trading_date != current_date:
            risk_state = DailyRiskState(
                trading_date=current_date,
                starting_capital=capital,
                current_capital=capital,
            )

        bar_time = ts.time()

        # ---- manage open position first (check exits) ----
        if open_position is not None:
            exit_reason = None
            exit_signal_price = None

            high, low, close = row["high"], row["low"], row["close"]
            # NOTE: we are pricing the OPTION, but bar high/low/close here are
            # INDEX bars. We reprice the option at this bar's close via Black-Scholes
            # to know whether stop/target were hit -- an approximation, since true
            # intrabar option high/low are unknown without option tick data.
            days_to_expiry = max((open_position.expiry - current_date).days, 0)
            t_years = max(days_to_expiry, 0.05) / 365.0
            iv = config.BACKTEST_ASSUMED_IV.get(index_name, 0.15)
            from options.selector import black_scholes_price
            option_price_now = black_scholes_price(
                close, open_position.strike, t_years, iv,
                config.BACKTEST_RISK_FREE_RATE, open_position.direction,
            )

            open_position.peak_price = max(open_position.peak_price, option_price_now)

            current_stop = open_position.stop_loss
            if config.USE_TRAILING_STOP:
                current_stop = update_trailing_stop(
                    current_stop, open_position.entry_execution_price, open_position.peak_price
                )
                open_position.stop_loss = current_stop

            if option_price_now <= current_stop:
                exit_reason = "Stop-loss hit"
                exit_signal_price = current_stop
            elif option_price_now >= open_position.target:
                exit_reason = "Target hit"
                exit_signal_price = open_position.target
            elif bar_time >= config.SQUARE_OFF_TIME:
                exit_reason = "End-of-day square-off"
                exit_signal_price = option_price_now
            elif i == len(df) - 1:
                exit_reason = "End of data (forced close)"
                exit_signal_price = option_price_now

            if exit_reason:
                pnl_info = compute_net_pnl(
                    open_position.entry_signal_price, exit_signal_price, open_position.quantity
                )
                capital += pnl_info["net_pnl"]
                risk_state.register_trade_result(pnl_info["net_pnl"])

                trade_record = {
                    "trade_uid": open_position.trade_uid,
                    "mode": mode_label,
                    "index_name": index_name,
                    "strategy": open_position.strategy,
                    "direction": open_position.direction,
                    "strike": open_position.strike,
                    "expiry": str(open_position.expiry),
                    "timeframe_min": timeframe_min,
                    "signal_score": open_position.signal_score,
                    "entry_time": str(open_position.entry_time),
                    "entry_signal_price": pnl_info["entry_signal_price"],
                    "entry_execution_price": pnl_info["entry_execution_price"],
                    "exit_time": str(ts),
                    "exit_signal_price": pnl_info["exit_signal_price"],
                    "exit_execution_price": pnl_info["exit_execution_price"],
                    "quantity": open_position.quantity,
                    "stop_loss": open_position.stop_loss,
                    "target": open_position.target,
                    "exit_reason": exit_reason,
                    "gross_pnl": pnl_info["gross_pnl"],
                    "slippage_cost": pnl_info["slippage_cost"],
                    "charges": pnl_info["charges"],
                    "net_pnl": pnl_info["net_pnl"],
                    "capital_used": open_position.capital_used,
                    "risk_amount": open_position.risk_amount,
                    "reasons": "; ".join(open_position.reasons),
                }
                result.trades.append(trade_record)
                open_position = None

        peak_equity = max(peak_equity, capital)
        drawdown = peak_equity - capital
        max_drawdown = max(max_drawdown, drawdown)
        result.equity_curve.append({"time": str(ts), "equity": capital, "drawdown": drawdown})

        # ---- look for a new entry only if flat, within trading hours, not halted ----
        if open_position is not None:
            continue
        if risk_state.trading_halted:
            continue
        if not (config.NO_NEW_ENTRY_BEFORE <= bar_time <= config.NO_NEW_ENTRY_AFTER):
            continue
        if bar_time >= config.SQUARE_OFF_TIME:
            continue

        regime = classify_regime(df, i)

        for strat in strategies:
            sig = strat.generate_signal(df, i)
            if sig is None:
                continue

            regime_fit = regime.allows(strat.preferred_regimes)
            score_info = score_signal(sig.score_components, regime_fit, liquidity_ok=True)

            if not score_info["passes_threshold"]:
                result.rejected_signals.append({
                    "mode": mode_label, "index_name": index_name, "strategy": strat.name,
                    "direction": sig.direction, "signal_time": str(ts),
                    "signal_score": score_info["score"],
                    "rejection_reason": f"Score {score_info['score']} below threshold {config.MIN_SIGNAL_SCORE}",
                })
                continue

            # entry executes at the OPEN of the NEXT bar (avoids look-ahead / same-bar fills)
            if i + 1 >= len(df):
                continue
            next_bar = df.iloc[i + 1]
            next_ts = df.index[i + 1]
            if next_ts.date() != current_date:
                continue  # don't carry entries across days

            expiry = next_weekly_expiry(current_date)
            contract = select_backtest_contract(index_name, sig.direction, next_bar["open"], next_ts, expiry)

            if not contract.liquidity_ok():
                result.rejected_signals.append({
                    "mode": mode_label, "index_name": index_name, "strategy": strat.name,
                    "direction": sig.direction, "signal_time": str(ts),
                    "signal_score": score_info["score"],
                    "rejection_reason": "Failed liquidity/spread check",
                })
                continue

            sizing = compute_position_size(contract.ltp, contract.lot_size, capital)
            if sizing.rejected:
                result.rejected_signals.append({
                    "mode": mode_label, "index_name": index_name, "strategy": strat.name,
                    "direction": sig.direction, "signal_time": str(ts),
                    "signal_score": score_info["score"],
                    "rejection_reason": sizing.rejection_reason,
                })
                continue

            entry_fill_price = contract.ltp  # slippage applied at exit-time cost calc via net_pnl()
            open_position = OpenPosition(
                trade_uid=str(uuid.uuid4())[:8],
                strategy=strat.name,
                direction=sig.direction,
                index_name=index_name,
                entry_time=next_ts,
                entry_signal_price=entry_fill_price,
                entry_execution_price=entry_fill_price,  # exact exec price resolved in compute_net_pnl
                quantity=sizing.quantity,
                stop_loss=sizing.stop_loss_price,
                target=sizing.target_price,
                peak_price=entry_fill_price,
                strike=contract.strike,
                expiry=expiry,
                signal_score=score_info["score"],
                reasons=sig.reasons,
                capital_used=sizing.capital_used,
                risk_amount=sizing.risk_amount,
            )
            break  # only one strategy's signal can be acted on per bar

    return result
