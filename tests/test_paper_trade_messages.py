from dataclasses import dataclass

import pytest

from scripts.paper_trade import (
    HTF_BREAKOUT_STRATEGY_VERSION,
    ORB_BODY_STRATEGY_VERSION,
    build_approval_url,
    build_strategy_lanes,
    format_approved_message,
    format_watch_recap_message,
    format_watch_started_message,
    parse_strategies,
    parse_symbols,
)


@dataclass
class FakeArgs:
    symbol: str = "AAPL"
    cutoff: str = "10:15"


@dataclass
class FakePlan:
    entry_side: str = "buy"
    quantity: int = 13
    entry_price_reference: float = 182.675
    stop_price: float = 181.75
    target_price: float = 184.525
    planned_risk_dollars: float = 12.025
    notional_dollars: float = 2374.775


@dataclass
class FakeDecision:
    plan: FakePlan
    signal: dict


def test_format_approved_message_is_readable_for_manual_execution():
    message = format_approved_message(
        args=FakeArgs(),
        decision=FakeDecision(
            plan=FakePlan(),
            signal={
                "breakout_timestamp": "2024-01-05 09:35",
                "retest_timestamp": "2024-01-05 09:36",
                "confirmation_timestamp": "2024-01-05 09:36",
            },
        ),
        dry_run=True,
    )

    assert "DRY RUN TRADE ALERT - AAPL" in message
    assert "Suggested action: BUY / go LONG" in message
    assert "Shares: 13" in message
    assert "Entry reference: 182.68" in message
    assert "Stop loss: 181.75" in message
    assert "Take profit: 184.53" in message
    assert "This GitHub alert does not place the trade" in message


def test_format_watch_started_message_groups_strategy_lanes():
    message = format_watch_started_message(
        symbols=["AAPL", "NVDA", "SNDK"],
        htf_symbols=["AAPL", "MSFT", "NVDA", "AMZN"],
        strategies=[ORB_BODY_STRATEGY_VERSION, HTF_BREAKOUT_STRATEGY_VERSION],
        args=FakeArgs(),
        dry_run=True,
    )

    assert "Trading watch started" in message
    assert "Mode: DRY RUN - alerts only" in message
    assert "ORB retest: AAPL, NVDA, SNDK" in message
    assert "HTF breakout: AAPL, MSFT, NVDA, AMZN" in message
    assert "No-trade reasons will be grouped" in message


def test_format_watch_recap_groups_stopped_reasons():
    message = format_watch_recap_message(
        approved_lanes={("AAPL", ORB_BODY_STRATEGY_VERSION)},
        stopped_reasons={
            "NO_BREAKOUT": [
                ("NVDA", HTF_BREAKOUT_STRATEGY_VERSION),
                ("MSFT", HTF_BREAKOUT_STRATEGY_VERSION),
            ],
            "NO_VALID_RESISTANCE": [
                ("GOOGL", HTF_BREAKOUT_STRATEGY_VERSION),
            ],
        },
        active_lanes={("SNDK", ORB_BODY_STRATEGY_VERSION)},
        args=FakeArgs(),
    )

    assert "Trading watch recap" in message
    assert "AAPL: ORB retest" in message
    assert "NO_BREAKOUT: MSFT (HTF), NVDA (HTF)" in message
    assert "NO_VALID_RESISTANCE: GOOGL (HTF)" in message
    assert "SNDK (ORB)" in message


def test_parse_symbols_accepts_comma_separated_symbols():
    assert parse_symbols(
        "AAPL",
        "aapl, nvda, SNDK, intc, googl, aapl",
    ) == ["AAPL", "NVDA", "SNDK", "INTC", "GOOGL"]


def test_parse_symbols_falls_back_to_single_symbol():
    assert parse_symbols("nvda") == ["NVDA"]


def test_parse_symbols_rejects_empty_list():
    with pytest.raises(ValueError, match="At least one symbol"):
        parse_symbols("AAPL", " , ")


def test_parse_strategies_accepts_known_lanes():
    assert parse_strategies(
        "ORB_RETEST_RECLAIM_BODY_2R, htf_breakout_retest_2r"
    ) == [
        "ORB_RETEST_RECLAIM_BODY_2R",
        "HTF_BREAKOUT_RETEST_2R",
    ]


def test_parse_strategies_rejects_unknown_lanes():
    with pytest.raises(ValueError, match="Unknown strategy"):
        parse_strategies("NOPE")


def test_build_strategy_lanes_allows_separate_htf_symbols():
    lanes = build_strategy_lanes(
        symbols=["AAPL", "NVDA"],
        htf_symbols=["AAPL", "MSFT", "GOOGL"],
        strategies=[
            ORB_BODY_STRATEGY_VERSION,
            HTF_BREAKOUT_STRATEGY_VERSION,
        ],
    )

    assert ("AAPL", ORB_BODY_STRATEGY_VERSION) in lanes
    assert ("NVDA", ORB_BODY_STRATEGY_VERSION) in lanes
    assert ("MSFT", HTF_BREAKOUT_STRATEGY_VERSION) in lanes
    assert ("GOOGL", HTF_BREAKOUT_STRATEGY_VERSION) in lanes
    assert ("MSFT", ORB_BODY_STRATEGY_VERSION) not in lanes


def test_build_approval_url_returns_none_without_base_url(monkeypatch):
    monkeypatch.delenv("APPROVAL_BASE_URL", raising=False)

    assert build_approval_url(
        args=FakeArgs(),
        decision=FakeDecision(
            plan=FakePlan(),
            signal={},
        ),
        risk_dollars=100.0,
        submit_paper=True,
    ) is None
