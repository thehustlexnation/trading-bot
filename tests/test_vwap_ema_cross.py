import pandas as pd

from paper_trading.config import PaperTradingConfig
from paper_trading.vwap_ema_cross import (
    STRATEGY_VERSION,
    VWAPEMACrossConfig,
    add_vwap_ema9,
    build_three_minute_bars,
    build_vwap_ema9_cross_paper_trade_decision,
    find_vwap_ema9_cross_signal,
)


def _session(closes: list[float]) -> pd.DataFrame:
    base = pd.Timestamp("2026-10-01 09:30", tz="America/New_York")
    rows = []
    previous = closes[0]
    for index, close in enumerate(closes):
        open_price = previous
        high = max(open_price, close) + 0.08
        low = min(open_price, close) - 0.08
        rows.append(
            {
                "timestamp_ny": base + pd.Timedelta(minutes=index),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000,
            }
        )
        previous = close
    return pd.DataFrame(rows)


def test_build_three_minute_bars_aggregates_session():
    bars = build_three_minute_bars(_session([100, 101, 102, 103]))

    assert len(bars) == 2
    assert float(bars.iloc[0]["open"]) == 100
    assert float(bars.iloc[0]["close"]) == 102
    assert float(bars.iloc[1]["open"]) == 102


def test_add_vwap_ema9_uses_ohlc4_vwap_and_close_ema():
    bars = build_three_minute_bars(_session([100, 101, 102, 103, 104, 105]))
    enriched = add_vwap_ema9(bars, ema_length=3)

    assert "session_vwap" in enriched.columns
    assert "ema9" in enriched.columns
    assert round(float(enriched.iloc[0]["ema9"]), 2) == 102.00


def test_find_vwap_ema9_cross_long_signal():
    df = _session(
        [
            100, 99.8, 99.6,
            99.5, 99.3, 99.2,
            99.4, 99.7, 100.2,
            100.8, 101.5, 102.2,
            102.8, 103.2, 103.6,
        ]
    )

    signal, reason = find_vwap_ema9_cross_signal(
        df,
        VWAPEMACrossConfig(
            ema_length=3,
            earliest_signal_minute="09:30",
        ),
    )

    assert reason == "APPROVED"
    assert signal["direction"] == "LONG"
    assert signal["strategy_version"] == STRATEGY_VERSION


def test_find_vwap_ema9_cross_short_signal():
    df = _session(
        [
            100, 100.4, 100.8,
            101.0, 101.2, 101.4,
            101.0, 100.4, 99.8,
            99.2, 98.6, 98.0,
            97.4, 97.0, 96.8,
        ]
    )

    signal, reason = find_vwap_ema9_cross_signal(
        df,
        VWAPEMACrossConfig(
            ema_length=3,
            earliest_signal_minute="09:30",
        ),
    )

    assert reason == "APPROVED"
    assert signal["direction"] == "SHORT"


def test_build_vwap_ema9_cross_paper_trade_decision():
    df = _session(
        [
            100, 99.8, 99.6,
            99.5, 99.3, 99.2,
            99.4, 99.7, 100.2,
            100.8, 101.5, 102.2,
            102.8, 103.2, 103.6,
        ]
    )

    decision = build_vwap_ema9_cross_paper_trade_decision(
        session_df=df,
        symbol="NVDA",
        account_equity=10_000.0,
        paper_config=PaperTradingConfig(
            risk_fraction=0.01,
            max_trade_risk_fraction=0.01,
        ),
        cross_config=VWAPEMACrossConfig(
            ema_length=3,
            earliest_signal_minute="09:30",
        ),
    )

    assert decision.approved
    assert decision.reason == "APPROVED"
    assert decision.plan.strategy_version == STRATEGY_VERSION
    assert decision.plan.position_side == "long"
