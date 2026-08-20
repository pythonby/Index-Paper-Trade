import os
import time
import json
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
import requests
from SmartApi import SmartConnect

IST = ZoneInfo("Asia/Kolkata")

# ============================================================
# SIMPLE CE PAPER TEST V3
# CE BUY ONLY | NO SELLING | NO LIVE ORDERS
# ============================================================

ANGEL_API_KEY = os.environ["ANGEL_API_KEY"].strip()
ANGEL_CLIENT_ID = os.environ["ANGEL_CLIENT_ID"].strip()
ANGEL_PASSWORD = os.environ["ANGEL_PASSWORD"].strip()
ANGEL_TOTP_SECRET = os.environ["ANGEL_TOTP_SECRET"].strip()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

TIMEFRAME = os.environ.get("TIMEFRAME", "ONE_MINUTE").strip()
DURATION = int(os.environ.get("TEST_DURATION", "60"))
SLIPPAGE = float(os.environ.get("SLIPPAGE_RUPEES", "1"))
CAPITAL = float(os.environ.get("CAPITAL", "20000"))
RISK = float(os.environ.get("RISK_PER_TRADE", "250"))

# Paper-test only. These are not broker orders.
STOP_LOSS_PCT = float(os.environ.get("SL_PCT", "0.05"))
TARGET_PCT = float(os.environ.get("TARGET_PCT", "0.10"))

NIFTY_TOKEN = "99926000"
MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

INTERVAL_MINUTES = {
    "ONE_MINUTE": 1,
    "THREE_MINUTE": 3,
    "FIVE_MINUTE": 5,
    "FIFTEEN_MINUTE": 15,
    "THIRTY_MINUTE": 30,
    "ONE_HOUR": 60,
}

if TIMEFRAME not in INTERVAL_MINUTES:
    raise ValueError("TIMEFRAME must be ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE or ONE_HOUR")


def telegram(text):
    print(text, flush=True)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        print(f"Telegram error: {exc}", flush=True)


def login():
    api = SmartConnect(ANGEL_API_KEY)
    otp = pyotp.TOTP(ANGEL_TOTP_SECRET).now()
    result = api.generateSession(ANGEL_CLIENT_ID, ANGEL_PASSWORD, otp)

    if not result or not result.get("status"):
        raise RuntimeError(f"Angel One login failed: {result}")

    return api


def load_master():
    with urllib.request.urlopen(MASTER_URL, timeout=30) as response:
        return pd.DataFrame(json.loads(response.read().decode("utf-8")))


