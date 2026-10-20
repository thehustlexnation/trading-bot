import asyncio
from datetime import timedelta
from unittest.mock import Mock

import pandas as pd
import pytest
from sqlalchemy import event

from realtime.acceptance import session_report
from realtime.config import RuntimeConfig
from realtime.journal import Journal
from realtime.readiness import preparation_phase, strategy_readiness
from realtime.service import acquire_session_lease, configuration_report
from realtime.telemetry import RuntimeTelemetry
from scripts import check_shadow_session


SESSION = "2026-10-19"
NOW = pd.Timestamp(f"{SESSION} 10:16", tz="America/New_York")
ORB = "ORB_RETEST_RECLAIM_BODY_2R"


def test_deployment_handover_waits_for_lease_without_stealing_it(monkeypatch):
    journal = Mock()
    journal.acquire.side_effect = [False, True]

    async def elapsed(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr("realtime.service.asyncio.wait_for", elapsed)
    assert asyncio.run(acquire_session_lease(journal, "new", asyncio.Event()))
    assert journal.acquire.call_count == 2
    journal.release.assert_not_called()


def test_handover_timeout_and_shutdown_never_take_ownership():
    journal = Mock()
    journal.acquire.return_value = False
    with pytest.raises(RuntimeError, match="handover timeout"):
        asyncio.run(acquire_session_lease(journal, "new", asyncio.Event(), timeout=0))
    stop = asyncio.Event()
    stop.set()
    assert not asyncio.run(acquire_session_lease(journal, "new", stop))


def test_latency_uses_bar_completion_and_separates_receive_from_queue_wait():
    telemetry = RuntimeTelemetry(sample_limit=2)
    stamp = NOW.floor("min")
    for index in range(3):
        telemetry.market_event("bar", {"symbol": "AAPL", "timestamp": stamp},
                               stamp + timedelta(seconds=61 + index),
                               stamp + timedelta(seconds=63 + index), index + 1)
    telemetry.observe("evaluation_seconds", 0.25)
    snapshot = telemetry.snapshot(NOW)
    lag = snapshot["latencies"]["bar_receive_seconds"]
    assert lag["count"] == 3 and lag["sample_count"] == 2
    assert lag["p50"] == 2.5 and lag["max"] == 3
    assert snapshot["latencies"]["queue_wait_seconds"]["max"] == 2
    assert snapshot["symbols"]["AAPL"]["bars"] == 3
    assert snapshot["queue_high_water"] == 3
    telemetry.reset()
    assert not telemetry.snapshot(NOW)["latencies"]
    assert snapshot["symbols"]["AAPL"]["bars"] == 3


def test_bar_revisions_have_separate_latency_samples():
    telemetry = RuntimeTelemetry()
    message = {"symbol": "AAPL", "timestamp": NOW}
    telemetry.market_event("bar", message, NOW + timedelta(seconds=61), NOW + timedelta(seconds=62), 1)
    telemetry.market_event("bar_revision", message, NOW + timedelta(seconds=91), NOW + timedelta(seconds=92), 1)
    snapshot = telemetry.snapshot(NOW + timedelta(seconds=92))
    assert snapshot["latencies"]["bar_receive_seconds"]["max"] == 1
    assert snapshot["latencies"]["bar_revision_receive_seconds"]["max"] == 31
    assert snapshot["symbols"]["AAPL"]["bars"] == 2


def test_ema_schedule_reports_actual_warmup_conflict():
    name = "VWAP_EMA9_CROSS_2R"
    row = strategy_readiness(RuntimeConfig())[name]
    assert row["status"] == "NO_ENTRY_WINDOW"
    assert row["earliest_readiness_ny"] == "10:00"
    assert strategy_readiness(RuntimeConfig(last_entry="10:01"))[name]["status"] == "ENTRY_WINDOW_AVAILABLE"
    assert not strategy_readiness(RuntimeConfig(strategies=(ORB,)))


def test_preparation_notifications_refresh_at_watch_and_opening_once_each(tmp_path):
    config = RuntimeConfig()
    journal = Journal(f"sqlite:///{tmp_path}/phase.sqlite3")
    for stamp, expected, added in [("07:45", "initial", True), ("08:00", "initial", False),
                                   ("09:25", "watch", True), ("09:30", "watch", False),
                                   ("09:35", "opening", True), ("09:40", "opening", False)]:
        phase = preparation_phase(pd.Timestamp(f"{SESSION} {stamp}", tz="America/New_York"), config)
        assert phase == expected
        assert journal.enqueue(SESSION, f"preparation:{phase}", stamp) is added
    assert len(journal.pending_notifications()) == 3


def complete_journal(tmp_path, monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID", raising=False)
    config = RuntimeConfig(intraday=("AAPL",), htf=("AAPL",), strategies=(ORB,))
    journal = Journal(f"sqlite:///{tmp_path}/journal.sqlite3")
    journal.event(SESSION, "SESSION_START", {**configuration_report(config), "code_sha": "reviewed-sha"})
    journal.event(SESSION, "STREAM_SUBSCRIPTION_CONFIRMED", {"symbols": config.subscriptions, "feed": config.feed})
    telemetry = RuntimeTelemetry()
    stamp = NOW.replace(hour=9, minute=30)
    for symbol in config.subscriptions:
        for index in range(45):
            minute = stamp + timedelta(minutes=index)
            journal.put_bar(SESSION, symbol, minute, {})
        telemetry.market_event("bar", {"symbol": symbol, "timestamp": stamp},
                               stamp + timedelta(seconds=61), stamp + timedelta(seconds=61.1), 1)
        telemetry.market_event("quote", {"symbol": symbol, "timestamp": stamp},
                               stamp + timedelta(seconds=1), stamp + timedelta(seconds=1.1), 1)
    telemetry.observe("evaluation_seconds", 0.2)
    journal.event(SESSION, "RUNTIME_TELEMETRY", telemetry.snapshot(stamp + timedelta(minutes=2)))
    journal.event(SESSION, "PREPARATION", {"symbol": "AAPL", "asof": stamp,
                                          "prior_close": 100, "pdh": 101, "pdl": 99})
    journal.event(SESSION, "DECISION", {"symbol": "AAPL", "strategy": ORB, "reason": "NO_RETEST"})
    journal.event(SESSION, "SESSION_RECAP", {"text": "completed", "code_sha": "reviewed-sha", "asof": NOW})
    journal.enqueue(SESSION, "recap", "completed")
    journal.mark_delivered(journal.pending_notifications()[0]["id"])
    return journal


def test_completed_report_is_read_only_and_requires_reviewed_identity(tmp_path, monkeypatch):
    journal = complete_journal(tmp_path, monkeypatch)
    statements = []
    event.listen(journal.engine, "before_cursor_execute", lambda conn, cursor, statement, *args: statements.append(statement))
    report = session_report(journal.engine, SESSION, NOW, "reviewed-sha")
    assert report["status"] == "PASS"
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert session_report(journal.engine, SESSION, NOW, "different-sha")["status"] == "FAIL"


def test_older_worker_cannot_pass_without_latency_or_lane_evidence(tmp_path):
    journal = Journal(f"sqlite:///{tmp_path}/old.sqlite3")
    journal.event(SESSION, "SESSION_START", {"code_sha": "old", "config_hash": "old"})
    report = session_report(journal.engine, SESSION, NOW)
    assert report["status"] == "INCOMPLETE"
    statuses = {row["name"]: row["status"] for row in report["gates"]}
    assert statuses["latency_evidence"] == statuses["lane_inventory"] == "UNVERIFIED"


def test_early_or_previous_release_recap_does_not_complete_new_session(tmp_path, monkeypatch):
    journal = complete_journal(tmp_path, monkeypatch)
    from sqlalchemy import delete

    with journal.engine.begin() as connection:
        connection.execute(delete(journal.events).where(journal.events.c.kind == "SESSION_RECAP"))
    journal.event(SESSION, "SESSION_RECAP", {"text": "early", "code_sha": "reviewed-sha",
                                           "asof": NOW.replace(hour=9, minute=45)})
    journal.event(SESSION, "SESSION_RECAP", {"text": "old", "code_sha": "previous-release", "asof": NOW})
    report = session_report(journal.engine, SESSION, NOW)
    assert report["status"] == "INCOMPLETE"
    assert next(row for row in report["gates"] if row["name"] == "completion")["status"] == "UNVERIFIED"


def test_report_command_does_not_create_missing_sqlite_database(tmp_path, monkeypatch, capsys):
    path = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(check_shadow_session, "database_url", lambda: f"sqlite:///{path}")
    monkeypatch.setattr("sys.argv", ["check_shadow_session", "--session", SESSION])
    assert check_shadow_session.main() == 2
    assert "UNAVAILABLE" in capsys.readouterr().out
    assert not path.exists()


def test_report_command_can_open_existing_sqlite_in_read_only_mode(tmp_path, monkeypatch, capsys):
    path = tmp_path / "existing.sqlite3"
    journal = Journal(f"sqlite:///{path}")
    journal.engine.dispose()
    monkeypatch.setattr(check_shadow_session, "database_url", lambda: f"sqlite:///{path}")
    monkeypatch.setattr("sys.argv", ["check_shadow_session", "--session", SESSION])
    assert check_shadow_session.main() == 2
    assert "INCOMPLETE" in capsys.readouterr().out


@pytest.mark.parametrize("kind, payload", [
    ("SESSION_FAILED", {"error_type": "RuntimeError"}),
    ("INVALID_MARKET_EVENT", {"symbol": "AAPL"}),
    ("DECISION", {"symbol": "AAPL", "strategy": ORB, "reason": "LANE_ERROR"}),
])
def test_failures_prevent_acceptance_even_with_recap(tmp_path, monkeypatch, kind, payload):
    journal = complete_journal(tmp_path, monkeypatch)
    journal.event(SESSION, kind, payload)
    assert session_report(journal.engine, SESSION, NOW)["status"] == "FAIL"
