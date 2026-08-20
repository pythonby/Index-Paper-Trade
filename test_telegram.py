"""
test_telegram.py
=================
Run this after setting TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (in your
.env file, or as environment variables) to confirm your Telegram bot is
wired up correctly before starting live paper trading.

Usage:
    python test_telegram.py
"""

import os

# If you have python-dotenv installed, this will auto-load your .env file.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("(python-dotenv not installed -- reading OS environment variables directly. "
          "Run 'pip install python-dotenv' if you want .env auto-loading.)")

import config
from notify import telegram

def main():
    print("Checking Telegram configuration...")
    print(f"  TELEGRAM_BOT_TOKEN set : {'YES' if config.TELEGRAM_BOT_TOKEN else 'NO'}")
    print(f"  TELEGRAM_CHAT_ID set   : {'YES' if config.TELEGRAM_CHAT_ID else 'NO'}")

    if not config.TELEGRAM_ENABLED:
        print("\n❌ Telegram is NOT configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
              "in your .env file (see .env.example) and try again.")
        return

    print("\nSending a test message to your Telegram bot...")
    try:
        telegram.send_alert(
            "This is a TEST message from your NSE options paper-trading bot.\n"
            "If you can see this, your Telegram setup is working correctly! ✅\n\n"
            "Real entry/exit/daily-report notifications will look like this "
            "once you run 'python main.py paper' during market hours."
        )
        print("✅ Message sent successfully. Check your Telegram chat.")
    except telegram.TelegramError as e:
        print(f"❌ Failed to send message: {e}")
        print("   Double-check your bot token and chat ID, and make sure you've "
              "messaged your bot at least once (Telegram bots can't message you first).")


if __name__ == "__main__":
    main()
