"""
costs/model.py
===============
Realistic slippage + transaction-cost model for options BUY trades.

Nothing here is hidden: every component that reduces P&L is broken out
separately and returned so it can be logged, displayed in Telegram
messages, and stored in the trade database.
"""

from dataclasses import dataclass
import config


@dataclass
class FillResult:
    signal_price: float
    execution_price: float
    slippage_rs_per_unit: float


@dataclass
class CostBreakdown:
    brokerage: float
    exchange_txn_charge: float
    sebi_charges: float
    stamp_duty: float
    stt: float
    gst: float
    total: float


def apply_slippage(signal_price: float, side: str, lot_size: int) -> FillResult:
    """
    side: 'BUY' or 'SELL' (SELL here always means squaring off a long option, not naked selling)
    Adverse slippage: on BUY, we pay more than signal price; on SELL, we receive less.
    """
    slip_per_unit = max(
        signal_price * config.SLIPPAGE_PCT_OF_PREMIUM,
        config.MIN_SLIPPAGE_RS_PER_LOT,
    )
    if side == "BUY":
        exec_price = signal_price + slip_per_unit
    elif side == "SELL":
        exec_price = max(signal_price - slip_per_unit, 0.05)
    else:
        raise ValueError("side must be BUY or SELL")

    return FillResult(
        signal_price=signal_price,
        execution_price=round(exec_price, 2),
        slippage_rs_per_unit=round(slip_per_unit, 2),
    )


def compute_costs(entry_price: float, exit_price: float, quantity: int) -> CostBreakdown:
    """
    Computes round-trip charges for a BUY-to-open, SELL-to-close options trade.
    quantity = total units (lots * lot_size).
    """
    buy_turnover = entry_price * quantity
    sell_turnover = exit_price * quantity
    total_turnover = buy_turnover + sell_turnover

    # Brokerage: flat per order (2 orders: entry + exit) or % of turnover, whichever lower,
    # mirroring typical Indian discount-broker "flat or X% whichever is lower" pricing.
    brokerage_entry = min(config.BROKERAGE_PER_ORDER, buy_turnover * config.BROKERAGE_PCT_OF_TURNOVER)
    brokerage_exit = min(config.BROKERAGE_PER_ORDER, sell_turnover * config.BROKERAGE_PCT_OF_TURNOVER)
    brokerage = brokerage_entry + brokerage_exit

    exchange_txn_charge = total_turnover * config.EXCHANGE_TXN_CHARGE_PCT
    sebi_charges = total_turnover * config.SEBI_CHARGES_PCT
    stamp_duty = buy_turnover * config.STAMP_DUTY_BUY_PCT
    stt = sell_turnover * config.STT_SELL_PCT

    gst = (brokerage + exchange_txn_charge) * config.GST_PCT

    total = brokerage + exchange_txn_charge + sebi_charges + stamp_duty + stt + gst

    return CostBreakdown(
        brokerage=round(brokerage, 2),
        exchange_txn_charge=round(exchange_txn_charge, 2),
        sebi_charges=round(sebi_charges, 2),
        stamp_duty=round(stamp_duty, 2),
        stt=round(stt, 2),
        gst=round(gst, 2),
        total=round(total, 2),
    )


def net_pnl(entry_signal_price: float, exit_signal_price: float, quantity: int):
    """
    Full pipeline: signal prices -> slippage-adjusted fills -> gross P&L -> costs -> net P&L.
    Returns a dict with every component visible.
    """
    entry_fill = apply_slippage(entry_signal_price, "BUY", quantity)
    exit_fill = apply_slippage(exit_signal_price, "SELL", quantity)

    gross_pnl = (exit_fill.execution_price - entry_fill.execution_price) * quantity
    costs = compute_costs(entry_fill.execution_price, exit_fill.execution_price, quantity)
    net = gross_pnl - costs.total

    total_slippage_cost = (entry_fill.slippage_rs_per_unit + exit_fill.slippage_rs_per_unit) * quantity

    return {
        "entry_signal_price": entry_signal_price,
        "entry_execution_price": entry_fill.execution_price,
        "exit_signal_price": exit_signal_price,
        "exit_execution_price": exit_fill.execution_price,
        "quantity": quantity,
        "gross_pnl": round(gross_pnl, 2),
        "slippage_cost": round(total_slippage_cost, 2),
        "charges": costs.total,
        "charges_breakdown": costs,
        "net_pnl": round(net, 2),
    }
