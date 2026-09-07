"""
notify/telegram.py
===================
All Telegram messages MUST include the "PAPER TRADE — NOT REAL MONEY" banner
(safety rule #10) and must never imply a real order was executed.

Reads credentials from environment variables only -- never hard-coded.
"""

import logging
import requests

import config

logger = logging.getLogger("notify.telegram")

# Change this to whatever name you want to appear at the top of every
# Telegram message from this bot -- set in config.py, not hard-coded here.
BOT_NAME = config.BOT_DISPLAY_NAME

BANNER = "⚠️ PAPER TRADE — NOT REAL MONEY ⚠️"


class TelegramError(Exception):
    pass


def _send(text: str, parse_mode: str = None):
    if not config.TELEGRAM_ENABLED:
        logger.info("Telegram not configured (TELEGRAM_BOT_TOKEN/CHAT_ID missing); "
                     "message suppressed. Set them in your .env to enable notifications.")
        logger.info("Message content:\n%s", text)
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resp = requests.post(url, data=payload, timeout=8)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        # Per safety rules: if Telegram fails, do NOT crash the trading loop,
        # but the caller should treat this as a fail-safe signal if it
        # persists (e.g. stop generating new trades until resolved).
        raise TelegramError(str(e)) from e


def send_entry_notification(trade: dict):
    text = f"""{BOT_NAME}
🚨 PAPER TRADE ENTRY
{BANNER}

Index: {trade['index_name']}
Direction: BUY {trade['direction']}
Strike: {trade['strike']}
Expiry: {trade['expiry']}
Timeframe: {trade['timeframe_min']}M

Spot: Rs{trade.get('spot', 'N/A')}
Option LTP: Rs{trade['entry_signal_price']}
Simulated Entry: Rs{trade['entry_execution_price']}

Stop Loss: Rs{trade['stop_loss']}
Target: Rs{trade['target']}

Quantity: {trade['quantity']}
Capital Used: Rs{trade['capital_used']}
Risk: Rs{trade['risk_amount']}

Strategy: {trade['strategy']}

Slippage Included: YES
Trading Costs Included: YES

Confidence: {trade['signal_score']}/100

Reason:
{chr(10).join('- ' + r for r in trade['reasons'])}
"""
    return _send(text)


def send_exit_notification(trade: dict):
    result = "PROFIT" if trade["net_pnl"] > 0 else "LOSS"
    text = f"""{BOT_NAME}
📊 PAPER TRADE EXIT
{BANNER}

Index: {trade['index_name']}
Position: BUY {trade['direction']}
Strike: {trade['strike']}

Entry: Rs{trade['entry_execution_price']}
Exit: Rs{trade['exit_execution_price']}

Quantity: {trade['quantity']}

Gross P&L: Rs{trade['gross_pnl']}
Slippage: Rs{trade['slippage_cost']}
Charges: Rs{trade['charges']}
Net P&L: Rs{trade['net_pnl']}

Exit Reason: {trade['exit_reason']}

Result: {result}
"""
    return _send(text)


def send_daily_report(report_text: str):
    return _send(f"{BOT_NAME}\n{BANNER}\n\n{report_text}")


def send_backtest_summary(summary_text: str):
    """Telegram has a 4096-character message limit, so long comparison
    tables are truncated with a pointer to the full GitHub Actions log.
    summary_text may contain an HTML <pre>...</pre> block (built by
    reports.performance.build_telegram_summary) for a monospace-aligned
    table -- sent with parse_mode=HTML so Telegram actually renders it
    as a fixed-width grid instead of raw text.

    IMPORTANT: naively slicing HTML text with str[:n] can cut in the
    middle of a tag or an escaped entity (e.g. "&amp;" -> "&am"), which
    makes Telegram reject the ENTIRE message as invalid HTML (not just
    truncate it -- nothing gets delivered at all). So truncation here
    always backs up to a safe newline boundary and force-closes any
    <pre> tag left open by the cut.
    """
    max_len = 3500
    if len(summary_text) > max_len:
        truncated = summary_text[:max_len]
        last_nl = truncated.rfind("\n")
        if last_nl > 0:
            truncated = truncated[:last_nl]
        if truncated.count("<pre>") > truncated.count("</pre>"):
            truncated += "</pre>"
        summary_text = truncated + "\n\n... (truncated -- see full results in the GitHub Actions log for this run)"
    return _send(f"{BOT_NAME}\n📊 BACKTEST REPORT\n{BANNER}\n\n{summary_text}", parse_mode="HTML")


def send_alert(message: str):
    """For fail-safe / halt notifications (data feed down, Telegram issue on
    a prior attempt, daily loss limit hit, etc.)."""
    return _send(f"{BOT_NAME}\n🛑 SYSTEM ALERT\n{BANNER}\n\n{message}")
