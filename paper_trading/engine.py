"""
paper_trading/engine.py
========================
Live (simulated) paper-trading loop. NEVER places a real order -- this
module only ever writes to the local SQLite DB and sends Telegram
notifications. There is no broker order API call anywhere in this file.

Uses the LIVE NSE option chain (data/fetcher.fetch_live_option_chain) for
real strike/LTP/spread selection, unlike the backtester which must
synthesize option prices.
"""

import logging
import uuid
import datetime as dt
from typing import Optional

import pandas as pd

import config
from data.fetcher import fetch_index_history, fetch_live_option_chain, DataFeedError, is_data_stale
from backtest.engine import prepare_dataframe, next_weekly_expiry
from options.selector import select_live_contract
from regime.detector import classify_regime
from signal.scorer import score_signal
from risk.manager import DailyRiskState, compute_position_size, update_trailing_stop
from costs.model import net_pnl as compute_net_pnl
from notify import telegram
from db import database

logger = logging.getLogger("paper_trading.engine")


class PaperTradingEngine:
    def __init__(self, index_name: str, strategies: list, timeframe_min: int = config.DEFAULT_TIMEFRAME_MIN):
        self.index_name = index_name
        self.strategies = strategies
        self.timeframe_min = timeframe_min
        self.capital = config.STARTING_CAPITAL
        self.risk_state: Optional[DailyRiskState] = None
        self.open_position = None
        self.last_data_time = None
        self.halted = False

    def _refresh_daily_state(self):
        today = dt.date.today()
        if self.risk_state is None or self.risk_state.trading_date != today:
            self.risk_state = DailyRiskState(
                trading_date=today, starting_capital=self.capital, current_capital=self.capital
            )
            self.halted = False

    def _fail_safe_stop(self, reason: str):
        self.halted = True
        logger.error("FAIL-SAFE TRIGGERED: %s", reason)
        try:
            telegram.send_alert(f"Trading halted: {reason}")
        except Exception:
            logger.error("Also failed to send the fail-safe Telegram alert.")

    def poll_once(self):
        """One iteration: fetch data, evaluate signals, manage open position.
        Call this on a schedule (e.g. every 30-60s) during market hours."""
        self._refresh_daily_state()

        now = dt.datetime.now()
        if not (config.MARKET_OPEN_TIME <= now.time() <= config.MARKET_CLOSE_TIME):
            logger.info("Market closed; skipping poll.")
            return

        if self.halted or self.risk_state.trading_halted:
            logger.info("Trading halted for today (%s); no new signals will be generated.",
                        self.risk_state.halt_reason if self.risk_state else "fail-safe")
            return

        try:
            df = fetch_index_history(self.index_name, self.timeframe_min, period="7d" if self.timeframe_min == 1 else "60d")
        except DataFeedError as e:
            self._fail_safe_stop(f"Index data feed error: {e}")
            return

        if df.empty:
            self._fail_safe_stop("Index data feed returned no rows.")
            return

        self.last_data_time = df.index[-1]
        if is_data_stale(self.last_data_time):
            self._fail_safe_stop(f"Data appears stale (last tick {self.last_data_time}).")
            return

        ema_fast, ema_slow = config.EMA_FAST_CANDIDATES[0], config.EMA_SLOW_CANDIDATES[0]
        orb_minutes = config.ORB_RANGE_MINUTES_CANDIDATES[0]
        df = prepare_dataframe(df, ema_fast, ema_slow, orb_minutes)
        i = len(df) - 1

        # ---- manage existing position ----
        if self.open_position is not None:
            self._manage_open_position(df, i)
            return  # only one thing per poll cycle

        if not (config.NO_NEW_ENTRY_BEFORE <= now.time() <= config.NO_NEW_ENTRY_AFTER):
            logger.info("Outside entry window; no new signals evaluated.")
            return

        regime = classify_regime(df, i)

        for strat in self.strategies:
            sig = strat.generate_signal(df, i)
            if sig is None:
                continue

            regime_fit = regime.allows(strat.preferred_regimes)

            try:
                chain = fetch_live_option_chain(self.index_name)
            except DataFeedError as e:
                self._fail_safe_stop(f"Option chain fetch error: {e}")
                return

            contract = select_live_contract(self.index_name, sig.direction, chain)
            if contract is None:
                logger.info("No liquid contract found for %s %s; NO TRADE.", self.index_name, sig.direction)
                continue

            score_info = score_signal(sig.score_components, regime_fit, contract.liquidity_ok())
            if not score_info["passes_threshold"]:
                database.insert_rejected_signal({
                    "mode": "paper_live", "index_name": self.index_name, "strategy": strat.name,
                    "direction": sig.direction, "signal_time": str(now),
                    "signal_score": score_info["score"],
                    "rejection_reason": f"Score {score_info['score']} below threshold {config.MIN_SIGNAL_SCORE}",
                })
                continue

            sizing = compute_position_size(contract.ltp, contract.lot_size, self.capital)
            if sizing.rejected:
                database.insert_rejected_signal({
                    "mode": "paper_live", "index_name": self.index_name, "strategy": strat.name,
                    "direction": sig.direction, "signal_time": str(now),
                    "signal_score": score_info["score"], "rejection_reason": sizing.rejection_reason,
                })
                continue

            self._open_position(strat, sig, contract, sizing, score_info, now)
            break

    def _open_position(self, strat, sig, contract, sizing, score_info, now):
        from costs.model import apply_slippage
        fill = apply_slippage(contract.ltp, "BUY", sizing.quantity)

        trade_uid = str(uuid.uuid4())[:8]
        self.open_position = {
            "trade_uid": trade_uid,
            "strategy": strat.name,
            "direction": sig.direction,
            "index_name": self.index_name,
            "strike": contract.strike,
            "expiry": str(contract.expiry),
            "timeframe_min": self.timeframe_min,
            "signal_score": score_info["score"],
            "entry_time": str(now),
            "entry_signal_price": contract.ltp,
            "entry_execution_price": fill.execution_price,
            "quantity": sizing.quantity,
            "stop_loss": sizing.stop_loss_price,
            "target": sizing.target_price,
            "peak_price": fill.execution_price,
            "capital_used": sizing.capital_used,
            "risk_amount": sizing.risk_amount,
            "reasons": sig.reasons,
            "spot": contract.spot,
        }

        try:
            telegram.send_entry_notification(self.open_position)
        except telegram.TelegramError:
            logger.warning("Entry notification failed to send (trade still recorded locally).")

        logger.info("Opened paper position: %s", self.open_position)

    def _manage_open_position(self, df, i):
        row = df.iloc[i]
        try:
            chain = fetch_live_option_chain(self.index_name)
        except DataFeedError as e:
            self._fail_safe_stop(f"Option chain fetch error while managing open position: {e}")
            return

        contract = select_live_contract(self.index_name, self.open_position["direction"], chain)
        if contract is None:
            logger.warning("Could not refresh live price for open position; will retry next poll.")
            return

        current_price = contract.ltp
        self.open_position["peak_price"] = max(self.open_position["peak_price"], current_price)

        stop = self.open_position["stop_loss"]
        if config.USE_TRAILING_STOP:
            stop = update_trailing_stop(stop, self.open_position["entry_execution_price"],
                                         self.open_position["peak_price"])
            self.open_position["stop_loss"] = stop

        now = dt.datetime.now()
        exit_reason = None
        if current_price <= stop:
            exit_reason = "Stop-loss hit"
        elif current_price >= self.open_position["target"]:
            exit_reason = "Target hit"
        elif now.time() >= config.SQUARE_OFF_TIME:
            exit_reason = "End-of-day square-off"
        elif not contract.liquidity_ok():
            exit_reason = "Abnormal spread/liquidity"

        if exit_reason:
            self._close_position(current_price, exit_reason, now)

    def _close_position(self, exit_signal_price, exit_reason, now):
        pos = self.open_position
        pnl_info = compute_net_pnl(pos["entry_signal_price"], exit_signal_price, pos["quantity"])
        self.capital += pnl_info["net_pnl"]
        self.risk_state.register_trade_result(pnl_info["net_pnl"])

        record = {
            "trade_uid": pos["trade_uid"], "mode": "paper_live", "index_name": pos["index_name"],
            "strategy": pos["strategy"], "direction": pos["direction"], "strike": pos["strike"],
            "expiry": pos["expiry"], "timeframe_min": pos["timeframe_min"],
            "signal_score": pos["signal_score"], "entry_time": pos["entry_time"],
            "entry_signal_price": pnl_info["entry_signal_price"],
            "entry_execution_price": pnl_info["entry_execution_price"],
            "exit_time": str(now), "exit_signal_price": pnl_info["exit_signal_price"],
            "exit_execution_price": pnl_info["exit_execution_price"], "quantity": pos["quantity"],
            "stop_loss": pos["stop_loss"], "target": pos["target"], "exit_reason": exit_reason,
            "gross_pnl": pnl_info["gross_pnl"], "slippage_cost": pnl_info["slippage_cost"],
            "charges": pnl_info["charges"], "net_pnl": pnl_info["net_pnl"],
            "capital_used": pos["capital_used"], "risk_amount": pos["risk_amount"],
            "reasons": "; ".join(pos["reasons"]),
        }
        database.insert_trade(record)

        try:
            telegram.send_exit_notification(record)
        except telegram.TelegramError:
            logger.warning("Exit notification failed to send (trade still recorded locally).")

        logger.info("Closed paper position: %s", record)
        self.open_position = None

    def force_square_off_all(self, reason: str = "Manual/EOD square-off"):
        if self.open_position is not None:
            try:
                chain = fetch_live_option_chain(self.index_name)
                contract = select_live_contract(self.index_name, self.open_position["direction"], chain)
                price = contract.ltp if contract else self.open_position["entry_execution_price"]
            except DataFeedError:
                price = self.open_position["entry_execution_price"]
            self._close_position(price, reason, dt.datetime.now())
