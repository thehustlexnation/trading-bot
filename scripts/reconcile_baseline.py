"""Explain the old handoff versus current stored AAPL control without rewriting trades."""
import json

import pandas as pd
from sqlalchemy import text

from backtest.metrics import summarize_trades
from database.connection import engine
from scripts.compare_retest import BASELINE_VERSION, END_DATE, START_DATE, TICKER, load_trades


def reconcile(trades, session_counts):
    first_session = min(session_counts) if session_counts else None
    excluded, comparable = [], []
    for row in trades.sort_values(["trade_date", "entry_time"]).to_dict("records"):
        day = row["trade_date"]
        reason = None
        if session_counts.get(day, 0) != 390:
            reason = "Legacy policy required exactly 390 bars between 09:30 and 16:00."
        elif day == first_session:
            reason = "Legacy fixed-R runner required previous-day levels inside the loaded sample."
        if reason:
            excluded.append({"trade_date": str(day), "r_multiple": float(row["r_multiple"]),
                             "legacy_regular_bar_count": session_counts.get(day, 0), "reason": reason})
        else:
            comparable.append(row)
    current = summarize_trades(trades)
    old = summarize_trades(pd.DataFrame(comparable))
    return {
        "strategy": BASELINE_VERSION, "ticker": TICKER, "start": START_DATE, "end": END_DATE,
        "current": current, "legacy_eligibility_subset": old,
        "added_sessions": excluded,
        "added_session_r": sum(row["r_multiple"] for row in excluded),
        "handoff_matches": len(comparable) == 247 and abs(float(old["total_r"]) - 2.43602023) < 1e-6
                           and abs(float(old["max_drawdown"]) + 17.39442940) < 1e-6,
        "scope": "Stored trades partitioned by historical session eligibility; not an independent replay or data-version proof.",
    }


def main():
    query = text("""
        SELECT DATE(local_time) AS trade_date, COUNT(*) AS bars
        FROM (
            SELECT timestamp AT TIME ZONE 'America/New_York' AS local_time
            FROM market_candles WHERE ticker = :ticker
        ) AS regular
        WHERE DATE(local_time) BETWEEN :start AND :end
          AND CAST(local_time AS TIME) >= TIME '09:30'
          AND CAST(local_time AS TIME) < TIME '16:00'
        GROUP BY DATE(local_time) ORDER BY trade_date
    """)
    with engine.connect() as conn:
        counts = dict(conn.execute(query, {"ticker": TICKER, "start": START_DATE, "end": END_DATE}).all())
    trades = load_trades(BASELINE_VERSION)
    if trades.empty:
        raise RuntimeError("No stored control trades to reconcile.")
    print(json.dumps(reconcile(trades, counts), indent=2, default=float))


if __name__ == "__main__":
    main()
