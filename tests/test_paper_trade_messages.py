from dataclasses import dataclass

import pandas as pd
import pytest

from scripts.paper_trade import (
    HTF_BREAKOUT_STRATEGY_VERSION,
    ORB_BODY_STRATEGY_VERSION,
    VWAP_PULLBACK_STRATEGY_VERSION,
    TradeOutcome,
    WatchedTrade,
    build_approval_url,
    build_relative_strength_context,
    build_strategy_lanes,
    calculate_intraday_change,
    evaluate_watched_trade,
    format_watched_trade_cutoff_result,
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
    symbol: str = "AAPL"
    strategy_version: str = ORB_BODY_STRATEGY_VERSION
    position_side: str = "long"
    entry_side: str = "buy"
    quantity: int = 13
    entry_price_reference: float = 182.675
    stop_price: float = 181.75
    target_price: float = 184.525
    risk_per_share: float = 0.925
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
    assert "Signal quality: B" in message
    assert "Relative strength: unavailable" in message
    assert "Shares: 13" in message
    assert "Entry reference: 182.68" in message
    assert "Stop loss: 181.75" in message
    assert "Take profit: 184.53" in message
    assert "This GitHub alert does not place the trade" in message


def test_format_approved_message_includes_relative_strength_quality():
    message = format_approved_message(
        args=FakeArgs(symbol="NVDA"),
        decision=FakeDecision(
            plan=FakePlan(symbol="NVDA"),
            signal={
                "breakout_timestamp": "2024-01-05 09:35",
                "retest_timestamp": "2024-01-05 09:36",
                "confirmation_timestamp": "2024-01-05 09:36",
                "relative_strength": {
                    "symbol": "NVDA",
                    "benchmark_symbol": "QQQ",
                    "symbol_change": 0.012,
                    "benchmark_change": 0.004,
                    "relative_change": 0.008,
                    "alignment": "aligned",
                },
            },
        ),
        dry_run=True,
    )

    assert "Signal quality: A" in message
    assert "Relative strength: aligned vs QQQ" in message
    assert "NVDA +1.20%" in message


def test_format_watch_started_message_groups_strategy_lanes():
    message = format_watch_started_message(
        symbols=["AAPL", "NVDA", "SNDK"],
        htf_symbols=["AAPL", "MSFT", "NVDA", "AMZN"],
        strategies=[
            ORB_BODY_STRATEGY_VERSION,
            HTF_BREAKOUT_STRATEGY_VERSION,
            VWAP_PULLBACK_STRATEGY_VERSION,
        ],
        args=FakeArgs(),
        dry_run=True,
    )

    assert "Trading watch started" in message
    assert "Mode: DRY RUN - alerts only" in message
    assert "ORB retest: AAPL, NVDA, SNDK" in message
    assert "VWAP pullback: AAPL, NVDA, SNDK" in message
    assert "HTF breakout: AAPL, MSFT, NVDA, AMZN" in message
    assert "No-trade reasons will be grouped" in message


def test_calculate_intraday_change_uses_first_open_and_last_close():
    df = pd.DataFrame(
        {
            "timestamp_ny": [
                pd.Timestamp("2026-10-02 09:30", tz="America/New_York"),
                pd.Timestamp("2026-10-02 09:31", tz="America/New_York"),
            ],
            "open": [100.0, 101.0],
            "close": [101.0, 103.0],
        }
    )

    assert calculate_intraday_change(df) == 0.03


def test_build_relative_strength_context_scores_long_alignment():
    symbol_df = pd.DataFrame(
        {
            "timestamp_ny": [
                pd.Timestamp("2026-10-02 09:30", tz="America/New_York"),
                pd.Timestamp("2026-10-02 09:31", tz="America/New_York"),
            ],
            "open": [100.0, 102.0],
            "close": [101.0, 104.0],
        }
    )
    benchmark_df = pd.DataFrame(
        {
            "timestamp_ny": [
                pd.Timestamp("2026-10-02 09:30", tz="America/New_York"),
                pd.Timestamp("2026-10-02 09:31", tz="America/New_York"),
            ],
            "open": [100.0, 100.5],
            "close": [100.2, 101.0],
        }
    )

    context = build_relative_strength_context(
        symbol="NVDA",
        position_side="long",
        symbol_session_df=symbol_df,
        benchmark_session_df=benchmark_df,
    )

    assert context["alignment"] == "aligned"
    assert round(context["relative_change"], 4) == 0.03


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
            "NO_VWAP_PULLBACK_CONFIRMATION": [
                ("AAPL", VWAP_PULLBACK_STRATEGY_VERSION),
            ],
        },
        active_lanes={
            ("SNDK", ORB_BODY_STRATEGY_VERSION),
            ("NVDA", VWAP_PULLBACK_STRATEGY_VERSION),
        },
        args=FakeArgs(),
        trade_outcomes=[
            TradeOutcome(
                symbol="AAPL",
                strategy=ORB_BODY_STRATEGY_VERSION,
                outcome="cutoff close",
                r_multiple=0.75,
                estimated_pnl=9.75,
            ),
            TradeOutcome(
                symbol="NVDA",
                strategy=VWAP_PULLBACK_STRATEGY_VERSION,
                outcome="target hit",
                r_multiple=2.0,
                estimated_pnl=48.20,
            ),
            TradeOutcome(
                symbol="AMD",
                strategy=HTF_BREAKOUT_STRATEGY_VERSION,
                outcome="stop hit",
                r_multiple=-1.0,
                estimated_pnl=-22.10,
            ),
        ],
    )

    assert "Trading watch recap" in message
    assert "AAPL: ORB retest" in message
    assert "Trade results:" in message
    assert "AAPL: ORB retest, cutoff close, +0.75R / $+9.75" in message
    assert "NVDA: VWAP pullback, target hit, +2.00R / $+48.20" in message
    assert "AMD: HTF breakout, stop hit, -1.00R / $-22.10" in message
    assert "NO_BREAKOUT: MSFT (HTF), NVDA (HTF)" in message
    assert "NO_VALID_RESISTANCE: GOOGL (HTF)" in message
    assert "NO_VWAP_PULLBACK_CONFIRMATION: AAPL (VWAP)" in message
    assert "SNDK (ORB)" in message
    assert "NVDA (VWAP)" in message


