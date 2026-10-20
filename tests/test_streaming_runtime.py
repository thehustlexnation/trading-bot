from dataclasses import replace
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from realtime.config import RuntimeConfig
from realtime.engine import ShadowEngine
from realtime.journal import Journal
from realtime.market import MarketState, Quote, with_entry_observation
from realtime.preparation import activity_summary, build_dossier, format_preparation, prepare_history
from realtime.service import configuration_report, database_url
from scripts.paper_trade import WAIT_REASONS, final_policy_rejection
from scripts.realtime_runner import next_runner_start
from paper_trading.vwap_ema_cross import build_three_minute_bars
from paper_trading.config import PaperTradingConfig


NOW = pd.Timestamp("2026-10-19 09:37:01", tz="America/New_York")
SESSION = "2026-10-19"
ORB = "ORB_RETEST_RECLAIM_BODY_2R"
LONG_ROWS = [(100, 101, 99, 100.5), (100.5, 101.2, 99.5, 100.7),
             (100.7, 101.3, 99.8, 100.9), (100.9, 101.4, 99.9, 101),
             (101, 101.5, 100, 101.1), (101.2, 102, 101.1, 101.8),
             (101.6, 101.8, 101.4, 101.7)]


def message(symbol, index, prices=(100, 100.1, 99.9, 100)):
    return {"symbol": symbol, "timestamp": NOW.normalize() + pd.Timedelta(hours=9, minutes=30 + index),
            **dict(zip(("open", "high", "low", "close"), prices)), "volume": 1000}


def quote(symbol="AAPL", now=NOW, bid=101.68, ask=101.70):
    return {"symbol": symbol, "timestamp": now, "bid_price": bid, "ask_price": ask}


def make_engine(tmp_path, symbol="AAPL", now=NOW):
    config = RuntimeConfig(intraday=(symbol,), htf=(symbol,), strategies=(ORB,))
    journal = Journal(f"sqlite:///{tmp_path}/state.sqlite3")
    assert journal.acquire("owner", now)
    engine = ShadowEngine(config, journal, SESSION, "owner")
    engine.set_macro({"status": "clear", "events": []}, now)
    for index, row in enumerate(LONG_ROWS):
        engine.ingest_bar(message(symbol, index, row), now)
    benchmark = "SPY" if symbol == "QQQ" else "QQQ"
    for index in range(7):
        engine.ingest_bar(message(benchmark, index), now)
    engine.ingest_quote(quote(symbol, now), now)
    return engine


