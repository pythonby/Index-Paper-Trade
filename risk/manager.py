"""
risk/manager.py
================
All position sizing and risk-limit enforcement lives here. The strategy
modules never decide position size or whether the daily loss limit has
been hit -- that responsibility is centralized to avoid inconsistency.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import config


@dataclass
class DailyRiskState:
    trading_date: date
    starting_capital: float
    current_capital: float
    realized_pnl_today: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    trading_halted: bool = False
    halt_reason: Optional[str] = None

    def register_trade_result(self, net_pnl: float):
        self.realized_pnl_today += net_pnl
        self.current_capital += net_pnl
        self.trades_today += 1
        if net_pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        self._check_halt_conditions()

    def _check_halt_conditions(self):
        max_daily_loss = self.starting_capital * config.MAX_DAILY_LOSS_PCT
        if self.realized_pnl_today <= -max_daily_loss:
            self.trading_halted = True
            self.halt_reason = (
                f"Max daily loss limit hit: Rs {abs(self.realized_pnl_today):.2f} "
                f"(limit Rs {max_daily_loss:.2f})"
            )
        elif self.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            self.trading_halted = True
            self.halt_reason = f"{self.consecutive_losses} consecutive losses hit the limit"
        elif self.trades_today >= config.MAX_TRADES_PER_DAY:
            self.trading_halted = True
            self.halt_reason = f"Max trades per day ({config.MAX_TRADES_PER_DAY}) reached"


@dataclass
class PositionSizeResult:
    quantity: int
    lots: int
    capital_used: float
    risk_amount: float
    stop_loss_price: float
    target_price: float
    rejected: bool = False
    rejection_reason: Optional[str] = None


def compute_position_size(entry_price: float, lot_size: int, available_capital: float
                           ) -> PositionSizeResult:
    """
    Sizing logic:
    - risk_amount = available_capital * RISK_PER_TRADE_PCT
    - stop_loss defined as STOP_LOSS_PCT_OF_PREMIUM below entry premium
    - per-unit risk = entry_price * STOP_LOSS_PCT_OF_PREMIUM
    - quantity (in units) = risk_amount / per_unit_risk, rounded DOWN to whole lots
    - capped so capital deployed never exceeds MAX_CAPITAL_DEPLOY_PCT of capital
    """
    if entry_price <= 0 or lot_size <= 0:
        return PositionSizeResult(0, 0, 0, 0, 0, 0, rejected=True,
                                   rejection_reason="Invalid entry price or lot size")

    risk_amount = available_capital * config.RISK_PER_TRADE_PCT
    per_unit_risk = entry_price * config.STOP_LOSS_PCT_OF_PREMIUM

    if per_unit_risk <= 0:
        return PositionSizeResult(0, 0, 0, 0, 0, 0, rejected=True,
                                   rejection_reason="Per-unit risk computed as zero")

    max_units_by_risk = risk_amount / per_unit_risk
    max_lots_by_risk = int(max_units_by_risk // lot_size)

    max_capital_for_trade = available_capital * config.MAX_CAPITAL_DEPLOY_PCT
    max_lots_by_capital = int(max_capital_for_trade // (entry_price * lot_size))

    lots = min(max_lots_by_risk, max_lots_by_capital)

    if lots < 1:
        return PositionSizeResult(0, 0, 0, 0, 0, 0, rejected=True,
                                   rejection_reason=(
                                       "Position size rounds down to 0 lots given current "
                                       "capital/risk settings and this option's premium"
                                   ))

    quantity = lots * lot_size
    capital_used = round(entry_price * quantity, 2)
    actual_risk = round(per_unit_risk * quantity, 2)

    stop_loss_price = round(entry_price * (1 - config.STOP_LOSS_PCT_OF_PREMIUM), 2)
    target_price = round(entry_price * (1 + config.TARGET_PCT_OF_PREMIUM), 2)

    risk_reward = config.TARGET_PCT_OF_PREMIUM / config.STOP_LOSS_PCT_OF_PREMIUM
    if risk_reward < config.MIN_RISK_REWARD_RATIO:
        return PositionSizeResult(0, 0, 0, 0, 0, 0, rejected=True,
                                   rejection_reason=(
                                       f"Risk/reward {risk_reward:.2f} below minimum "
                                       f"{config.MIN_RISK_REWARD_RATIO}"
                                   ))

    return PositionSizeResult(
        quantity=quantity,
        lots=lots,
        capital_used=capital_used,
        risk_amount=actual_risk,
        stop_loss_price=stop_loss_price,
        target_price=target_price,
        rejected=False,
    )


def update_trailing_stop(current_stop: float, entry_price: float, peak_price: float) -> float:
    """Ratchets the stop up (never down) once the trigger threshold is reached."""
    trigger_price = entry_price * (1 + config.TRAILING_STOP_TRIGGER_PCT)
    if peak_price < trigger_price:
        return current_stop
    new_stop = peak_price * (1 - config.TRAILING_STOP_GIVEBACK_PCT)
    return max(current_stop, new_stop)
