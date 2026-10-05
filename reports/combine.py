"""
reports/combine.py
====================
Merges per-INDEX backtest "shards" (each produced by a separate GitHub
Actions matrix job -- see .github/workflows/backtest.yml -- running
`python main.py backtest --index NIFTY` / `--index BANKNIFTY` / `--index
FINNIFTY` in parallel, each in its own isolated checkout) into the final,
single set of combined report files:

    reports/latest_backtest_summary.csv / .xlsx   (every combo, all 3 indices)
    reports/backtest_best_trades.csv               (best combo per index)
    reports/latest_backtest_dashboard.xlsx          (grid+stats per index)
    reports/dashboard.xlsx                          (+ live sheets, if any)

WHY THIS EXISTS: splitting the backtest into 3 parallel matrix jobs is what
makes a full `--timeframe all` sweep across all indices finish inside
GitHub Actions' free-tier 360-minute-per-job cap (a single job doing all 3
indices was observed taking 5h40m+ without finishing). Each shard uploads
its reports/ files as a build artifact (not a git commit -- 3 matrix jobs
committing to the same repo in parallel would hit the exact same race
condition dashboard.xlsx generation already hit once). This module is the
one, single place that reads all 3 artifacts back and does the one git
commit, after all shards are done (see the "combine" job in backtest.yml).
"""

import csv
import os
import shutil

from reports.csv_log import write_backtest_summary_csv, write_backtest_summary_xlsx
from reports.dashboard import generate_backtest_dashboard, generate_dashboard


def _read_csv_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def combine_shards(shard_dirs: list, out_reports_dir: str):
    """
    shard_dirs: list of directory paths, each one matrix shard's downloaded
    `reports/` artifact contents (i.e. each dir directly contains
    latest_backtest_summary.csv, backtest_best_trades.csv, etc. for ONE
    index). out_reports_dir: where to write the final combined files
    (normally the real reports/ dir in the checked-out repo).
    """
    os.makedirs(out_reports_dir, exist_ok=True)

    # 1) Merge latest_backtest_summary.csv (every combo, every shard)
    all_rows = []
    for d in shard_dirs:
        all_rows.extend(_read_csv_rows(os.path.join(d, "latest_backtest_summary.csv")))
    for r in all_rows:
        try:
            r["Trades"] = int(r.get("Trades", 0))
        except (TypeError, ValueError):
            r["Trades"] = 0
        try:
            r["Net P&L"] = float(r.get("Net P&L", 0))
        except (TypeError, ValueError):
            r["Net P&L"] = 0.0
        try:
            r["Win Rate %"] = float(r.get("Win Rate %", 0))
        except (TypeError, ValueError):
            r["Win Rate %"] = 0.0
    write_backtest_summary_csv(all_rows, path=os.path.join(out_reports_dir, "latest_backtest_summary.csv"))
    write_backtest_summary_xlsx(all_rows, path=os.path.join(out_reports_dir, "latest_backtest_summary.xlsx"))
    print(f"Merged {len(all_rows)} rows from {len(shard_dirs)} shard(s) into latest_backtest_summary.csv/.xlsx")

    # 2) Merge backtest_best_trades.csv (one index's worth of rows per
    #    shard, since each shard only ever covers its own index -- a
    #    straight concatenation, no re-deriving "best" needed).
    best_cols = ["Date", "Index", "Strategy", "Net P&L", "Costs", "Capital Used"]
    merged_path = os.path.join(out_reports_dir, "backtest_best_trades.csv")
    with open(merged_path, "w", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=best_cols)
        writer.writeheader()
        for d in shard_dirs:
            for row in _read_csv_rows(os.path.join(d, "backtest_best_trades.csv")):
                writer.writerow({c: row.get(c, "") for c in best_cols})
    print(f"Merged backtest_best_trades.csv written to {merged_path}")

    # 3) Rebuild the backtest dashboard (Monthly/Weekly/Daily grid + stats,
    #    best-per-index) and the combined dashboard.xlsx (+ live sheets)
    #    from the just-merged backtest_best_trades.csv. NOTE: generate_
    #    dashboard() reads reports/backtest_best_trades.csv and reports/
    #    trade_log.csv from their real, fixed locations (reports/csv_log.py
    #    module constants) -- this all lines up correctly as long as
    #    out_reports_dir IS the repo's actual reports/ directory (true for
    #    the "combine" job in backtest.yml, which checks out the real repo).
    from reports.csv_log import read_backtest_best_trades_csv
    trades_by_index, labels_by_index = read_backtest_best_trades_csv(merged_path)
    generate_backtest_dashboard(trades_by_index, labels_by_index,
                                 out_path=os.path.join(out_reports_dir, "latest_backtest_dashboard.xlsx"))
    print("latest_backtest_dashboard.xlsx written")

    generate_dashboard(
        trade_log_path=os.path.join(out_reports_dir, "trade_log.csv"),
        out_path=os.path.join(out_reports_dir, "dashboard.xlsx"),
    )
    print("dashboard.xlsx (combined) written")


if __name__ == "__main__":
    import sys
    # Usage: python -m reports.combine <shard_dir_1> <shard_dir_2> ... --out <reports_dir>
    args = sys.argv[1:]
    if "--out" not in args:
        print("Usage: python -m reports.combine <shard_dir_1> [<shard_dir_2> ...] --out <reports_dir>")
        sys.exit(1)
    out_idx = args.index("--out")
    shard_dirs = args[:out_idx]
    out_dir = args[out_idx + 1]
    combine_shards(shard_dirs, out_dir)
