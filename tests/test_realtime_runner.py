from datetime import datetime, time
from zoneinfo import ZoneInfo

from scripts.realtime_runner import next_runner_start, should_exit_before_wait


NY_TZ = ZoneInfo("America/New_York")


def test_next_runner_start_skips_weekend_to_monday():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=NY_TZ)

    run_at = next_runner_start(now, time(9, 25))

    assert run_at == datetime(2026, 10, 12, 9, 25, tzinfo=NY_TZ)


def test_next_runner_start_uses_same_day_before_start():
    now = datetime(2026, 10, 9, 8, 0, tzinfo=NY_TZ)

    run_at = next_runner_start(now, time(9, 25))

    assert run_at == datetime(2026, 10, 9, 9, 25, tzinfo=NY_TZ)


def test_should_exit_before_wait_when_session_is_too_far_away():
    assert should_exit_before_wait(wait_seconds=7200, max_wait_minutes=60) is True


def test_should_not_exit_before_wait_when_session_is_close():
    assert should_exit_before_wait(wait_seconds=1800, max_wait_minutes=60) is False


def test_should_not_exit_before_wait_without_limit():
    assert should_exit_before_wait(wait_seconds=7200, max_wait_minutes=None) is False
