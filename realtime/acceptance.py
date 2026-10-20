from collections import Counter
from datetime import datetime
import json

import pandas as pd
from sqlalchemy import text

from realtime.diagnostics import trade_diagnostics
from realtime.market import NY, aware_timestamp
from strategy.market_calendar import get_expected_regular_session_minutes


def session_report(database, session, now, expected_sha=None):
    """Read existing journal tables only; never initialize storage or acquire a lease."""
    datetime.strptime(session, "%Y-%m-%d")
    now = aware_timestamp(now)
    kinds = ("SESSION_START", "STREAM_SUBSCRIPTION_CONFIRMED", "PREPARATION", "DECISION",
             "RUNTIME_TELEMETRY", "SESSION_RECAP", "SESSION_FAILED", "BACKFILL_ERROR",
             "PREPARATION_SOURCE_ERROR", "UNRESOLVED_SHADOW_EXPOSURE", "INVALID_MARKET_EVENT")
    with database.connect() as connection:
        events = list(connection.execute(text(
            "SELECT kind, payload FROM realtime_events WHERE session = :session "
            "ORDER BY created_at, id"), {"session": session}).mappings())
        bars = list(connection.execute(text(
            "SELECT symbol, timestamp FROM realtime_bars WHERE session = :session"),
            {"session": session}).mappings())
        positions = list(connection.execute(text(
            "SELECT symbol, state, pnl, payload FROM realtime_shadow_positions WHERE session = :session"),
            {"session": session}).mappings())
        notifications = list(connection.execute(text(
            "SELECT state FROM realtime_outbox WHERE session = :session"), {"session": session}).scalars())
    # SQLAlchemy text queries return JSON text on SQLite, decoded JSON on PostgreSQL.
    grouped = {kind: [] for kind in kinds}
    for event in events:
        if event["kind"] in grouped:
            payload = event["payload"]
            grouped[event["kind"]].append(json.loads(payload) if isinstance(payload, str) else payload)
    starts = grouped["SESSION_START"]
    config = starts[-1] if starts else {}
    symbols = config.get("watchlist", [])
    subscriptions = config.get("subscriptions", [])
    lanes = config.get("lanes", [])
    gates = []

    def gate(name, status, detail):
        gates.append({"name": name, "status": status, "detail": detail})

    gate("session_record", "PASS" if starts else "UNVERIFIED", "Persisted session identity and configuration.")
    identities = {(row.get("code_sha"), row.get("config_hash")) for row in starts}
    identity_ok = bool(starts) and len(identities) == 1 and config.get("code_sha") not in {None, "", "unknown", "local"}
    if expected_sha is not None:
        identity_ok = identity_ok and config.get("code_sha") == expected_sha
    gate("release_identity", "PASS" if identity_ok else "FAIL" if starts else "UNVERIFIED",
         "Expected SHA and one consistent code/config identity per session." if expected_sha else
         "One recorded code/config identity; no expected SHA was supplied for comparison.")
    gate("submission_disabled", "PASS" if config.get("broker_submission") == "DISABLED" else "UNVERIFIED",
         "Recorded mode must explicitly disable broker submission.")
    confirmed = grouped["STREAM_SUBSCRIPTION_CONFIRMED"]
    subscription_ok = bool(subscriptions) and bool(confirmed) and all(
        set(subscriptions) == set(row.get("symbols", [])) and row.get("feed") == config.get("feed")
        for row in confirmed)
    gate("subscriptions", "PASS" if subscription_ok else "UNVERIFIED", "Actual stream acknowledgement matches configured symbols/feed.")
    readiness = config.get("strategy_readiness")
    conflicts = [name for name, row in (readiness or {}).items() if row["status"] == "NO_ENTRY_WINDOW"]
    gate("strategy_schedule", "FAIL" if conflicts else "PASS" if readiness is not None else "UNVERIFIED",
         ", ".join(conflicts) if conflicts else "Schedule readiness must be recorded by the worker.")
    expected = get_expected_regular_session_minutes(session)
    expected = expected[expected + pd.Timedelta(minutes=1) <= now]
    cutoff = pd.Timestamp(f"{session} {config.get('cutoff_ny', '10:15')}", tz=NY)
    expected = expected[expected < cutoff]
    seen = {}
    for row in bars:
        seen.setdefault(row["symbol"], set()).add(aware_timestamp(row["timestamp"]))
    missing = {symbol: len(set(expected) - seen.get(symbol, set())) for symbol in subscriptions}
    gap_symbols = {symbol: count for symbol, count in missing.items() if count}
    gate("completed_minutes", "UNVERIFIED" if not len(expected) or not subscriptions else "FAIL" if gap_symbols else "PASS",
         {"expected_minutes_per_symbol": len(expected), "missing_minutes": gap_symbols})
    telemetry = grouped["RUNTIME_TELEMETRY"]
    active_telemetry = [row for row in telemetry if
        pd.Timestamp(f"{session} 09:30", tz=NY) < aware_timestamp(row["asof"]) <= min(now, cutoff + pd.Timedelta(minutes=1))]
    quote_counts = Counter()
    for row in active_telemetry:
        for symbol, item in row.get("symbols", {}).items():
            quote_counts[symbol] += item.get("quotes", 0)
    missing_quotes = [symbol for symbol in subscriptions if not quote_counts[symbol]]
    gate("quote_coverage", "PASS" if subscriptions and not missing_quotes else "UNVERIFIED",
         {"missing_symbols": missing_quotes, "scope": "telemetry windows during the regular-session watch"})
    required_metrics = {"bar_receive_seconds", "quote_receive_seconds", "queue_wait_seconds", "evaluation_seconds"}
    measured = {name for row in active_telemetry for name in row.get("latencies", {})}
    gate("latency_evidence", "PASS" if required_metrics <= measured else "UNVERIFIED",
         {"missing_metrics": sorted(required_metrics - measured),
          "scope": "recent window percentiles; maxima/counts exact within each window, not whole-session percentiles"})
    decisions = grouped["DECISION"]
    evaluated = {(row["symbol"], row["strategy"]) for row in decisions}
    missing_lanes = [lane for lane in lanes if tuple(lane) not in evaluated]
    gate("lane_inventory", "PASS" if lanes and not missing_lanes else "UNVERIFIED",
         {"configured": config.get("lane_count"), "recorded": len(evaluated), "missing": missing_lanes})
    failures = {name: len(grouped[name]) for name in
                ("SESSION_FAILED", "UNRESOLVED_SHADOW_EXPOSURE", "INVALID_MARKET_EVENT") if grouped[name]}
    if any(row["reason"] == "LANE_ERROR" for row in decisions):
        failures["LANE_ERROR"] = sum(row["reason"] == "LANE_ERROR" for row in decisions)
    gate("session_failures", "FAIL" if failures else "PASS", failures)
    open_symbols = [row["symbol"] for row in positions if row["state"] == "OPEN"]
    trade_audits = [trade_diagnostics(json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"])
                    for row in positions]
    unverified_symbols = [row["symbol"] for row in trade_audits if not row["verified_path"]]
    gate("verified_trade_paths", "UNVERIFIED" if unverified_symbols else "PASS",
         {"unverified_symbols": unverified_symbols, "scope": "Observed marks are not broker fills."})
    completed = any(row.get("code_sha") == config.get("code_sha") and row.get("asof")
                    and cutoff <= aware_timestamp(row["asof"]) <= now for row in grouped["SESSION_RECAP"])
    gate("completion", "FAIL" if completed and open_symbols else "PASS" if completed else "UNVERIFIED",
         {"recap_recorded": completed, "open_shadow_positions": open_symbols})
    pending = sum(state != "DELIVERED" for state in notifications)
    gate("notifications", "PASS" if notifications and not pending else "UNVERIFIED",
         {"total": len(notifications), "pending": pending})
    latest_preparation = {row["symbol"]: row for row in grouped["PREPARATION"]}
    gate("preparation_inventory", "PASS" if symbols and set(symbols) <= set(latest_preparation) else "UNVERIFIED",
         {"configured": len(symbols), "recorded": len(latest_preparation)})
    preparation = {symbol: {"asof": row["asof"], "catalyst_status": row.get("catalysts", {}).get("status"),
        "catalyst_reason": row.get("catalysts", {}).get("reason"),
        "missing_price_fields": [name for name in ("prior_close", "pdh", "pdl", "premarket_high", "premarket_low")
                                 if row.get(name) is None]} for symbol, row in latest_preparation.items()}
    missing_levels = {symbol: [name for name in ("prior_close", "pdh", "pdl")
                              if latest_preparation.get(symbol, {}).get(name) is None] for symbol in symbols}
    missing_levels = {symbol: names for symbol, names in missing_levels.items() if names}
    gate("preparation_levels", "PASS" if symbols and not missing_levels else "UNVERIFIED", missing_levels)
    return {"session": session, "asof": now.isoformat(),
            "status": "FAIL" if any(row["status"] == "FAIL" for row in gates) else
                      "PASS" if all(row["status"] == "PASS" for row in gates) else "INCOMPLETE",
            "code_sha": config.get("code_sha"), "config_hash": config.get("config_hash"),
            "feed": config.get("feed"), "gates": gates, "preparation": preparation,
            "decision_reasons": dict(Counter(row["reason"] for row in decisions)),
            "trade_audits": trade_audits,
            "latest_latency_window": active_telemetry[-1] if active_telemetry else None,
            "source_errors": {name: len(grouped[name]) for name in ("BACKFILL_ERROR", "PREPARATION_SOURCE_ERROR")},
            "scope": "shadow runtime evidence only; no strategy promotion or broker lifecycle certification"}
