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
import argparse
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
from reports.performance import compute_metrics, build_comparison_table, is_robust, format_daily_report, format_period_report
from strategies.vwap_ema_momentum import VwapEmaMomentum
from strategies.opening_range_breakout import OpeningRangeBreakout
from strategies.trend_pullback import TrendPullback
from strategies.mean_reversion import MeanReversion
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
    if config.ENABLED_STRATEGIES.get("mean_reversion"):
        strategies.append(MeanReversion())
    return strategies


def run_backtest_mode(timeframe_arg: str = None, index_arg: str = None):
    print_first_run_status()

    if timeframe_arg is None or timeframe_arg == "default":
        timeframes = config.BACKTEST_TIMEFRAMES_MIN
    elif timeframe_arg == "all":
        timeframes = config.TIMEFRAMES_MIN
    else:
        timeframes = [int(timeframe_arg)]

    if index_arg is None or index_arg == "all":
        instruments = config.INSTRUMENTS
    else:
        instruments = [index_arg.upper()]

    print(f"\nStarting backtest across instruments {instruments} x timeframes {timeframes} x strategies...\n")
    print("(Testing more timeframes/instruments/EMA combinations takes longer -- use --timeframe "
          "and --index to narrow a run, or 'all' for a full sweep.)\n")

    all_results = {}
    any_robust = False

    strategy_registry = [
        (lambda: [VwapEmaMomentum()], "vwap_ema_momentum"),
        (lambda: [OpeningRangeBreakout()], "opening_range_breakout"),
        (lambda: [TrendPullback()], "trend_pullback"),
        (lambda: [MeanReversion()], "mean_reversion"),
    ]

    for index_name in instruments:
        for timeframe_min in timeframes:
            period = "7d" if timeframe_min == 1 else "60d"
            try:
                raw_df = fetch_index_history(index_name, timeframe_min, period=period)
            except DataFeedError as e:
                print(f"[{index_name} {timeframe_min}m] Could not fetch historical data: {e}")
                print(f"[{index_name} {timeframe_min}m] Skipping -- see README for free-data-source limitations.")
                continue

            if len(raw_df) < 60:
                print(f"[{index_name} {timeframe_min}m] Not enough bars ({len(raw_df)}) for a "
                      f"meaningful backtest at this timeframe -- skipping.")
                continue

            for ema_fast in config.EMA_FAST_CANDIDATES:
                for ema_slow in config.EMA_SLOW_CANDIDATES:
                    if ema_fast >= ema_slow:
                        continue

                    df = prepare_dataframe(raw_df, ema_fast, ema_slow, config.ORB_RANGE_MINUTES_CANDIDATES[0])

                    for strat_builder, strat_name in strategy_registry:
                        if not config.ENABLED_STRATEGIES.get(strat_name):
                            continue

                        folds = rolling_walk_forward(df, index_name, strat_builder, timeframe_min)
                        fold_summary = summarize_folds(folds)

                        full_result = run_backtest(df, index_name, strat_builder(), timeframe_min)
                        metrics = compute_metrics(full_result.trades, full_result.equity_curve, config.STARTING_CAPITAL)

                        key = (f"{strat_name} (EMA{ema_fast}/{ema_slow})", index_name, timeframe_min)
                        all_results[key] = metrics

                        if fold_summary["overall_robust"]:
                            any_robust = True
                        print(f"[{index_name} {timeframe_min}m] {strat_name} EMA{ema_fast}/{ema_slow}: "
                              f"{metrics['num_trades']} trades, net Rs{metrics['net_pnl']}, "
                              f"walk-forward robust={fold_summary['overall_robust']} ({fold_summary['reason']})")

    print("\n" + "=" * 100)
    print("STRATEGY COMPARISON TABLE")
    print("=" * 100)
    comparison_table = build_comparison_table(all_results)
    print(comparison_table)

    print("\n" + "=" * 100)
    if any_robust:
        robust_keys = sorted(all_results.items(), key=lambda kv: (kv[1]["net_pnl"] or -1e18), reverse=True)[:3]
        selection_lines = ["SELECTED STRATEGIES (top robust candidates by net P&L, capped at 3):"]
        for key, m in robust_keys:
            line = (f"  - {key[0]} on {key[1]}: net Rs{m['net_pnl']}, PF={m['profit_factor']}, "
                    f"win rate={m['win_rate']}%")
            print(line)
            selection_lines.append(line)
        selection_text = "\n".join(selection_lines)
    else:
        selection_text = ("NO PROFITABLE STRATEGY FOUND UNDER CURRENT TEST CONDITIONS.\n"
                           "(All tested configurations either lost money after realistic costs/slippage,\n"
                           " or failed walk-forward out-of-sample validation. See table below.)")
        print(selection_text)
    print("=" * 100)

    telegram_summary = f"{selection_text}\n\n{comparison_table}"
    try:
        telegram.send_backtest_summary(telegram_summary)
    except telegram.TelegramError:
        logging.getLogger("main").warning("Could not send backtest summary to Telegram (still printed above/in logs).")


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

    _maybe_send_weekly_report()
    _maybe_send_monthly_report()


