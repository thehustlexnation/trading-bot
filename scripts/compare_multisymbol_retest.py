from __future__ import annotations

import pandas as pd

from backtest.engine import simulate_trade
from backtest.metrics import summarize_trades
from scripts.run_backtest import (
    is_strategy_session_eligible,
    load_data,
)
from strategy.orb import calculate_opening_range
from strategy.session import add_new_york_time
from strategy.signals import (
    find_first_orb_breakout,
    find_first_orb_retest_reclaim_body,
)
from strategy.market_calendar import compare_session_minutes, get_expected_regular_session_minutes


SYMBOLS = [
    "NVDA",
    "AAPL",
    "META",
    "GOOGL",
    "TQQQ",
    "AMD",
    "AVGO",
    "SNDK",
    "INTC",
    "TSLA",
]

PERIODS = {
    "2024": ("2024-01-02", "2024-12-31"),
    "2025": ("2025-01-01", "2025-12-31"),
    "2026": ("2026-01-01", "2026-12-31"),
}

TARGET_R = 2.0
RETEST_MAX_BARS = 5
RETEST_TOLERANCE_OR_FRACTION = 0.10


def prepare_sessions(df: pd.DataFrame) -> list[tuple[object, pd.DataFrame, object]]:
    sessions = []
    for trading_date, day_df in df.groupby(df["timestamp_ny"].dt.date, sort=True):
        expected_minutes = get_expected_regular_session_minutes(trading_date)
        if expected_minutes.empty:
            report = compare_session_minutes(trading_date, [])
            sessions.append((trading_date, day_df.iloc[0:0].copy(), report))
            continue

        expected_set = set(expected_minutes)
        scheduled_df = day_df[
            day_df["timestamp_ny"].dt.floor("min").isin(expected_set)
        ].sort_values("timestamp_ny")
        report = compare_session_minutes(
            trading_date,
            scheduled_df["timestamp_ny"],
        )
        sessions.append((trading_date, scheduled_df, report))
    return sessions


def run_strategy(
    ticker: str,
    sessions: list[tuple[object, pd.DataFrame, object]],
    strategy_version: str,
) -> dict:
    if not sessions:
        return {
            "ticker": ticker,
            "strategy": strategy_version,
            "trades": 0,
            "total_r": 0.0,
            "average_r": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "breakouts": 0,
            "valid_setups": 0,
        }

    breakouts = 0
    valid_setups = 0
    trades = []

    for trading_date, day_df, report in sessions:
        if not is_strategy_session_eligible(report):
            continue

        orb = calculate_opening_range(day_df, opening_minutes=5)
        signal = find_first_orb_breakout(
            day_df,
            or_high=orb["or_high"],
            or_low=orb["or_low"],
        )
        if signal is None:
            continue
        breakouts += 1

        if strategy_version == "ORB_RETEST_RECLAIM_BODY_2R":
            signal = find_first_orb_retest_reclaim_body(
                day_df,
                or_high=orb["or_high"],
                or_low=orb["or_low"],
                max_bars=RETEST_MAX_BARS,
                tolerance_or_fraction=RETEST_TOLERANCE_OR_FRACTION,
            )
            if signal is None:
                continue
        elif strategy_version != "ORB_BASELINE_2R":
            raise ValueError(f"Unsupported strategy: {strategy_version}")

        entry_price = float(signal["entry_price"])
        if signal["direction"] == "LONG":
            stop_price = float(orb["or_low"])
            risk = entry_price - stop_price
            target_price = entry_price + risk * TARGET_R
        else:
            stop_price = float(orb["or_high"])
            risk = stop_price - entry_price
            target_price = entry_price - risk * TARGET_R

        if risk <= 0:
            continue

        valid_setups += 1
        result = simulate_trade(
            session_df=day_df,
            entry_time=signal["entry_timestamp"],
            direction=signal["direction"],
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            break_even_r=None,
        )
        trades.append({
            "trade_date": trading_date,
            "direction": signal["direction"],
            "r_multiple": result["r_multiple"],
            "outcome": result["outcome"],
        })

    metrics = summarize_trades(pd.DataFrame(trades))
    return {
        "ticker": ticker,
        "strategy": strategy_version,
        "trades": metrics["trades"],
        "total_r": metrics["total_r"],
        "average_r": metrics["average_r"],
        "profit_factor": metrics["profit_factor"],
        "max_drawdown": metrics["max_drawdown"],
        "breakouts": breakouts,
        "valid_setups": valid_setups,
    }


def main() -> None:
    rows = []
    for period, (start_date, end_date) in PERIODS.items():
        for ticker in SYMBOLS:
            print(f"Loading {ticker} {period}...", flush=True)
            df = add_new_york_time(load_data(ticker, start_date, end_date))
            sessions = prepare_sessions(df) if not df.empty else []
            for strategy in ("ORB_BASELINE_2R", "ORB_RETEST_RECLAIM_BODY_2R"):
                print(f"  Running {strategy}...", flush=True)
                result = run_strategy(ticker, sessions, strategy)
                result["period"] = period
                rows.append(result)

    results = pd.DataFrame(rows)
    print()
    print("MULTI-SYMBOL SUMMARY")
    print("-" * 80)
    print(results.to_string(index=False))

    pivot = results.pivot_table(
        index=["period", "ticker"],
        columns="strategy",
        values=["trades", "total_r", "average_r", "profit_factor", "max_drawdown"],
        aggfunc="first",
    )
    print()
    print("BASELINE VS BODY RETEST")
    print("-" * 80)
    print(pivot.to_string())

    totals = results.groupby(["period", "strategy"], as_index=False).agg(
        symbols=("ticker", "nunique"),
        trades=("trades", "sum"),
        total_r=("total_r", "sum"),
        avg_symbol_r=("total_r", "mean"),
        median_symbol_r=("total_r", "median"),
        positive_symbols=("total_r", lambda s: int((s > 0).sum())),
    )
    print()
    print("PERIOD TOTALS")
    print("-" * 80)
    print(totals.to_string(index=False))


if __name__ == "__main__":
    main()
