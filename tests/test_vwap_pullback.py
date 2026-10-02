import pandas as pd

from paper_trading.config import PaperTradingConfig
from paper_trading.vwap_pullback import (
    STRATEGY_VERSION,
    VWAPPullbackConfig,
    add_session_vwap,
    build_vwap_pullback_paper_trade_decision,
    find_vwap_trend_pullback_signal,
)


def _session(candles):
    base = pd.Timestamp("2026-10-01 09:35", tz="America/New_York")
    return pd.DataFrame(
        [
            {
                "timestamp_ny": base + pd.Timedelta(minutes=index),
                "open": candle[0],
                "high": candle[1],
                "low": candle[2],
                "close": candle[3],
                "volume": candle[4] if len(candle) > 4 else 1000,
            }
            for index, candle in enumerate(candles)
        ]
    )


def test_add_session_vwap_calculates_intraday_vwap():
    df = add_session_vwap(
        _session(
            [
                (100.0, 101.0, 99.0, 100.0, 100),
                (101.0, 102.0, 100.0, 101.0, 100),
            ]
        )
    )

    assert "session_vwap" in df.columns
    assert round(float(df.iloc[-1]["session_vwap"]), 2) == 100.50


def test_find_vwap_trend_pullback_long_signal():
    df = _session(
        [
            (100.0, 100.5, 99.8, 100.3),
            (100.4, 101.0, 100.2, 100.8),
            (100.8, 101.5, 100.7, 101.2),
            (101.2, 102.0, 101.1, 101.8),
            (101.8, 102.3, 101.5, 102.0),
            (101.4, 101.9, 100.8, 101.7),
            (101.8, 102.5, 101.7, 102.3),
        ]
    )

    signal, reason = find_vwap_trend_pullback_signal(
        df,
        VWAPPullbackConfig(
            earliest_signal_minute="09:40",
            trend_lookback_bars=5,
            min_bars_on_trend_side=3,
        ),
    )

    assert reason == "APPROVED"
    assert signal["direction"] == "LONG"
    assert signal["entry_price"] == 101.8


def test_find_vwap_trend_pullback_short_signal():
    df = _session(
        [
            (100.0, 100.2, 99.3, 99.5),
            (99.5, 99.7, 98.8, 99.0),
            (99.0, 99.2, 98.2, 98.5),
            (98.5, 98.7, 97.8, 98.1),
            (98.1, 98.3, 97.5, 97.8),
            (98.4, 99.0, 97.9, 98.0),
            (97.9, 98.0, 97.0, 97.2),
        ]
    )

    signal, reason = find_vwap_trend_pullback_signal(
        df,
        VWAPPullbackConfig(
            earliest_signal_minute="09:40",
            trend_lookback_bars=5,
            min_bars_on_trend_side=3,
        ),
    )

    assert reason == "APPROVED"
    assert signal["direction"] == "SHORT"
    assert signal["entry_price"] == 97.9


def test_build_vwap_pullback_paper_trade_decision():
    df = _session(
        [
            (100.0, 100.5, 99.8, 100.3),
            (100.4, 101.0, 100.2, 100.8),
            (100.8, 101.5, 100.7, 101.2),
            (101.2, 102.0, 101.1, 101.8),
            (101.8, 102.3, 101.5, 102.0),
            (101.4, 101.9, 100.8, 101.7),
            (101.8, 102.5, 101.7, 102.3),
        ]
    )

    decision = build_vwap_pullback_paper_trade_decision(
        session_df=df,
        symbol="NVDA",
        account_equity=10_000.0,
        paper_config=PaperTradingConfig(
            risk_fraction=0.01,
            max_trade_risk_fraction=0.01,
        ),
        vwap_config=VWAPPullbackConfig(
            earliest_signal_minute="09:40",
            trend_lookback_bars=5,
            min_bars_on_trend_side=3,
        ),
    )

    assert decision.approved
    assert decision.reason == "APPROVED"
    assert decision.plan.strategy_version == STRATEGY_VERSION
    assert decision.plan.position_side == "long"