def _entry_date(trade: dict):
    import datetime as _dt
    return _dt.date.fromisoformat(str(trade.get("entry_time", ""))[:10])


def _send_period_report(period_label: str, start_date, end_date):
    """Aggregates all paper trades in [start_date, end_date] (inclusive) and
    sends a Telegram summary. Used for weekly and monthly reports."""
    all_trades = database.fetch_trades(mode="paper_live")
    period_trades = []
    for t in all_trades:
        try:
            ed = _entry_date(t)
        except ValueError:
            continue
        if start_date <= ed <= end_date:
            period_trades.append(t)

    if not period_trades:
        print(f"No trades found for {period_label} period ({start_date} to {end_date}); skipping report.")
        return

    running = config.STARTING_CAPITAL
    peak = running
    max_dd = 0.0
    strategy_pnls = {}
    for t in period_trades:
        running += t["net_pnl"]
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        strategy_pnls[t["strategy"]] = strategy_pnls.get(t["strategy"], 0.0) + t["net_pnl"]

    report_text = format_period_report(
        period_label,
        starting_capital=config.STARTING_CAPITAL,
        ending_capital=round(running, 2),
        trades=period_trades,
        max_drawdown=round(max_dd, 2),
        strategy_pnls=strategy_pnls,
    )
    print(report_text)
    try:
        telegram.send_daily_report(report_text)  # reuses the same sender, header text already says WEEKLY/MONTHLY
    except telegram.TelegramError:
        logging.getLogger("main").warning(f"Could not send {period_label} report to Telegram (logged locally instead).")


def _maybe_send_weekly_report():
    """Sends a weekly summary only when today is Friday (last trading day of
    the week), covering Monday through today."""
    import datetime as _dt
    today = _dt.date.today()
    if today.weekday() != 4:  # 0=Monday ... 4=Friday
        return
    week_start = today - _dt.timedelta(days=today.weekday())
    _send_period_report("WEEKLY", week_start, today)


def _maybe_send_monthly_report():
    """Sends a monthly summary only when today is the last trading day
    before the month changes (i.e. tomorrow is a new month)."""
    import datetime as _dt
    today = _dt.date.today()
    tomorrow = today + _dt.timedelta(days=1)
    if tomorrow.month == today.month:
        return  # not the last day of the month yet
    month_start = today.replace(day=1)
    _send_period_report("MONTHLY", month_start, today)


