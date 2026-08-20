"""
options/selector.py
====================
Two responsibilities:

1. BACKTEST option premium reconstruction via Black-Scholes, since no free
   historical NSE option premium series exists (see data/fetcher.py docstring).
   This is explicitly a MODEL, using config.BACKTEST_ASSUMED_IV -- not a
   claim about real historical premiums.

2. LIVE strike/expiry selection from the real NSE option chain, including
   liquidity and bid/ask spread checks, for actual paper trading.
"""

import math
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional

import config

LOT_SIZES = {
    # Approximate NSE lot sizes -- these change periodically with exchange
    # circulars. VERIFY current lot size before relying on this for sizing.
    "NIFTY": 25,
    "BANKNIFTY": 15,
    "FINNIFTY": 40,
}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black_scholes_price(spot: float, strike: float, t_years: float, iv: float,
                         r: float, option_type: str) -> float:
    """Standard Black-Scholes (no dividend yield adjustment) for index options."""
    if t_years <= 0:
        intrinsic = max(spot - strike, 0) if option_type == "CE" else max(strike - spot, 0)
        return round(intrinsic, 2)
    if iv <= 0:
        iv = 0.0001

    d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * t_years) / (iv * math.sqrt(t_years))
    d2 = d1 - iv * math.sqrt(t_years)

    if option_type == "CE":
        price = spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    elif option_type == "PE":
        price = strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    else:
        raise ValueError("option_type must be CE or PE")

    return max(round(price, 2), 0.05)


@dataclass
class OptionContract:
    index: str
    option_type: str          # "CE" or "PE"
    strike: float
    expiry: date
    spot: float
    ltp: float
    intrinsic_value: float
    time_value: float
    days_to_expiry: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    oi: Optional[float] = None
    volume: Optional[float] = None
    lot_size: int = 0

    @property
    def bid_ask_spread_pct(self) -> Optional[float]:
        if self.bid is None or self.ask is None or self.ltp <= 0:
            return None
        return (self.ask - self.bid) / self.ltp

    def liquidity_ok(self) -> bool:
        if self.bid_ask_spread_pct is not None and self.bid_ask_spread_pct > config.MAX_BID_ASK_SPREAD_PCT:
            return False
        if self.ltp < config.MIN_OPTION_LTP:
            return False
        if self.oi is not None and self.oi <= 0:
            return False
        return True


def nearest_strike(spot: float, strike_step: int) -> float:
    return round(spot / strike_step) * strike_step


STRIKE_STEPS = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}


def select_backtest_contract(index: str, option_type: str, spot: float,
                              signal_time: datetime, expiry: date) -> OptionContract:
    """
    Build a synthetic ATM (or near-ATM per config) contract for backtesting,
    priced with Black-Scholes using the configured assumed IV.
    """
    step = STRIKE_STEPS.get(index, 50)
    strike = nearest_strike(spot, step)

    days_to_expiry = max((expiry - signal_time.date()).days, 0)
    # add fraction of a day remaining until 15:30 expiry cutoff
    t_years = max(days_to_expiry, 0.25) / 365.0

    iv = config.BACKTEST_ASSUMED_IV.get(index, 0.15)
    price = black_scholes_price(spot, strike, t_years, iv, config.BACKTEST_RISK_FREE_RATE, option_type)

    intrinsic = max(spot - strike, 0) if option_type == "CE" else max(strike - spot, 0)
    time_value = max(price - intrinsic, 0)

    return OptionContract(
        index=index,
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        spot=spot,
        ltp=price,
        intrinsic_value=round(intrinsic, 2),
        time_value=round(time_value, 2),
        days_to_expiry=days_to_expiry,
        bid=round(price * 0.995, 2),
        ask=round(price * 1.005, 2),
        oi=1_000_000,   # assume ample synthetic liquidity in backtest; real liquidity
                         # is NOT modeled here since we have no historical OI/volume data
        volume=100_000,
        lot_size=LOT_SIZES.get(index, 25),
    )


def select_live_contract(index: str, option_type: str, option_chain_json: dict) -> Optional[OptionContract]:
    """
    Pick the best real contract from a live NSE option-chain snapshot
    (as returned by data.fetcher.fetch_live_option_chain), applying the
    liquidity / spread / strike-proximity filters from config.

    Returns None if no suitable contract is found -- caller must treat
    that as NO TRADE, never guess/fabricate a contract.
    """
    records = option_chain_json.get("records", {})
    spot = records.get("underlyingValue")
    all_data = records.get("data", [])
    expiry_dates = records.get("expiryDates", [])
    if not spot or not all_data or not expiry_dates:
        return None

    nearest_expiry_str = expiry_dates[0]
    try:
        nearest_expiry = datetime.strptime(nearest_expiry_str, "%d-%b-%Y").date()
    except ValueError:
        return None

    days_to_expiry = (nearest_expiry - datetime.now().date()).days
    if not (config.MIN_DAYS_TO_EXPIRY <= days_to_expiry <= config.MAX_DAYS_TO_EXPIRY):
        # roll to next expiry in the list if the nearest one is outside our window
        for exp_str in expiry_dates[1:]:
            try:
                exp = datetime.strptime(exp_str, "%d-%b-%Y").date()
            except ValueError:
                continue
            dte = (exp - datetime.now().date()).days
            if config.MIN_DAYS_TO_EXPIRY <= dte <= config.MAX_DAYS_TO_EXPIRY:
                nearest_expiry, days_to_expiry = exp, dte
                break

    step = STRIKE_STEPS.get(index, 50)
    atm_strike = nearest_strike(spot, step)
    key = "CE" if option_type == "CE" else "PE"

    candidates = []
    for row in all_data:
        if row.get("expiryDate") != nearest_expiry_str:
            continue
        leg = row.get(key)
        if not leg:
            continue
        strike = row.get("strikePrice")
        if strike is None:
            continue
        strike_dist = abs(strike - atm_strike) / step
        if strike_dist > config.MAX_STRIKES_FROM_ATM:
            continue

        contract = OptionContract(
            index=index,
            option_type=option_type,
            strike=strike,
            expiry=nearest_expiry,
            spot=spot,
            ltp=leg.get("lastPrice", 0.0),
            intrinsic_value=max(spot - strike, 0) if option_type == "CE" else max(strike - spot, 0),
            time_value=0.0,
            days_to_expiry=days_to_expiry,
            bid=leg.get("bidprice"),
            ask=leg.get("askPrice"),
            oi=leg.get("openInterest"),
            volume=leg.get("totalTradedVolume"),
            lot_size=LOT_SIZES.get(index, 25),
        )
        contract.time_value = round(contract.ltp - contract.intrinsic_value, 2)

        if contract.ltp <= 0:
            continue
        if contract.ltp > config.STARTING_CAPITAL * config.MAX_OPTION_LTP_PCT_OF_CAPITAL:
            continue
        candidates.append((strike_dist, contract))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    for _, contract in candidates:
        if contract.liquidity_ok():
            return contract

    return None  # nothing passed liquidity checks -> NO TRADE
