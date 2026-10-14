import pandas as pd

from paper_trading.config import PaperTradingConfig
from paper_trading.post_news_breakout import (
    STRATEGY_VERSION,
    PostNewsBreakoutConfig,
    build_post_news_breakout_paper_trade_decision,
    find_post_news_breakout_signal,
)


def post_news_fixture() -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-10-14 10:30",
        periods=8,
        freq="3min",
        tz="America/New_York",
    )
    rows = [
        (100, 101, 99, 100.5),
        (100.5, 101, 100, 100.4),
        (100.4, 101, 99.5, 100.6),
        (100.6, 101, 100, 100.7),
        (100.7, 101, 100.2, 100.8),
        (100.8, 103, 100.7, 102.5),
        (102.5, 102.6, 101, 102.2),
        (102.2, 103, 102, 102.8),
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


def test_post_news_breakout_retest_enters_after_confirmation():
    signal, reason = find_post_news_breakout_signal(
        post_news_fixture(),
        PostNewsBreakoutConfig(news_window_end="10:30"),
    )

    assert reason == "APPROVED"
    assert signal["strategy_version"] == STRATEGY_VERSION
    assert signal["direction"] == "LONG"
    assert signal["breakout_timestamp"].strftime("%H:%M") == "10:45"
    assert signal["retest_timestamp"].strftime("%H:%M") == "10:48"
    assert signal["entry_timestamp"].strftime("%H:%M") == "10:51"


def test_post_news_decision_builds_two_r_bracket():
    decision = build_post_news_breakout_paper_trade_decision(
        session_df=post_news_fixture(),
        symbol="AAPL",
        account_equity=10_000.0,
        paper_config=PaperTradingConfig(),
    )

    assert decision.approved
    assert decision.plan.strategy_version == STRATEGY_VERSION
    assert decision.plan.target_price > decision.plan.entry_price_reference
