import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from strategy.market_calendar import is_trading_session

NY_TZ = ZoneInfo("America/New_York")


def next_runner_start(now: datetime, start_time: time, cutoff_time: time = time(10, 15)) -> datetime:
    if now.tzinfo is None:
        raise ValueError("Scheduler timestamps must be timezone-aware.")
    now = now.astimezone(NY_TZ)
    candidate_date = now.date()
    while True:
        candidate = datetime.combine(candidate_date, start_time, tzinfo=NY_TZ)
        cutoff = datetime.combine(candidate_date, cutoff_time, tzinfo=NY_TZ)
        if is_trading_session(candidate_date):
            if candidate > now:
                return candidate
            if candidate <= now < cutoff:
                return now
        candidate_date += timedelta(days=1)


def should_exit_before_wait(wait_seconds: int, max_wait_minutes: int | None) -> bool:
    return max_wait_minutes is not None and wait_seconds > max_wait_minutes * 60


def main():
    from realtime.service import main as service_main
    service_main()


if __name__ == "__main__":
    main()
