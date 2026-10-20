"""Offline audit probes. All broker and network boundaries are mocked."""

import ast
import contextlib
import inspect
import io
import json
from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.engine import simulate_trade
from backtest.metrics import calculate_max_drawdown
from paper_trading.config import PaperTradingConfig
from paper_trading.failed_breakdown_reversal import find_failed_breakdown_reversal_signal
from paper_trading.order_plan import build_bracket_order_plan
from paper_trading.post_news_breakout import find_post_news_breakout_signal
from paper_trading.risk import check_order_risk
from paper_trading.signal_builder import build_retest_body_paper_trade_decision
from paper_trading.vwap_ema_cross import build_three_minute_bars
from scripts import paper_trade as watch
from scripts.realtime_runner import next_runner_start
from strategy.market_calendar import SessionQuality


def frame(rows, start="2026-10-19 09:30"):
    result = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    result["timestamp_ny"] = pd.date_range(
        start, periods=len(rows), freq="min", tz="America/New_York"
    )
    result["volume"] = 1000
    return result


def emit(name, **values):
    print(json.dumps({"probe": name, **values}, default=str, sort_keys=True))


reversal = frame([
    (100, 100.5, 99.5, 100),
    (100, 100.4, 99.6, 100.1),
    (100.1, 100.3, 99.7, 100),
    (100, 100.4, 99.6, 100.1),
    (100.1, 100.3, 99.7, 100),
    (100, 100.1, 98.8, 99),
    (99, 101.5, 98.9, 101),
    (101, 101.4, 100.8, 101.2),
])
try:
    signal, reason = find_failed_breakdown_reversal_signal(reversal.iloc[:7])
    emit("reversal_missing_next_bar", reason=reason)
except Exception as exc:
    emit("reversal_missing_next_bar", exception=type(exc).__name__, detail=str(exc))

waiting = watch.WAIT_REASONS
emit("premature_lane_retirement", removed_reasons=[
    reason for reason in ["NO_BREAKOUT", "NO_RETEST", "NO_BREAKDOWN",
                          "NO_FAILED_BREAKDOWN_CONFIRMATION", "POST_NEWS_RANGE_NOT_READY"]
    if reason not in waiting
])

plan = build_bracket_order_plan(
    "AAPL", watch.ORB_BODY_STRATEGY_VERSION, "LONG", 100, 99, 102,
    10000, 0.0025, 0.25,
)
checks = [check_order_risk(plan, 10000, PaperTradingConfig().risk_limits(), 0) for _ in range(8)]
emit("aggregate_risk_not_checked", approvals=sum(check.approved for check in checks),
     combined_planned_risk=8 * plan.planned_risk_dollars,
     configured_daily_loss_limit=10000 * PaperTradingConfig().max_daily_loss_fraction)

emit("scheduler_mid_session_restart",
     restarted_at="2026-10-19T09:26:00-04:00",
     next_run=next_runner_start(datetime(2026, 10, 19, 9, 26, tzinfo=ZoneInfo("America/New_York")), time(9, 25)))
emit("drawdown_starting_losses", returns=[-1.0, -1.0],
     calculated=calculate_max_drawdown(pd.Series([-1.0, -1.0])), expected=-2.0)

ambiguous = frame([(100, 111, 89, 100)], start="2026-10-19 09:40")
emit("breakeven_same_bar_ordering", result=simulate_trade(
    ambiguous, ambiguous.iloc[0]["timestamp_ny"], "LONG", 100, 90, 120, break_even_r=1
), conservative_expected_r=-1.0)

incomplete = frame([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100),
                    (100, 105, 99, 104)])
bars = build_three_minute_bars(incomplete)
emit("partial_three_minute_bin", source_minutes=len(incomplete), bars=len(bars),
     last_bin=bars.iloc[-1]["timestamp_ny"], actual_minutes_in_last_bin=1)

late_reclaim = pd.concat([
    reversal.iloc[:6],
    frame([(99, 99.4, 98.8, 99)] * 23 + [(99, 101.5, 98.9, 101), (101, 102, 100.8, 101.2)],
          start="2026-10-19 09:36")
], ignore_index=True)
_, reason = find_failed_breakdown_reversal_signal(late_reclaim)
emit("late_reclaim_after_eight_bar_window", reclaim_at="09:59", reason=reason)

