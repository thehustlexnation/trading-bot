import argparse
import os
import subprocess
import sys
import time as sleep_time
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from strategy.market_calendar import is_trading_session


NY_TZ = ZoneInfo("America/New_York")
DEFAULT_SYMBOLS = "AAPL,NVDA,AMD,AVGO,SNDK,INTC,GOOGL,QQQ"
DEFAULT_HTF_SYMBOLS = (
    "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AMD,AVGO,"
    "JPM,V,MA,NFLX,COST,ORCL,CRM,ADBE,QQQ"
)
DEFAULT_STRATEGIES = (
    "ORB_RETEST_RECLAIM_BODY_2R,"
    "HTF_BREAKOUT_RETEST_2R,"
    "VWAP_EMA9_CROSS_2R"
)


def parse_ny_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def next_runner_start(now: datetime, start_time: time) -> datetime:
    candidate_date = now.date()
    while True:
        candidate = datetime.combine(candidate_date, start_time, tzinfo=NY_TZ)
        if candidate > now and is_trading_session(candidate_date):
            return candidate
        candidate_date += timedelta(days=1)


def run_command(command: list[str]) -> int:
    print(f"RUN: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=ROOT_DIR)
    return int(completed.returncode)


def run_news_checks(args) -> None:
    run_command(
        [
            sys.executable,
            "scripts/forexfactory_news_risk.py",
            "--countries",
            "USD",
            "--impacts",
            "High",
            "--start",
            args.news_start,
            "--end",
            args.news_end,
            "--slack",
        ]
    )

    watchlist = f"{args.symbols},{args.htf_symbols}"
    run_command(
        [
            sys.executable,
            "scripts/stock_catalyst_risk.py",
            "--symbols",
            watchlist,
            "--lookback-days",
            "1",
            "--lookahead-days",
            "7",
            "--slack",
        ]
    )


def run_trading_watch(args) -> int:
    command = [
        sys.executable,
        "scripts/paper_trade.py",
        "--symbols",
        args.symbols,
        "--htf-symbols",
        args.htf_symbols,
        "--strategies",
        args.strategies,
        "--source",
        "live",
        "--watch",
        "--slack",
        "--alert-status",
        "--poll-seconds",
        str(args.poll_seconds),
        "--cutoff",
        args.cutoff,
        "--last-entry",
        args.last_entry,
        "--macro-news-start",
        args.news_start,
        "--macro-news-end",
        args.news_end,
        "--equity",
        str(args.equity),
    ]
    if args.submit_paper:
        command.extend(["--submit", "--auto-submit-watch"])
    return run_command(command)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Long-running real-time paper alert runner for VPS deployment."
    )
    parser.add_argument("--symbols", default=os.getenv("REALTIME_SYMBOLS", DEFAULT_SYMBOLS))
    parser.add_argument("--htf-symbols", default=os.getenv("REALTIME_HTF_SYMBOLS", DEFAULT_HTF_SYMBOLS))
    parser.add_argument("--strategies", default=os.getenv("REALTIME_STRATEGIES", DEFAULT_STRATEGIES))
    parser.add_argument("--start", default=os.getenv("REALTIME_START", "09:25"))
    parser.add_argument("--cutoff", default=os.getenv("REALTIME_CUTOFF", "10:15"))
    parser.add_argument("--last-entry", default=os.getenv("REALTIME_LAST_ENTRY", "10:00"))
    parser.add_argument("--news-start", default=os.getenv("REALTIME_NEWS_START", "08:00"))
    parser.add_argument("--news-end", default=os.getenv("REALTIME_NEWS_END", "10:30"))
    parser.add_argument("--poll-seconds", type=int, default=int(os.getenv("REALTIME_POLL_SECONDS", "30")))
    parser.add_argument("--equity", type=float, default=float(os.getenv("REALTIME_EQUITY", "10000")))
    parser.add_argument("--submit-paper", action="store_true", default=env_bool("REALTIME_SUBMIT_PAPER", False))
    parser.add_argument("--once", action="store_true", help="Run one session and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_time = parse_ny_clock(args.start)
    print(
        "Realtime runner started "
        f"(start={args.start} NY, cutoff={args.cutoff}, last_entry={args.last_entry}, "
        f"submit_paper={args.submit_paper})",
        flush=True,
    )

    while True:
        now = datetime.now(tz=NY_TZ)
        run_at = next_runner_start(now, start_time)
        wait_seconds = max(0, int((run_at - now).total_seconds()))
        print(f"Next trading run: {run_at.isoformat()} ({wait_seconds}s)", flush=True)
        sleep_time.sleep(wait_seconds)

        run_date = datetime.now(tz=NY_TZ).date()
        print(f"Starting realtime session for {run_date}", flush=True)
        run_news_checks(args)
        exit_code = run_trading_watch(args)
        print(f"Trading watch finished with exit code {exit_code}", flush=True)

        if args.once:
            raise SystemExit(exit_code)

        sleep_time.sleep(60)


if __name__ == "__main__":
    main()