def nifty_candles(api):
    now = datetime.now(IST).replace(second=0, microsecond=0)
    minutes = max(120, INTERVAL_MINUTES[TIMEFRAME] * 60)

    params = {
        "exchange": "NSE",
        "symboltoken": NIFTY_TOKEN,
        "interval": TIMEFRAME,
        "fromdate": (now - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M"),
        "todate": now.strftime("%Y-%m-%d %H:%M"),
    }

    result = api.getCandleData(params)

    if not result or not result.get("status") or not result.get("data"):
        raise RuntimeError(f"NIFTY candle error: {result}")

    df = pd.DataFrame(
        result["data"],
        columns=["time", "open", "high", "low", "close", "volume"],
    )

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna().reset_index(drop=True)


def find_nearest_ce(master, spot):
    m = master.copy()

    for col in ["exch_seg", "name", "instrumenttype", "symbol"]:
        if col not in m.columns:
            raise RuntimeError(f"Master file missing column: {col}")

    x = m[
        (m["exch_seg"].astype(str).str.upper() == "NFO")
        & (m["name"].astype(str).str.upper() == "NIFTY")
        & (m["instrumenttype"].astype(str).str.upper() == "OPTIDX")
        & (m["symbol"].astype(str).str.upper().str.endswith("CE"))
    ].copy()

    if x.empty:
        raise RuntimeError("No NIFTY CE contracts found in master")

    # Angel One master normally stores expiry as DDMMMYYYY.
    def parse_expiry(value):
        value = str(value).strip().upper()
        for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass
        return None

    x["expiry_date"] = x["expiry"].map(parse_expiry)
    today = datetime.now(IST).date()
    x = x[x["expiry_date"].notna() & (x["expiry_date"] >= today)]

    if x.empty:
        raise RuntimeError("No active NIFTY CE expiry found")

    nearest_expiry = x["expiry_date"].min()
    x = x[x["expiry_date"] == nearest_expiry].copy()

    x["strike_num"] = pd.to_numeric(x["strike"], errors="coerce")
    # Some Angel One master versions store strikes multiplied by 100.
    if x["strike_num"].median() > spot * 10:
        x["strike_num"] = x["strike_num"] / 100

    x = x[x["strike_num"].notna() & (x["strike_num"] > 0)]

    if x.empty:
        raise RuntimeError("No valid NIFTY CE strikes found")

    row = x.iloc[(x["strike_num"] - spot).abs().argmin()]

    return {
        "symbol": str(row["symbol"]),
        "token": str(row["token"]),
        "strike": float(row["strike_num"]),
        "expiry": str(row["expiry"]),
        "lotsize": int(float(row["lotsize"])),
    }


def option_ltp(api, token):
    result = api.getMarketData("LTP", {"NFO": [token]})

    if not result or not result.get("status"):
        raise RuntimeError(f"Option LTP error: {result}")

    data = result.get("data", {})
    fetched = data.get("fetchedData", []) if isinstance(data, dict) else []

    if fetched:
        return float(fetched[0]["ltp"])

    if isinstance(data, dict) and data.get("ltp") is not None:
        return float(data["ltp"])

    raise RuntimeError(f"Option LTP missing: {result}")


def buy_signal(df):
    # Simple paper-test signal:
    # EMA20 trend + breakout of previous 10 candles.
    if len(df) < 30:
        return False

    work = df.copy()
    work["ema20"] = work["close"].ewm(span=20, adjust=False).mean()

    last = work.iloc[-1]
    previous_high = work.iloc[-11:-1]["high"].max()

    return bool(
        last["close"] > last["ema20"]
        and last["close"] > previous_high
        and last["close"] > last["open"]
    )


def market_open():
    now = datetime.now(IST)
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= now <= end


def main():
    telegram(
        "🟢 CE PAPER TEST V3 STARTED\n"
        f"NIFTY | {TIMEFRAME} | {DURATION} min\n"
        f"Capital ₹{CAPITAL:.0f} | Risk ₹{RISK:.0f}\n"
        "CE BUY ONLY | NO SELLING | LIVE OFF\n"
        f"NIFTY TOKEN = {NIFTY_TOKEN}"
    )

    api = login()
    master = load_master()

    open_trade = None
    last_candle = None
    trades = 0
    start_time = time.time()

    while time.time() - start_time < DURATION * 60:
        if not market_open():
            time.sleep(30)
            continue

        try:
            df = nifty_candles(api)
            candle_id = str(df.iloc[-1]["time"])

            # Manage existing PAPER position.
            if open_trade:
                price = option_ltp(api, open_trade["token"])

                if price <= open_trade["sl"] or price >= open_trade["target"]:
                    exit_price = max(0.0, price - SLIPPAGE)
                    pnl = (exit_price - open_trade["entry"]) * open_trade["qty"]

                    reason = "SL" if price <= open_trade["sl"] else "TARGET"

                    telegram(
                        "🔴 PAPER CE EXIT\n"
                        f"{open_trade['symbol']}\n"
                        f"Reason: {reason}\n"
                        f"Entry ₹{open_trade['entry']:.2f}\n"
                        f"Exit ₹{exit_price:.2f}\n"
                        f"P&L ₹{pnl:.2f}\n"
                        "LIVE ORDER = 0"
                    )

                    open_trade = None

            # Only evaluate a new signal once per new candle.
            if candle_id != last_candle:
                last_candle = candle_id

                if open_trade is None and buy_signal(df):
                    spot = float(df.iloc[-1]["close"])
                    contract = find_nearest_ce(master, spot)
                    ltp = option_ltp(api, contract["token"])

                    entry = ltp + SLIPPAGE
                    sl = entry * (1 - STOP_LOSS_PCT)
                    target = entry * (1 + TARGET_PCT)
                    qty = contract["lotsize"]

                    # Risk/capital check for paper position.
                    if entry * qty <= CAPITAL and (entry - sl) * qty <= RISK:
                        open_trade = {
                            **contract,
                            "entry": entry,
                            "sl": sl,
                            "target": target,
                            "qty": qty,
                        }
                        trades += 1

                        telegram(
                            "🟢 PAPER CE BUY SIGNAL\n"
                            f"{contract['symbol']}\n"
                            f"Strike {contract['strike']:.0f}\n"
                            f"Expiry {contract['expiry']}\n"
                            f"Qty {qty}\n"
                            f"LTP ₹{ltp:.2f}\n"
                            f"Paper Entry ₹{entry:.2f}\n"
                            f"SL ₹{sl:.2f}\n"
                            f"Target ₹{target:.2f}\n"
                            f"Slippage ₹{SLIPPAGE:.2f}\n"
                            "⚠️ LIVE ORDER = 0"
                        )

            time.sleep(max(15, INTERVAL_MINUTES[TIMEFRAME] * 60))

        except Exception as exc:
            print(f"⚠️ {exc}", flush=True)
            time.sleep(30)

    telegram(
        "⏹️ CE PAPER TEST V3 FINISHED\n"
        f"Paper trades: {trades}\n"
        "LIVE ORDERS: 0"
    )


if __name__ == "__main__":
    main()
