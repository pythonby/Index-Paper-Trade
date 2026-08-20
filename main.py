"""
main.py
=======
Entry point for the paper-trading system.

Usage:
    python main.py backtest        # run full backtest + walk-forward validation
    python main.py paper           # start the live paper-trading loop (simulated only)
    python main.py status          # print the first-run status block and exit

See README.md for full setup instructions.
"""

import sys
import time
import logging
import datetime as dt

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; fall back to real OS environment variables

import config
from db import database
from data.fetcher import fetch_index_history, DataFeedError
from backtest.engine import prepare_dataframe, run_backtest
from validation.walkforward import rolling_walk_forward, summarize_folds
from reports.performance import compute_metrics, build_comparison_table, is_robust, format_daily_report
from strategies.vwap_ema_momentum import VwapEmaMomentum
from strategies.opening_range_breakout import OpeningRangeBreakout
from strategies.trend_pullback import TrendPullback
from paper_trading.engine import PaperTradingEngine
from notify import telegram


def setup_logging():
    import os
    os.makedirs(config.LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(f"{config.LOG_DIR}/system.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def print_first_run_status():
    print("=" * 70)
    print("NSE INDEX OPTIONS PAPER-TRADING SYSTEM")
    print("=" * 70)
    print(f"Current date/time      : {dt.datetime.now()}")
    now_t = dt.datetime.now().time()
    market_open = config.MARKET_OPEN_TIME <= now_t <= config.MARKET_CLOSE_TIME
    print(f"Market status           : {'OPEN' if market_open else 'CLOSED'}")
    print(f"Available capital       : Rs {config.STARTING_CAPITAL:,.2f}")
    print(f"Enabled strategies      : {[k for k, v in config.ENABLED_STRATEGIES.items() if v]}")
    print(f"Data-feed status        : Not yet checked (checked on first poll)")
    print(f"Slippage configuration  : {config.SLIPPAGE_PCT_OF_PREMIUM*100:.2f}% of premium per fill "
          f"(min Rs{config.MIN_SLIPPAGE_RS_PER_LOT})")
    print(f"Cost configuration      : brokerage Rs{config.BROKERAGE_PER_ORDER}/order "
          f"(or {config.BROKERAGE_PCT_OF_TURNOVER*100:.3f}% whichever lower), "
          f"exchange {config.EXCHANGE_TXN_CHARGE_PCT*100:.4f}%, GST {config.GST_PCT*100:.0f}%, "
          f"STT {config.STT_SELL_PCT*100:.4f}% (sell side)")
    print(f"Risk per trade          : {config.RISK_PER_TRADE_PCT*100:.2f}% of available capital")
    print(f"Max daily loss          : {config.MAX_DAILY_LOSS_PCT*100:.1f}% of starting capital "
          f"(Rs {config.STARTING_CAPITAL*config.MAX_DAILY_LOSS_PCT:.2f})")
    print(f"Max trades/day          : {config.MAX_TRADES_PER_DAY}")
    print(f"Max consecutive losses  : {config.MAX_CONSECUTIVE_LOSSES}")
    print(f"Min signal score        : {config.MIN_SIGNAL_SCORE}/100")
    print(f"Telegram configured     : {'YES' if config.TELEGRAM_ENABLED else 'NO (set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env)'}")
    print("=" * 70)
    print("PAPER TRADING ONLY. No real-money orders will ever be placed.")
    print("=" * 70)


def build_strategy_set():
    strategies = []
    if config.ENABLED_STRATEGIES.get("vwap_ema_momentum"):
        strategies.append(VwapEmaMomentum())
    if config.ENABLED_STRATEGIES.get("opening_range_breakout"):
        strategies.append(OpeningRangeBreakout(range_minutes=config.ORB_RANGE_MINUTES_CANDIDATES[0]))
    if config.ENABLED_STRATEGIES.get("trend_pullback"):
        strategies.append(TrendPullback())
    return strategies


def run_backtest_mode():
    print_first_run_status()
    print("\nStarting backtest across all instruments/timeframes/strategies...\n")

    all_results = {}
    any_robust = False

    for index_name in config.INSTRUMENTS:
        try:
            raw_df = fetch_index_history(index_name, config.DEFAULT_TIMEFRAME_MIN, period="60d")
        except DataFeedError as e:
            print(f"[{index_name}] Could not fetch historical data: {e}")
            print(f"[{index_name}] Skipping -- see README for free-data-source limitations.")
            continue

        for ema_fast in config.EMA_FAST_CANDIDATES:
            for ema_slow in config.EMA_SLOW_CANDIDATES:
                if ema_fast >= ema_slow:
                    continue

                df = prepare_dataframe(raw_df, ema_fast, ema_slow, config.ORB_RANGE_MINUTES_CANDIDATES[0])

                for strat_builder, strat_name in [
                    (lambda: [VwapEmaMomentum()], "vwap_ema_momentum"),
                    (lambda: [OpeningRangeBreakout()], "opening_range_breakout"),
                    (lambda: [TrendPullback()], "trend_pullback"),
                ]:
                    if not config.ENABLED_STRATEGIES.get(strat_name):
                        continue

                    folds = rolling_walk_forward(df, index_name, strat_builder, config.DEFAULT_TIMEFRAME_MIN)
                    fold_summary = summarize_folds(folds)

                    full_result = run_backtest(df, index_name, strat_builder(), config.DEFAULT_TIMEFRAME_MIN)
                    metrics = compute_metrics(full_result.trades, full_result.equity_curve, config.STARTING_CAPITAL)

                    key = (f"{strat_name} (EMA{ema_fast}/{ema_slow})", index_name, config.DEFAULT_TIMEFRAME_MIN)
                    all_results[key] = metrics

                    if fold_summary["overall_robust"]:
                        any_robust = True
                    print(f"[{index_name}] {strat_name} EMA{ema_fast}/{ema_slow}: "
                          f"{metrics['num_trades']} trades, net Rs{metrics['net_pnl']}, "
                          f"walk-forward robust={fold_summary['overall_robust']} ({fold_summary['reason']})")

    print("\n" + "=" * 100)
    print("STRATEGY COMPARISON TABLE")
    print("=" * 100)
    print(build_comparison_table(all_results))

    print("\n" + "=" * 100)
    if any_robust:
        robust_keys = sorted(all_results.items(), key=lambda kv: (kv[1]["net_pnl"] or -1e18), reverse=True)[:3]
        print("SELECTED STRATEGIES (top robust candidates by net P&L, capped at 3):")
        for key, m in robust_keys:
            print(f"  - {key[0]} on {key[1]}: net Rs{m['net_pnl']}, PF={m['profit_factor']}, "
                  f"win rate={m['win_rate']}%")
    else:
        print("NO PROFITABLE STRATEGY FOUND UNDER CURRENT TEST CONDITIONS.")
        print("(All tested configurations either lost money after realistic costs/slippage,")
        print(" or failed walk-forward out-of-sample validation. See table above.)")
    print("=" * 100)


def _send_end_of_day_report():
    """Pulls today's paper trades from the DB and sends the daily Telegram report.
    Called both on square-off and on graceful shutdown."""
    import datetime as _dt
    today_str = _dt.date.today().isoformat()
    all_trades = database.fetch_trades(mode="paper_live")
    todays_trades = [t for t in all_trades if str(t.get("entry_time", "")).startswith(today_str)]

    if not todays_trades:
        print("No trades were taken today; skipping daily report.")
        return

    running = config.STARTING_CAPITAL
    peak = running
    max_dd = 0.0
    strategy_pnls = {}
    for t in todays_trades:
        running += t["net_pnl"]
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        strategy_pnls[t["strategy"]] = strategy_pnls.get(t["strategy"], 0.0) + t["net_pnl"]

    report_text = format_daily_report(
        starting_capital=config.STARTING_CAPITAL,
        ending_capital=round(running, 2),
        trades=todays_trades,
        max_drawdown=round(max_dd, 2),
        strategy_pnls=strategy_pnls,
    )
    print(report_text)
    try:
        telegram.send_daily_report(report_text)
    except telegram.TelegramError:
        logging.getLogger("main").warning("Could not send daily report to Telegram (logged locally instead).")


def run_paper_trading_mode():
    """
    Runs the live polling loop until end-of-day square-off, then EXITS
    (does not sleep forever). This is intentional for cloud/scheduled
    execution (e.g. a GitHub Actions cron job that triggers fresh every
    trading day) -- there is no need to keep a process idling all night.
    """
    print_first_run_status()
    database.init_db()

    engines = []
    for index_name in config.INSTRUMENTS:
        strategies = build_strategy_set()
        engines.append(PaperTradingEngine(index_name, strategies, config.DEFAULT_TIMEFRAME_MIN))

    print(f"\nStarting live paper-trading loop for {config.INSTRUMENTS}. Press Ctrl+C to stop safely.\n")

    try:
        while True:
            now_t = dt.datetime.now().time()
            if now_t >= config.SQUARE_OFF_TIME:
                for eng in engines:
                    eng.force_square_off_all("End-of-day square-off")
                print("Market square-off time reached. Sending daily report and exiting.")
                _send_end_of_day_report()
                break

            if now_t < config.MARKET_OPEN_TIME:
                print(f"Waiting for market open ({config.MARKET_OPEN_TIME})... current time {now_t}")
                time.sleep(30)
                continue

            for eng in engines:
                try:
                    eng.poll_once()
                except Exception as e:
                    logging.getLogger("main").exception("Unhandled error in poll_once for %s: %s",
                                                          eng.index_name, e)
                    telegram.send_alert(f"Unhandled error for {eng.index_name}: {e}. "
                                         f"Halting that instrument's trading until restarted.")
                    eng.halted = True

            time.sleep(30)  # poll interval
    except KeyboardInterrupt:
        print("\nShutdown requested. Squaring off any open paper positions safely...")
        for eng in engines:
            eng.force_square_off_all("Manual shutdown")
        _send_end_of_day_report()
        print("All positions closed. Safe to exit.")


if __name__ == "__main__":
    setup_logging()
    database.init_db()

    mode = sys.argv[1] if len(sys.argv) > 1 else "status"

    if mode == "backtest":
        run_backtest_mode()
    elif mode == "paper":
        run_paper_trading_mode()
    elif mode == "status":
        print_first_run_status()
    else:
        print(f"Unknown mode '{mode}'. Use: backtest | paper | status")
        sys.exit(1)
