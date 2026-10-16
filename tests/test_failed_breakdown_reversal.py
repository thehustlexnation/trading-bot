import pandas as pd
import pytest

from paper_trading.config import PaperTradingConfig
from paper_trading.failed_breakdown_reversal import (
    STRATEGY_VERSION,
    build_failed_breakdown_reversal_paper_trade_decision,
    find_failed_breakdown_reversal_signal,
)


def failed_breakdown_fixture() -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-10-16 09:30",
        periods=8,
        freq="1min",
        tz="America/New_York",
    )
    rows = [
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.4, 99.6, 100.1),
        (100.1, 100.3, 99.7, 100.0),
        (100.0, 100.4, 99.6, 100.1),
        (100.1, 100.3, 99.7, 100.0),
        (100.0, 99.2, 98.8, 99.0),
        (99.0, 101.5, 98.9, 101.0),
        (101.0, 101.4, 100.8, 101.2),
    ]
    return pd.DataFrame(
        [
            {
                "timestamp_ny": timestamp,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000,
            }
            for timestamp, (open_price, high, low, close) in zip(timestamps, rows)
        ]
    )


def test_failed_breakdown_reversal_enters_after_reclaim_confirmation():
    signal, reason = find_failed_breakdown_reversal_signal(failed_breakdown_fixture())

    assert reason == "APPROVED"
    assert signal["strategy_version"] == STRATEGY_VERSION
    assert signal["direction"] == "LONG"
    assert signal["breakdown_timestamp"].strftime("%H:%M") == "09:35"
    assert signal["confirmation_timestamp"].strftime("%H:%M") == "09:36"
    assert signal["entry_timestamp"].strftime("%H:%M") == "09:37"


def test_failed_breakdown_reversal_builds_two_r_long_bracket():
    decision = build_failed_breakdown_reversal_paper_trade_decision(
        session_df=failed_breakdown_fixture(),
        symbol="AAPL",
        account_equity=10_000.0,
        paper_config=PaperTradingConfig(),
    )

    assert decision.approved
    assert decision.plan.strategy_version == STRATEGY_VERSION
    assert decision.plan.position_side == "long"
    assert decision.plan.target_price > decision.plan.entry_price_reference
    assert decision.plan.target_price - decision.plan.entry_price_reference == pytest.approx(
        2 * decision.plan.risk_per_share
    )
