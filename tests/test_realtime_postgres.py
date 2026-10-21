"""Optional PostgreSQL integration; creates and removes only its own UUID schema."""
import os
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from realtime.config import RuntimeConfig
from realtime.acceptance import session_report
from realtime.journal import Journal


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="Explicit opt-in required for local PostgreSQL integration")
def test_postgres_schema_lease_reservation_and_recovery():
    from dotenv import load_dotenv

    load_dotenv()
    url = os.getenv("REALTIME_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgres"):
        pytest.skip("No PostgreSQL URL configured")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    schema = "runtime_test_" + uuid4().hex
    control = create_engine(url)
    journals = []
    created = False
    try:
        with control.begin() as connection:
            connection.execute(CreateSchema(schema))
            created = True
        first = Journal(url, schema=schema)
        second = Journal(url, schema=schema)
        journals = [first, second]
        now = pd.Timestamp("2026-10-19 09:37", tz="America/New_York")
        assert first.acquire("owner", now)
        assert not second.acquire("other", now)
        payload = {"side": "long", "entry": 100, "stop": 99, "target": 102,
                   "quantity": 25, "risk": 25, "notional": 2500}
        assert first.open_shadow("2026-10-19", "AAPL", "g", payload, RuntimeConfig(), "owner", now) == "APPROVED"
        assert second.shadow_positions("2026-10-19")[0]["symbol"] == "AAPL"
        first.mark_path_gap("2026-10-19", "AAPL", "QUOTE_OBSERVATION_GAP", {"gap_seconds": 20, "asof": now})
        assert second.shadow_positions("2026-10-19")[0]["payload"]["unverified_reasons"]["QUOTE_OBSERVATION_GAP"]["gap_seconds"] == 20
        first.close_shadow("2026-10-19", "AAPL", 102, "TARGET", now)
        assert second.shadow_positions("2026-10-19")[0]["pnl"] == 50
        first.event("2026-10-19", "SESSION_START", {
            "code_sha": "integration-sha", "config_hash": "test", "broker_submission": "DISABLED",
        })
        reader = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
        try:
            report = session_report(reader, "2026-10-19", now, "integration-sha")
            assert report["status"] == "INCOMPLETE"
            assert report["code_sha"] == "integration-sha"
            assert next(row for row in report["gates"] if row["name"] == "release_identity")["status"] == "PASS"
            assert not report["source_errors"]["BACKFILL_ERROR"]
            assert not report["trade_audits"][0]["verified_path"]
            assert report["trade_audits"][0]["r_calculated_from_record"] == 2
        finally:
            reader.dispose()
    finally:
        for journal in journals:
            journal.engine.dispose()
        if created:
            with control.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        control.dispose()
