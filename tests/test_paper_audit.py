import json

import pandas as pd

from paper_trading.audit import (
    append_audit_record,
    append_watch_journal_record,
    decision_to_audit_record,
)
from paper_trading.config import PaperTradingConfig
from paper_trading.signal_builder import build_retest_body_paper_trade_decision


def _session(candles):
    base = pd.Timestamp("2024-01-02 09:30", tz="America/New_York")
    rows = []
    for index, candle in enumerate(candles):
        rows.append(
            {
                "timestamp_ny": base + pd.Timedelta(minutes=index),
                "open": candle[0],
                "high": candle[1],
                "low": candle[2],
                "close": candle[3],
                "volume": 1000,
            }
        )
    return pd.DataFrame(rows)


def test_decision_to_audit_record_contains_plan():
    df = _session(
        [
            (100.0, 101.0, 99.0, 100.5),
            (100.5, 101.2, 99.5, 100.7),
            (100.7, 101.3, 99.8, 100.9),
            (100.9, 101.4, 99.9, 101.0),
            (101.0, 101.5, 100.0, 101.1),
            (101.2, 102.0, 101.1, 101.8),
            (101.6, 101.8, 101.4, 101.7),
            (101.9, 102.4, 101.8, 102.2),
        ]
    )
    decision = build_retest_body_paper_trade_decision(
        session_df=df,
        symbol="AAPL",
        account_equity=10_000.0,
        config=PaperTradingConfig(risk_fraction=0.01, max_trade_risk_fraction=0.01),
    )

    record = decision_to_audit_record(
        decision=decision,
        symbol="AAPL",
        trading_date="2024-01-02",
        source="db",
        dry_run=True,
    )

    assert record["approved"]
    assert record["reason"] == "APPROVED"
    assert record["plan"]["position_side"] == "long"
    assert record["order"] is None


def test_append_audit_record_writes_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"

    append_audit_record(
        {"symbol": "AAPL", "approved": True},
        path=path,
    )

    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0]) == {
        "approved": True,
        "symbol": "AAPL",
    }


def test_append_watch_journal_record_writes_jsonl(tmp_path):
    path = tmp_path / "watch_journal.jsonl"

    append_watch_journal_record(
        {
            "trading_date": "2026-10-13",
            "total_r": -0.32,
            "trade_outcomes": [
                {"symbol": "AAPL", "outcome": "cutoff close"},
            ],
        },
        path=path,
    )

    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["trading_date"] == "2026-10-13"