def run_paper_trading_mode(timeframe_arg: str = None, index_arg: str = None):
    """
    timeframe_arg:
        None / "default" -> uses config.DEFAULT_TIMEFRAME_MIN (single timeframe, as before)
        "all"             -> runs one independent engine per (index, timeframe) combination
        "<number>"        -> runs that single timeframe only, e.g. "15"

    index_arg:
        None / "all" -> runs every instrument in config.INSTRUMENTS
        "<NAME>"      -> runs only that one instrument, e.g. "NIFTY"

    NOTE on combining "all" timeframe with multiple indices: each (index, timeframe)
    pair is treated as its own independent bot instance, each individually respecting
    MAX_OPEN_POSITIONS=1 for ITSELF. This means running all 6 timeframes for all 3
    indices could have up to 18 positions open at once (one per combination) -- this
    is a deliberate trade-off to let you compare timeframes/indices live, not a
    violation of the "1 position" rule within any single strategy/timeframe instance.
    If you want a strict single global position, run one index and one timeframe at a time.
    """
    print_first_run_status()
    database.init_db()

    if timeframe_arg is None or timeframe_arg == "default":
        timeframes_to_run = [config.DEFAULT_TIMEFRAME_MIN]
    elif timeframe_arg == "all":
        timeframes_to_run = config.TIMEFRAMES_MIN
    else:
        timeframes_to_run = [int(timeframe_arg)]

    if index_arg is None or index_arg == "all":
        instruments_to_run = config.INSTRUMENTS
    else:
        instruments_to_run = [index_arg.upper()]

    if len(timeframes_to_run) > 1 or len(instruments_to_run) > 1:
        print(f"\n⚠️  Running {len(instruments_to_run)} instrument(s) x {len(timeframes_to_run)} "
              f"timeframe(s) = {len(instruments_to_run) * len(timeframes_to_run)} independent engines. "
              f"See the note in run_paper_trading_mode() docstring about position limits.\n")

    engines = []
    for index_name in instruments_to_run:
        for tf in timeframes_to_run:
            strategies = build_strategy_set()
            engines.append(PaperTradingEngine(index_name, strategies, tf))

    print(f"\nStarting live paper-trading loop for {len(engines)} engine(s) "
          f"({instruments_to_run} x {timeframes_to_run}). Press Ctrl+C to stop safely.\n")

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

            # If every engine has hit a fail-safe halt (data feed issue, error, etc.),
            # there is nothing productive left to do today. Exit now instead of idling
            # in 30-second polls until square-off -- this avoids burning through free
            # GitHub Actions minutes (and the platform's hard 6-hour job cap) for no
            # reason. A halted engine will simply try again fresh on the next scheduled run.
            all_halted = all(
                eng.halted or (eng.risk_state is not None and eng.risk_state.trading_halted)
                for eng in engines
            )
            if all_halted:
                print("\nAll engines are halted (fail-safe or risk limit) -- nothing further "
                      "can happen today. Exiting early instead of idling until square-off.")
                _send_end_of_day_report()
                break

            time.sleep(30)  # poll interval
    except KeyboardInterrupt:
        print("\nShutdown requested. Squaring off any open paper positions safely...")
        for eng in engines:
            eng.force_square_off_all("Manual shutdown")
        _send_end_of_day_report()
        print("All positions closed. Safe to exit.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NSE index options paper-trading system")
    parser.add_argument("mode", choices=["backtest", "paper", "status"], help="What to run")
    parser.add_argument(
        "--timeframe", default=None,
        help=(
            "Which timeframe to use: a number in minutes (1,3,5,15,30,60), "
            "or 'all' to run every configured timeframe together. "
            "If omitted, uses the default (5m for paper; config.BACKTEST_TIMEFRAMES_MIN for backtest)."
        ),
    )
    parser.add_argument(
        "--index", default=None,
        help=(
            "Which instrument to trade: NIFTY, BANKNIFTY, or FINNIFTY, "
            "or 'all' to run every configured instrument together. "
            "If omitted, runs all instruments (same as 'all')."
        ),
    )
    args = parser.parse_args()

    setup_logging()
    database.init_db()

    if args.mode == "backtest":
        run_backtest_mode(args.timeframe, args.index)
    elif args.mode == "paper":
        run_paper_trading_mode(args.timeframe, args.index)
    elif args.mode == "status":
        print_first_run_status()