symbol_bars = frame([(100, 101, 99, 100), (100, 102, 100, 101), (101, 102, 89, 90)])
benchmark_bars = frame([(100, 101, 99, 100)] * 3)
symbol_bars["timestamp_ny"] = pd.to_datetime(
    ["2026-10-19 09:30-04:00", "2026-10-19 09:37-04:00", "2026-10-19 09:59-04:00"]
)
benchmark_bars["timestamp_ny"] = symbol_bars["timestamp_ny"]
emit("context_uses_after_signal_bars", signal_time="09:37",
     context=watch.build_relative_strength_context("AAPL", "long", symbol_bars, benchmark_bars),
     expected_asof_symbol_change=0.01)

setup = frame([
    (100, 101, 99, 100.5), (100.5, 101.2, 99.5, 100.7),
    (100.7, 101.3, 99.8, 100.9), (100.9, 101.4, 99.9, 101),
    (101, 101.5, 100, 101.1), (101.2, 102, 101.1, 101.8),
    (101.6, 101.8, 101.4, 101.7), (101.9, 102.4, 101.8, 102.2),
])
config = PaperTradingConfig()
decision = build_retest_body_paper_trade_decision(setup, "AAPL", 10000, config)
assert decision.approved
decision.signal["relative_strength"] = {"alignment": "against", "benchmark_change": -0.01, "relative_change": -0.005}
args = SimpleNamespace(submit=True, equity=10000, source="live", symbol="AAPL", realized_daily_pnl=0,
                       last_entry="10:00", macro_risk_context={"status": "clear"})
quality = SimpleNamespace(quality=SessionQuality.COMPLETE, opening_range_complete=True)
fake_result = SimpleNamespace(payload=SimpleNamespace(
    side="buy", qty=decision.plan.quantity, stop_loss_stop_price=decision.plan.stop_price,
    take_profit_limit_price=decision.plan.target_price), submitted=True)
with (
    patch.object(watch, "create_alpaca_paper_trading_client", return_value=object()),
    patch.object(watch, "get_account_snapshot", return_value=SimpleNamespace(equity=10000, trading_blocked=False)),
    patch.object(watch, "load_live_session", return_value=setup),
    patch.object(watch, "filter_expected_session_minutes", return_value=(setup, quality)),
    patch.object(watch, "build_retest_body_paper_trade_decision", return_value=decision),
    patch.object(watch, "enrich_decision_with_market_context"),
    patch.object(watch, "symbol_is_clear_to_trade", return_value=True),
    patch.object(watch, "append_audit_record"),
    patch.object(watch, "decision_to_audit_record", return_value={}),
    patch.object(watch, "submit_bracket_order", return_value=fake_result) as submit,
    contextlib.redirect_stdout(io.StringIO()),
):
    result_reason, _ = watch.run_once(args, datetime(2026, 10, 19).date(), config, False,
                                    watch.ORB_BODY_STRATEGY_VERSION)
    order_called = submit.called
emit("submission_precedes_watch_rejection", builder_result=result_reason,
     broker_submit_called=order_called,
     subsequent_watch_quality=watch.signal_quality_from_context(decision.signal)[0],
     subsequent_market_rejection=watch.market_direction_rejection_reason(
         watch.ORB_BODY_STRATEGY_VERSION, decision.plan, decision.signal))

cutoff_bars = frame([(100, 103, 99.5, 100.5)], start="2026-10-19 10:14")
trade = watch.WatchedTrade("AAPL", "TEST", plan, cutoff_bars.iloc[0]["timestamp_ny"])
_, outcome = watch.format_watched_trade_cutoff_result(trade, cutoff_bars, "10:15")
emit("cutoff_ignores_last_bar_target_touch", cutoff_r=outcome.r_multiple,
     last_bar_high=103, target=plan.target_price, expected_target_r=2.0)

pre_cutoff = frame([(100, 101, 99, 100)] * 45)
_, reason = find_post_news_breakout_signal(pre_cutoff)
emit("post_news_while_watcher_active", at="10:14", reason=reason,
     watcher_cutoff="10:15", last_entry="10:00", news_window_end="10:30")