def test_default_inventory_and_sanitized_configuration(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID", raising=False)
    config = RuntimeConfig()
    assert len(config.watchlist) == 20
    assert len(config.subscriptions) == 22
    assert len(config.lanes) == 34
    assert config.fingerprint == RuntimeConfig().fingerprint
    report = configuration_report(config)
    assert report["broker_submission"] == "DISABLED"
    assert "api_key" not in report and "secret" not in report


@pytest.mark.parametrize("changes", [{"feed": "delayed_sip"}, {"equity": float("nan")},
    {"last_entry": "07:00"}, {"risk_fraction": 0.02}, {"strategies": ("FAILED_BREAKDOWN_REVERSAL_2R",)}])
def test_configuration_rejects_unsafe_inputs(changes):
    with pytest.raises(ValueError):
        RuntimeConfig(**changes)


def test_legacy_start_remains_watch_start(monkeypatch):
    monkeypatch.setenv("REALTIME_START", "09:25")
    monkeypatch.delenv("REALTIME_PREPARATION_START", raising=False)
    monkeypatch.delenv("REALTIME_STRATEGIES", raising=False)
    config = RuntimeConfig.from_env()
    assert config.preparation_start == "07:45"
    assert config.watch_start == "09:25"


def test_railway_refuses_ephemeral_database(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "production")
    monkeypatch.delenv("REALTIME_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="PostgreSQL"):
        database_url()
    monkeypatch.setenv("REALTIME_DATABASE_URL", "postgresql://example.invalid/test")
    assert database_url().startswith("postgresql")


def test_submission_environment_is_rejected(monkeypatch):
    monkeypatch.setenv("REALTIME_SUBMIT_PAPER", "true")
    with pytest.raises(ValueError, match="PAPER_SUBMISSION_DISABLED"):
        RuntimeConfig.from_env()


def test_wait_states_do_not_retire_lanes():
    assert {"NO_BREAKOUT", "NO_RETEST", "NO_REVERSAL_CONFIRMATION", "NO_BREAKDOWN",
            "NO_FAILED_BREAKDOWN_CONFIRMATION", "POST_NEWS_RANGE_NOT_READY"} <= WAIT_REASONS


def test_scheduler_resumes_active_session_and_handles_dst():
    assert next_runner_start(NOW.to_pydatetime(), time(9, 25)) == NOW.to_pydatetime()
    friday = pd.Timestamp("2026-10-30 12:00", tz="America/New_York").to_pydatetime()
    monday = next_runner_start(friday, time(7, 45))
    assert monday.date().isoformat() == "2026-11-02"
    assert monday.utcoffset() == timedelta(hours=-5)


def test_lease_excludes_second_worker_and_can_recover(tmp_path):
    url = f"sqlite:///{tmp_path}/lease.sqlite3"
    first, second = Journal(url), Journal(url)
    assert first.acquire("first", NOW)
    assert not second.acquire("second", NOW)
    assert second.acquire("second", NOW + timedelta(seconds=61))
    assert not first.acquire("first", NOW + timedelta(seconds=62))


def test_bar_validation_and_closed_only_snapshots(tmp_path):
    journal = Journal(f"sqlite:///{tmp_path}/bars.sqlite3")
    state = MarketState(SESSION, "iex", journal)
    for index in range(8):
        state.bar(message("AAPL", index), NOW)
    assert len(state.frame("AAPL", NOW)) == 7
    assert len(journal.history(SESSION, "BAR_OBSERVATION")) == 8
    assert not state.bar(message("AAPL", 0), NOW)
    with pytest.raises(ValueError, match="Invalid OHLC"):
        state.bar({**message("AAPL", 0), "close": float("nan")}, NOW)
    with pytest.raises(ValueError, match="bounds"):
        state.bar({**message("AAPL", 0), "close": 200}, NOW)
    assert len(MarketState(SESSION, "iex", Journal(f"sqlite:///{tmp_path}/bars.sqlite3")).frame("AAPL", NOW)) == 7


def test_bar_revision_preserves_received_versions(tmp_path):
    engine = make_engine(tmp_path)
    original = len(engine.journal.history(SESSION, "BAR_OBSERVATION"))
    revised = message("AAPL", 6, (101.6, 101.9, 101.4, 101.8))
    assert engine.ingest_bar(revised, NOW + timedelta(seconds=1))
    assert len(engine.journal.history(SESSION, "BAR_OBSERVATION")) == original + 1
    assert engine.market.frame("AAPL", NOW).iloc[-1]["close"] == 101.8


def test_missing_minute_blocks_and_can_be_backfilled(tmp_path):
    engine = make_engine(tmp_path)
    state = MarketState(SESSION, "iex", Journal(f"sqlite:///{tmp_path}/gap.sqlite3"))
    for index in (0, 1, 2, 3, 4, 6):
        state.bar(message("AAPL", index), NOW)
    assert state.ready_frame("AAPL", NOW)[1] == "DATA_GAP_WAITING_FOR_BACKFILL"
    state.bar(message("AAPL", 5), NOW)
    assert state.ready_frame("AAPL", NOW)[1] is None


def test_quote_freshness_spread_and_out_of_order_handling(tmp_path):
    engine = make_engine(tmp_path)
    stale = Quote.from_message(quote(now=NOW - timedelta(seconds=20)), NOW)
    assert stale.rejection(NOW, 10, 20) == "STALE_QUOTE"
    wide = Quote.from_message(quote(bid=100, ask=101), NOW)
    assert wide.rejection(NOW, 10, 20) == "SPREAD_LIMIT"
    assert engine.market.quote(quote(now=NOW - timedelta(seconds=1)), NOW) is None


def test_three_minute_source_count_and_entry_observation(tmp_path):
    engine = make_engine(tmp_path)
    frame = engine.market.frame("AAPL", NOW)
    bars = build_three_minute_bars(frame)
    assert len(bars) == 2
    assert bars.iloc[-1]["timestamp_ny"].strftime("%H:%M") == "09:33"
    frame = frame.iloc[:6]
    at_boundary = NOW.replace(minute=36)
    entry = Quote.from_message(quote(now=at_boundary), at_boundary)
    bars = build_three_minute_bars(with_entry_observation(frame, entry, at_boundary))
    assert len(bars) == 3 and bars.iloc[-1]["volume"] == 0
    with pytest.raises(ValueError, match="Duplicate"):
        build_three_minute_bars(pd.concat([frame, frame.iloc[:1]]))


@pytest.mark.parametrize("symbol", RuntimeConfig().watchlist)
def test_shadow_candidate_for_each_configured_instrument(tmp_path, symbol):
    engine = make_engine(tmp_path, symbol)
    assert engine.evaluate_lane(symbol, ORB, NOW) == "APPROVED_SHADOW"
    positions = engine.journal.shadow_positions(SESSION)
    assert len(positions) == 1
    assert positions[0]["payload"]["reference_type"].startswith("hypothetical")
    assert engine.evaluate_lane(symbol, ORB, NOW) == "SYMBOL_ALREADY_EVALUATED"


def test_restart_preserves_claim_and_shadow_position(tmp_path):
    engine = make_engine(tmp_path)
    assert engine.evaluate_lane("AAPL", ORB, NOW) == "APPROVED_SHADOW"
    restored = ShadowEngine(engine.config, Journal(f"sqlite:///{tmp_path}/state.sqlite3"), SESSION, "owner")
    assert "AAPL" in restored.positions
    assert restored.positions["AAPL"]["path_gap"]
    assert "WORKER_RESTART" in restored.positions["AAPL"]["unverified_reasons"]
    assert restored.evaluate_lane("AAPL", ORB, NOW) == "SYMBOL_ALREADY_EVALUATED"


def test_quote_exit_and_replay_are_idempotent(tmp_path):
    engine = make_engine(tmp_path)
    engine.evaluate_lane("AAPL", ORB, NOW)
    target = engine.positions["AAPL"]["target"]
    later = NOW + timedelta(seconds=2)
    engine.ingest_quote(quote(now=later, bid=target, ask=target + 0.02), later)
    assert "AAPL" not in engine.positions
    row = engine.journal.shadow_positions(SESSION)[0]
    assert row["state"] == "CLOSED"
    assert row["payload"]["r_multiple"] == pytest.approx(2)
    assert engine.close("AAPL", target, "TARGET", later) is None


def test_wide_exit_quote_is_unverified_and_preserves_observed_loss(tmp_path):
    from realtime.journal import exit_message
    from realtime.diagnostics import trade_diagnostics

    engine = make_engine(tmp_path)
    assert engine.evaluate_lane("AAPL", ORB, NOW) == "APPROVED_SHADOW"
    position = engine.positions["AAPL"]
    risk_per_share = position["entry"] - position["stop"]
    later = NOW + timedelta(seconds=2)
    engine.ingest_quote(quote(now=later, bid=position["entry"] - 4.2 * risk_per_share,
                             ask=position["entry"] + 0.02), later)
    payload = engine.journal.shadow_positions(SESSION)[0]["payload"]
    assert payload["r_multiple"] == pytest.approx(-4.2)
    assert payload["path_gap"]
    assert "EXIT_SPREAD_LIMIT" in payload["unverified_reasons"]
    assert payload["unverified_reasons"]["EXIT_SPREAD_LIMIT"]["quote"]["bid"] == payload["exit"]
    assert "UNVERIFIED SHADOW OBSERVATION" in exit_message(payload)
    assert "planned stop" in exit_message(payload)
    assert "closed P/L before fees: $+0.00" in engine.recap()
    audit = trade_diagnostics(payload)
    assert not audit["verified_path"] and audit["r_calculated_from_record"] == pytest.approx(-4.2)
    assert audit["exit_spread_bps"] > engine.config.max_spread_bps


def test_quote_gap_retains_the_last_observations_and_elapsed_time(tmp_path):
    engine = make_engine(tmp_path)
    assert engine.evaluate_lane("AAPL", ORB, NOW) == "APPROVED_SHADOW"
    later = NOW + timedelta(seconds=20)
    engine.ingest_quote(quote(now=later), later)
    payload = engine.journal.shadow_positions(SESSION)[0]["payload"]
    detail = payload["unverified_reasons"]["QUOTE_OBSERVATION_GAP"]
    assert detail["gap_seconds"] == 20
    assert pd.Timestamp(detail["previous_quote"]["timestamp"]) == NOW
    assert pd.Timestamp(detail["next_quote"]["timestamp"]) == later


def test_tight_fresh_stop_quote_remains_a_verified_shadow_mark(tmp_path):
    from realtime.journal import exit_message

    engine = make_engine(tmp_path)
    assert engine.evaluate_lane("AAPL", ORB, NOW) == "APPROVED_SHADOW"
    stop = engine.positions["AAPL"]["stop"]
    later = NOW + timedelta(seconds=2)
    engine.ingest_quote(quote(now=later, bid=stop, ask=stop + 0.02), later)
    payload = engine.journal.shadow_positions(SESSION)[0]["payload"]
    assert payload["r_multiple"] == pytest.approx(-1)
    assert not payload.get("path_gap")
    assert exit_message(payload).startswith("SHADOW RESULT")


def test_cutoff_does_not_falsely_close_without_current_quote(tmp_path):
    engine = make_engine(tmp_path)
    engine.evaluate_lane("AAPL", ORB, NOW)
    cutoff = NOW.replace(hour=10, minute=15)
    engine.tick(cutoff)
    assert engine.journal.shadow_positions(SESSION)[0]["state"] == "OPEN"
    engine.ingest_quote(quote(now=cutoff, bid=102, ask=102.02), cutoff)
    assert engine.journal.shadow_positions(SESSION)[0]["payload"]["outcome"] == "CUTOFF"


def test_no_entries_at_actual_deadline_even_for_earlier_signal(tmp_path):
    engine = make_engine(tmp_path)
    assert engine.evaluate_lane("AAPL", ORB, NOW.replace(hour=10, minute=0)) == "AFTER_LAST_ENTRY"
    assert not engine.journal.shadow_positions(SESSION)


def test_unknown_macro_and_stale_quote_cannot_approve(tmp_path):
    engine = make_engine(tmp_path)
    engine.macro = {"status": "unavailable"}
    assert engine.evaluate_lane("AAPL", ORB, NOW) == "MACRO_CONTEXT_UNAVAILABLE"
    engine.macro = {"status": "clear"}
    assert engine.evaluate_lane("AAPL", ORB, NOW + timedelta(seconds=20)) == "STALE_QUOTE"


def test_portfolio_reserves_combined_risk_not_per_order_only(tmp_path):
    journal = Journal(f"sqlite:///{tmp_path}/risk.sqlite3")
    config = RuntimeConfig()
    journal.acquire("owner", NOW)
    payload = {"side": "long", "entry": 100, "stop": 99, "target": 102,
               "quantity": 25, "risk": 25, "notional": 2500}
    results = [journal.open_shadow(SESSION, f"X{index}", f"group{index}", payload, config, "owner", NOW)
               for index in range(8)]
    assert results.count("APPROVED") == 4
    assert results[4:] == ["PORTFOLIO_RISK_LIMIT"] * 4


def test_daily_losses_and_lost_leadership_block_reservations(tmp_path):
    journal = Journal(f"sqlite:///{tmp_path}/risk.sqlite3")
    config = RuntimeConfig()
    payload = {"side": "long", "entry": 100, "entry_time": NOW.isoformat(), "stop": 99,
               "target": 102, "quantity": 25, "risk": 25, "notional": 2500}
    assert journal.open_shadow(SESSION, "AAPL", "g", payload, config, "owner", NOW) == "LEADERSHIP_LOST"
    journal.acquire("owner", NOW)
    assert journal.open_shadow(SESSION, "AAPL", "g", payload, config, "owner", NOW) == "APPROVED"
    journal.close_shadow(SESSION, "AAPL", 96, "STOP_GAP", NOW)
    assert journal.open_shadow(SESSION, "NVDA", "g", payload, config, "owner", NOW) == "PORTFOLIO_RISK_LIMIT"


def test_outbox_is_durable_and_deduplicated(tmp_path):
    url = f"sqlite:///{tmp_path}/outbox.sqlite3"
    journal = Journal(url)
    assert journal.enqueue(SESSION, "start", "hello")
    assert not journal.enqueue(SESSION, "start", "hello")
    journal = Journal(url)
    pending = journal.pending_notifications()
    assert len(pending) == 1
    journal.mark_delivered(pending[0]["id"])
    assert not journal.pending_notifications()


def test_preparation_covers_every_instrument_with_unknowns():
    empty = prepare_history(pd.DataFrame())
    config = RuntimeConfig()
    dossiers = [build_dossier(symbol, empty, empty, NOW) for symbol in config.watchlist]
    assert len(dossiers) == 20
    assert all(row["catalysts"]["status"] == "UNKNOWN" for row in dossiers)
    output = format_preparation(dossiers, "iex")
    assert all(symbol in output for symbol in config.watchlist)
    assert "+48 more" not in output


def test_opening_rvol_requires_completed_window_and_prior_history():
    rows = []
    for i in range(15):
        day = NOW.normalize() - pd.Timedelta(days=14 - i)
        for minute in range(5):
            stamp = day + pd.Timedelta(hours=9, minutes=30 + minute)
            rows.append({"timestamp": stamp.tz_convert("UTC"), "timestamp_ny": stamp,
                         "volume": 200 if i == 14 else 100, "ticker": "AAPL"})
    frame = pd.DataFrame(rows)
    assert activity_summary(frame, "AAPL", NOW)["opening_rvol"] == 2
    assert activity_summary(frame, "AAPL", NOW.replace(minute=33))["opening_rvol"] is None


def test_legacy_final_gate_runs_before_any_adapter_call(monkeypatch):
    from scripts import paper_trade as watcher
    from paper_trading.order_plan import build_bracket_order_plan
    from strategy.market_calendar import SessionQuality

    plan = build_bracket_order_plan("AAPL", ORB, "LONG", 100, 99, 102, 10000, 0.0025)
    signal = {"entry_timestamp": NOW, "relative_strength": {"alignment": "against"}}
    decision = SimpleNamespace(approved=True, reason="APPROVED", plan=plan, signal=signal)
    frame = pd.DataFrame({"timestamp_ny": [NOW]})
    monkeypatch.setattr(watcher, "load_db_session", lambda *_: frame)
    monkeypatch.setattr(watcher, "filter_expected_session_minutes", lambda *_: (frame,
        SimpleNamespace(quality=SessionQuality.COMPLETE, opening_range_complete=True)))
    monkeypatch.setattr(watcher, "build_retest_body_paper_trade_decision", lambda **_: decision)
    monkeypatch.setattr(watcher, "enrich_decision_with_market_context", lambda **_: None)
    monkeypatch.setattr(watcher, "append_audit_record", lambda *_: None)
    submit, client = Mock(), Mock()
    monkeypatch.setattr(watcher, "submit_bracket_order", submit)
    monkeypatch.setattr(watcher, "create_alpaca_paper_trading_client", client)
    args = SimpleNamespace(submit=True, equity=10000, source="db", symbol="AAPL", realized_daily_pnl=0,
        last_entry="10:00", macro_risk_context={"status": "clear"})
    reason, _ = watcher.run_once(args, NOW.date(), PaperTradingConfig(), True, ORB)
    assert reason == "LOW_QUALITY_C"
    submit.assert_not_called()
    client.assert_not_called()


def test_legacy_cutoff_still_checks_last_completed_bar_target():
    from scripts.paper_trade import WatchedTrade, format_watched_trade_cutoff_result
    from paper_trading.order_plan import build_bracket_order_plan

    plan = build_bracket_order_plan("AAPL", "TEST", "LONG", 100, 99, 102, 10000, 0.0025)
    stamp = NOW.replace(hour=10, minute=14, second=0)
    frame = pd.DataFrame([{"timestamp_ny": stamp, "open": 100, "high": 103, "low": 99.5, "close": 100.5, "volume": 1000}])
    trade = WatchedTrade("AAPL", "TEST", plan, stamp)
    _, result = format_watched_trade_cutoff_result(trade, frame, "10:15")
    assert result.r_multiple == 2


def test_reversal_missing_next_bar_is_a_wait_not_an_exception():
    from paper_trading.failed_breakdown_reversal import find_failed_breakdown_reversal_signal

    rows = [(100, 100.5, 99.5, 100)] * 5 + [(100, 100.1, 98.8, 99), (99, 101.5, 98.9, 101)]
    frame = pd.DataFrame([{**message("AAPL", i, row), "timestamp_ny": message("AAPL", i, row)["timestamp"]}
                          for i, row in enumerate(rows)])
    _, reason = find_failed_breakdown_reversal_signal(frame)
    assert reason == "NO_ENTRY_CANDLE"


def test_short_shadow_entry_and_target_are_direction_correct(tmp_path):
    engine = make_engine(tmp_path)
    rows = [(100, 101, 99, 99.8), (99.8, 100.5, 98.8, 99.2),
            (99.2, 100, 98.7, 99), (99, 100.2, 98.6, 98.9),
            (98.9, 100, 98.5, 98.8), (98.4, 98.6, 97.8, 98), (98.2, 98.7, 98, 98.1)]
    for index, row in enumerate(rows):
        engine.ingest_bar(message("AAPL", index, row), NOW)
    engine.ingest_quote(quote(now=NOW + timedelta(milliseconds=1), bid=98.08, ask=98.10), NOW)
    assert engine.evaluate_lane("AAPL", ORB, NOW) == "APPROVED_SHADOW"
    position = engine.positions["AAPL"]
    assert position["side"] == "short" and position["target"] < position["entry"] < position["stop"]
    later = NOW + timedelta(seconds=2)
    engine.ingest_quote(quote(now=later, bid=position["target"] - 0.02, ask=position["target"]), later)
    assert engine.journal.shadow_positions(SESSION)[0]["payload"]["r_multiple"] == pytest.approx(2)


def test_slow_evaluation_cannot_cross_entry_deadline(tmp_path):
    engine = make_engine(tmp_path)
    engine.now_fn = lambda: NOW.replace(hour=10, minute=0)
    assert engine.evaluate_lane("AAPL", ORB, NOW) == "AFTER_LAST_ENTRY"
    assert not engine.journal.shadow_positions(SESSION)


def test_shadow_position_and_notification_commit_together(tmp_path):
    engine = make_engine(tmp_path)
    engine.evaluate_lane("AAPL", ORB, NOW)
    restarted = Journal(f"sqlite:///{tmp_path}/state.sqlite3")
    assert len(restarted.shadow_positions(SESSION)) == 1
    assert len(restarted.pending_notifications()) == 1


def test_macro_events_keep_structured_timestamps_in_journal(tmp_path):
    from scripts.forexfactory_news_risk import EconomicEvent

    engine = make_engine(tmp_path)
    event = EconomicEvent("CPI", "USD", NOW, "High")
    engine.set_macro({"status": "high", "events": [event]}, NOW)
    row = engine.journal.history(SESSION, "MACRO_CONTEXT")[-1]
    assert row["context"]["events"][0]["title"] == "CPI"
    assert "2026-10-19" in row["context"]["events"][0]["event_time"]


def test_sdk_rest_transport_has_connect_and_read_deadlines(monkeypatch):
    from requests.adapters import HTTPAdapter
    from realtime.network import BoundedHTTPAdapter

    send = Mock(return_value="response")
    monkeypatch.setattr(HTTPAdapter, "send", send)
    assert BoundedHTTPAdapter().send(Mock(), timeout=None) == "response"
    assert send.call_args.kwargs["timeout"] == (5, 20)