def test_evaluate_watched_trade_sends_one_r_update_once():
    trade = WatchedTrade(
        symbol="AAPL",
        strategy=ORB_BODY_STRATEGY_VERSION,
        plan=FakePlan(
            entry_price_reference=100.0,
            stop_price=99.0,
            target_price=102.0,
            risk_per_share=1.0,
        ),
        signal_time=pd.Timestamp("2026-09-30 09:50", tz="America/New_York"),
    )
    session_df = pd.DataFrame(
        {
            "timestamp_ny": [
                pd.Timestamp("2026-09-30 09:50", tz="America/New_York"),
                pd.Timestamp("2026-09-30 09:51", tz="America/New_York"),
            ],
            "high": [100.50, 101.05],
            "low": [100.00, 100.50],
            "close": [100.25, 100.90],
        }
    )

    message, terminal, outcome = evaluate_watched_trade(trade, session_df)
    repeat_message, repeat_terminal, repeat_outcome = evaluate_watched_trade(trade, session_df)

    assert "AAPL update: +1R reached" in message
    assert terminal is False
    assert outcome is None
    assert repeat_message is None
    assert repeat_terminal is False
    assert repeat_outcome is None


def test_evaluate_watched_trade_reports_target_hit():
    trade = WatchedTrade(
        symbol="AAPL",
        strategy=ORB_BODY_STRATEGY_VERSION,
        plan=FakePlan(
            entry_price_reference=100.0,
            stop_price=99.0,
            target_price=102.0,
            risk_per_share=1.0,
        ),
        signal_time=pd.Timestamp("2026-09-30 09:50", tz="America/New_York"),
    )
    session_df = pd.DataFrame(
        {
            "timestamp_ny": [
                pd.Timestamp("2026-09-30 09:51", tz="America/New_York"),
            ],
            "high": [102.10],
            "low": [100.50],
            "close": [102.00],
        }
    )

    message, terminal, outcome = evaluate_watched_trade(trade, session_df)

    assert "AAPL result: target hit" in message
    assert "Result: +2.00R" in message
    assert "Estimated P/L: $+26.00" in message
    assert "Plain English: if you entered near 100.00" in message
    assert terminal is True
    assert outcome.outcome == "target hit"
    assert outcome.r_multiple == 2.0
    assert outcome.estimated_pnl == 26.0
    assert trade.closed is True


def test_format_watched_trade_cutoff_result_marks_open_result():
    trade = WatchedTrade(
        symbol="AAPL",
        strategy=ORB_BODY_STRATEGY_VERSION,
        plan=FakePlan(
            entry_price_reference=100.0,
            stop_price=99.0,
            target_price=102.0,
            risk_per_share=1.0,
        ),
        signal_time=pd.Timestamp("2026-09-30 09:50", tz="America/New_York"),
    )
    session_df = pd.DataFrame(
        {
            "timestamp_ny": [
                pd.Timestamp("2026-09-30 10:14", tz="America/New_York"),
            ],
            "high": [100.90],
            "low": [100.10],
            "close": [100.75],
        }
    )

    message, outcome = format_watched_trade_cutoff_result(
        trade,
        session_df,
        cutoff="10:15",
    )

    assert "AAPL result: cutoff close" in message
    assert "Open trade result: +0.75R" in message
    assert "Estimated P/L if entered: $+9.75" in message
    assert "this was about +0.75R / $+9.75 on 13 shares at 100.75" in message
    assert outcome.outcome == "cutoff close"
    assert outcome.r_multiple == 0.75
    assert outcome.estimated_pnl == 9.75
    assert trade.closed is True


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
        "ORB_RETEST_RECLAIM_BODY_2R, htf_breakout_retest_2r, vwap_trend_pullback_2r"
    ) == [
        "ORB_RETEST_RECLAIM_BODY_2R",
        "HTF_BREAKOUT_RETEST_2R",
        "VWAP_TREND_PULLBACK_2R",
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
