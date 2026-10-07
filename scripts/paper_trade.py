import argparse
from copy import copy
from dataclasses import dataclass
import os
import sys
import time as sleep_time
from datetime import datetime, time
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from paper_trading.alpaca_adapter import submit_bracket_order
from paper_trading.audit import append_audit_record, decision_to_audit_record
from paper_trading.broker import (
    create_alpaca_paper_trading_client,
    get_account_snapshot,
    symbol_is_clear_to_trade,
)
from paper_trading.config import PaperTradingConfig
from paper_trading.htf_breakout import (
    HTFBreakoutConfig,
    STRATEGY_VERSION as HTF_BREAKOUT_STRATEGY_VERSION,
    build_htf_breakout_paper_trade_decision,
)
from paper_trading.notifications import (
    notify,
    notify_email,
    notify_slack,
    notify_telegram,
)
from paper_trading.signal_builder import build_retest_body_paper_trade_decision
from paper_trading.vwap_pullback import (
    STRATEGY_VERSION as VWAP_PULLBACK_STRATEGY_VERSION,
    VWAPPullbackConfig,
    build_vwap_pullback_paper_trade_decision,
)
from paper_trading.vwap_ema_cross import (
    STRATEGY_VERSION as VWAP_EMA_CROSS_STRATEGY_VERSION,
    VWAPEMACrossConfig,
    build_vwap_ema9_cross_paper_trade_decision,
)
from scripts.forexfactory_news_risk import (
    DEFAULT_CALENDAR_URL as FOREXFACTORY_CALENDAR_URL,
    fetch_text as fetch_forexfactory_text,
    filter_news_risk_events,
    parse_calendar_events,
    parse_ny_clock as parse_news_ny_clock,
)
from strategy.market_calendar import (
    SessionQuality,
    compare_session_minutes,
    get_expected_regular_session_minutes,
    is_trading_session,
)
from strategy.session import add_new_york_time


NY_TZ = ZoneInfo("America/New_York")
ORB_BODY_STRATEGY_VERSION = "ORB_RETEST_RECLAIM_BODY_2R"
DEFAULT_HTF_SYMBOLS = (
    "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AMD,AVGO,"
    "JPM,V,MA,NFLX,COST,ORCL,CRM,ADBE,QQQ"
)


@dataclass
class WatchedTrade:
    symbol: str
    strategy: str
    plan: object
    signal_time: object
    one_r_sent: bool = False
    closed: bool = False


@dataclass(frozen=True)
class TradeOutcome:
    symbol: str
    strategy: str
    outcome: str
    r_multiple: float
    estimated_pnl: float


def parse_args():
    parser = argparse.ArgumentParser(
        description="Dry-run or submit one paper ORB retest bracket order."
    )
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols to scan in watch mode.")
    parser.add_argument(
        "--htf-symbols",
        default=DEFAULT_HTF_SYMBOLS,
        help="Comma-separated symbols for HTF_BREAKOUT_RETEST_2R.",
    )
    parser.add_argument(
        "--strategies",
        default=(
            f"{ORB_BODY_STRATEGY_VERSION},"
            f"{HTF_BREAKOUT_STRATEGY_VERSION},"
            f"{VWAP_PULLBACK_STRATEGY_VERSION},"
            f"{VWAP_EMA_CROSS_STRATEGY_VERSION}"
        ),
        help="Comma-separated strategy lanes to scan.",
    )
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today in New York.")
    parser.add_argument("--source", choices=["db", "live"], default="db")
    parser.add_argument("--submit", action="store_true", help="Submit to Alpaca paper account.")
    parser.add_argument("--equity", type=float, default=None, help="Override account equity.")
    parser.add_argument("--realized-daily-pnl", type=float, default=0.0)
    parser.add_argument("--risk-fraction", type=float, default=PaperTradingConfig.risk_fraction)
    parser.add_argument("--max-notional-fraction", type=float, default=PaperTradingConfig.max_notional_fraction)
    parser.add_argument("--max-daily-loss-fraction", type=float, default=PaperTradingConfig.max_daily_loss_fraction)
    parser.add_argument("--disable-shorts", action="store_true")
    parser.add_argument("--watch", action="store_true", help="Keep polling until the cutoff time.")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--cutoff", default="10:15", help="New York HH:MM stop time for watch mode.")
    parser.add_argument("--notify", action="store_true", help="Send macOS notifications for decisions.")
    parser.add_argument("--telegram", action="store_true", help="Send Telegram notifications using TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
    parser.add_argument("--slack", action="store_true", help="Send Slack notifications using SLACK_WEBHOOK_URL.")
    parser.add_argument("--email", action="store_true", help="Send email notifications using SMTP_* secrets.")
    parser.add_argument("--alert-status", action="store_true", help="Send start and no-signal completion alerts.")
    parser.add_argument("--macro-news-start", default="08:00", help="New York HH:MM macro risk window start.")
    parser.add_argument("--macro-news-end", default="10:30", help="New York HH:MM macro risk window end.")
    return parser.parse_args()


def trading_date_from_args(value: str | None):
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.now(tz=NY_TZ).date()


def parse_ny_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def parse_symbols(symbol: str, symbols: str | None = None) -> list[str]:
    raw = symbols if symbols is not None else symbol
    parsed = [
        item.strip().upper()
        for item in raw.split(",")
        if item.strip()
    ]
    if not parsed:
        raise ValueError("At least one symbol is required.")
    return list(dict.fromkeys(parsed))


def parse_strategies(strategies: str) -> list[str]:
    parsed = [
        item.strip().upper()
        for item in strategies.split(",")
        if item.strip()
    ]
    if not parsed:
        raise ValueError("At least one strategy is required.")

    allowed = {
        ORB_BODY_STRATEGY_VERSION,
        HTF_BREAKOUT_STRATEGY_VERSION,
        VWAP_PULLBACK_STRATEGY_VERSION,
        VWAP_EMA_CROSS_STRATEGY_VERSION,
    }
    unknown = sorted(set(parsed) - allowed)
    if unknown:
        raise ValueError(f"Unknown strategy lane(s): {', '.join(unknown)}")
    return list(dict.fromkeys(parsed))


def build_strategy_lanes(
    symbols: list[str],
    strategies: list[str],
    htf_symbols: list[str] | None = None,
) -> set[tuple[str, str]]:
    lanes = set()
    for strategy in strategies:
        strategy_symbols = (
            htf_symbols
            if strategy == HTF_BREAKOUT_STRATEGY_VERSION and htf_symbols is not None
            else symbols
        )
        for symbol in strategy_symbols:
            lanes.add((symbol, strategy))
    return lanes


def args_for_symbol(args, symbol: str):
    symbol_args = copy(args)
    symbol_args.symbol = symbol
    return symbol_args


def load_db_session(symbol: str, trading_date) -> pd.DataFrame:
    from sqlalchemy import text

    from database.connection import engine

    query = text("""
        SELECT
            ticker,
            timestamp,
            open,
            high,
            low,
            close,
            volume,
            trade_count,
            vwap
        FROM market_candles
        WHERE ticker = :symbol
          AND DATE(timestamp AT TIME ZONE 'America/New_York') = :trading_date
        ORDER BY timestamp;
    """)

    with engine.connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params={
                "symbol": symbol.upper(),
                "trading_date": str(trading_date),
            },
        )

    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return add_new_york_time(df)


def build_approval_url(args, decision, risk_dollars: float, submit_paper: bool) -> str | None:
    base_url = os.getenv("APPROVAL_BASE_URL")
    if not base_url or decision is None or decision.plan is None:
        return None

    plan = decision.plan
    direction = "LONG" if plan.entry_side == "buy" else "SHORT"
    query = urlencode(
        {
            "symbol": plan.symbol,
            "strategy": plan.strategy_version,
            "direction": direction,
            "entry": f"{plan.entry_price_reference:.4f}",
            "stop": f"{plan.stop_price:.4f}",
            "target": f"{plan.target_price:.4f}",
            "risk_dollars": f"{risk_dollars:.2f}",
            "submit_paper": "true" if submit_paper else "false",
        }
    )
    return f"{base_url.rstrip('/')}/approve?{query}"


def send_mobile_alert(args, message: str) -> bool:
    sent = notify_telegram(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        message=message,
        enabled=args.telegram,
    )
    sent = notify_slack(
        webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
        message=message,
        enabled=args.slack,
    ) or sent
    sent = notify_email(
        smtp_host=os.getenv("SMTP_HOST"),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME"),
        smtp_password=os.getenv("SMTP_PASSWORD"),
        sender=os.getenv("SMTP_SENDER"),
        recipient=os.getenv("ALERT_EMAIL_TO"),
        subject=f"Trading Alert: {args.symbol.upper()}",
        message=message,
        enabled=args.email,
    ) or sent
    return sent


def _format_symbol_list(symbols: list[str], max_items: int = 8) -> str:
    if len(symbols) <= max_items:
        return ", ".join(symbols)
    shown = ", ".join(symbols[:max_items])
    return f"{shown}, +{len(symbols) - max_items} more"


def format_watch_started_message(
    symbols: list[str],
    htf_symbols: list[str],
    strategies: list[str],
    args,
    dry_run: bool,
) -> str:
    lines = [
        "Trading watch started",
        "",
        f"Mode: {'DRY RUN - alerts only' if dry_run else 'PAPER SUBMIT'}",
        f"Cutoff: {args.cutoff} New York",
        "",
        "Strategy lanes:",
    ]
    if ORB_BODY_STRATEGY_VERSION in strategies:
        lines.append(f"- ORB retest: {_format_symbol_list(symbols)}")
    if VWAP_PULLBACK_STRATEGY_VERSION in strategies:
        lines.append(f"- VWAP pullback: {_format_symbol_list(symbols)}")
    if VWAP_EMA_CROSS_STRATEGY_VERSION in strategies:
        lines.append(f"- VWAP + EMA9 cross: {_format_symbol_list(symbols)}")
    if HTF_BREAKOUT_STRATEGY_VERSION in strategies:
        lines.append(f"- HTF breakout: {_format_symbol_list(htf_symbols)}")
    lines.extend(
        [
            "",
            "I will send trade alerts immediately. No-trade reasons will be grouped "
            "into a recap so alerts stay readable.",
        ]
    )
    return "\n".join(lines)


def format_watch_skipped_message(
    symbols: list[str],
    htf_symbols: list[str],
    strategies: list[str],
    reason: str,
    args,
    trading_date=None,
) -> str:
    detail = (
        f"Date: {trading_date}"
        if trading_date is not None
        else f"Cutoff: {args.cutoff} New York"
    )
    return (
        f"Trading watch skipped\n\n"
        f"Reason: {reason}\n"
        f"{detail}\n\n"
        f"ORB symbols: {_format_symbol_list(symbols)}\n"
        f"HTF symbols: {_format_symbol_list(htf_symbols)}\n"
        f"Strategies: {', '.join(strategies)}"
    )


def calculate_intraday_change(session_df: pd.DataFrame) -> float | None:
    if session_df.empty:
        return None
    df = session_df.sort_values("timestamp_ny")
    first_open = float(df.iloc[0]["open"])
    last_close = float(df.iloc[-1]["close"])
    if first_open <= 0:
        return None
    return (last_close - first_open) / first_open


def build_relative_strength_context(
    symbol: str,
    position_side: str,
    symbol_session_df: pd.DataFrame,
    benchmark_session_df: pd.DataFrame,
    benchmark_symbol: str = "QQQ",
) -> dict | None:
    symbol_change = calculate_intraday_change(symbol_session_df)
    benchmark_change = calculate_intraday_change(benchmark_session_df)
    if symbol_change is None or benchmark_change is None:
        return None

    relative_change = symbol_change - benchmark_change
    if abs(relative_change) < 0.001:
        alignment = "neutral"
    elif position_side == "long":
        alignment = "aligned" if relative_change > 0 else "against"
    else:
        alignment = "aligned" if relative_change < 0 else "against"

    return {
        "symbol": symbol.upper(),
        "benchmark_symbol": benchmark_symbol.upper(),
        "symbol_change": symbol_change,
        "benchmark_change": benchmark_change,
        "relative_change": relative_change,
        "alignment": alignment,
    }


def signal_quality_from_context(signal: dict) -> tuple[str, str]:
    relative_strength = signal.get("relative_strength")
    if not relative_strength:
        return "B", "Strategy setup confirmed. Market-relative context unavailable."

    alignment = relative_strength.get("alignment")
    if alignment == "aligned":
        return "A", "Strategy setup confirmed and stock is stronger/weaker than benchmark in the trade direction."
    if alignment == "against":
        return "C", "Strategy setup confirmed, but relative strength is against the trade direction."
    return "B", "Strategy setup confirmed; relative strength is neutral."


def format_relative_strength_context(signal: dict) -> str:
    relative_strength = signal.get("relative_strength")
    if not relative_strength:
        return "- Relative strength: unavailable"

    symbol = relative_strength["symbol"]
    benchmark = relative_strength["benchmark_symbol"]
    symbol_change = relative_strength["symbol_change"] * 100
    benchmark_change = relative_strength["benchmark_change"] * 100
    relative_change = relative_strength["relative_change"] * 100
    alignment = relative_strength["alignment"]
    return (
        f"- Relative strength: {alignment} vs {benchmark} "
        f"({symbol} {symbol_change:+.2f}%, {benchmark} {benchmark_change:+.2f}%, "
        f"spread {relative_change:+.2f}%)"
    )


def build_macro_risk_context(
    trading_date,
    start: str = "08:00",
    end: str = "10:30",
) -> dict:
    try:
        start_time = parse_news_ny_clock(start)
        end_time = parse_news_ny_clock(end)
        text = fetch_forexfactory_text(FOREXFACTORY_CALENDAR_URL)
        events = parse_calendar_events(text)
        risk_events = filter_news_risk_events(
            events=events,
            trading_date=trading_date,
            countries={"USD"},
            impacts={"HIGH"},
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "start": start,
            "end": end,
            "reason": f"{type(exc).__name__}: {exc}",
            "events": [],
        }

    return {
        "status": "high" if risk_events else "clear",
        "start": start,
        "end": end,
        "events": risk_events,
    }


def format_macro_risk_context(macro_risk_context: dict | None) -> str:
    if not macro_risk_context:
        return "- Macro risk: unavailable"

    status = macro_risk_context.get("status")
    start = macro_risk_context.get("start", "08:00")
    end = macro_risk_context.get("end", "10:30")
    if status == "clear":
        return f"- Macro risk: clear in {start}-{end} NY window"
    if status == "high":
        events = macro_risk_context.get("events", [])
        if not events:
            return f"- Macro risk: HIGH in {start}-{end} NY window"
        first = events[0]
        first_label = f"{first.event_time.strftime('%H:%M')} NY {first.title}"
        more = f", +{len(events) - 1} more" if len(events) > 1 else ""
        return f"- Macro risk: HIGH ({first_label}{more})"
    reason = macro_risk_context.get("reason", "not available")
    return f"- Macro risk: unavailable ({reason})"


def signal_reference_timestamp(signal: dict):
    return (
        signal.get("entry_timestamp")
        or signal.get("confirmation_timestamp")
        or signal.get("retest_timestamp")
        or signal.get("breakout_timestamp")
        or signal.get("pullback_timestamp")
    )


def minutes_until_cutoff(signal: dict, cutoff: str) -> int | None:
    timestamp = signal_reference_timestamp(signal)
    if timestamp is None:
        return None
    try:
        signal_time = pd.Timestamp(timestamp)
    except (TypeError, ValueError):
        return None
    if signal_time.tzinfo is None:
        signal_time = signal_time.tz_localize(NY_TZ)
    signal_time = signal_time.tz_convert(NY_TZ)
    cutoff_dt = pd.Timestamp(
        datetime.combine(
            signal_time.date(),
            parse_ny_clock(cutoff),
            tzinfo=NY_TZ,
        )
    )
    return int((cutoff_dt - signal_time).total_seconds() // 60)


def format_timing_context(signal: dict, cutoff: str) -> str:
    minutes_left = minutes_until_cutoff(signal, cutoff)
    if minutes_left is None:
        return "- Timing context: unavailable"
    if minutes_left < 0:
        return f"- Timing warning: signal is after cutoff by {abs(minutes_left)} min."
    if minutes_left <= 15:
        return (
            f"- Timing warning: signal fired {minutes_left} min before cutoff; "
            "less time for target."
        )
    return f"- Timing context: {minutes_left} min before cutoff"


def format_approved_message(
    args,
    decision,
    dry_run: bool,
    macro_risk_context: dict | None = None,
) -> str:
    plan = decision.plan
    signal = decision.signal or {}
    mode = "DRY RUN" if dry_run else "PAPER SUBMIT"
    action = "BUY / go LONG" if plan.entry_side == "buy" else "SELL SHORT"
    quality, quality_reason = signal_quality_from_context(signal)
    breakout_time = signal.get("breakout_timestamp", "n/a")
    retest_time = signal.get("retest_timestamp", "n/a")
    pullback_time = signal.get("pullback_timestamp", "n/a")
    confirmation_time = signal.get("confirmation_timestamp", "n/a")
    if plan.strategy_version == VWAP_PULLBACK_STRATEGY_VERSION:
        vwap_reference = signal.get("vwap", "n/a")
        if isinstance(vwap_reference, float):
            vwap_reference = f"{vwap_reference:.2f}"
        what_happened = "VWAP trend pullback and confirmation detected."
        timing_lines = (
            f"- Pullback: {pullback_time}\n"
            f"- Confirmation: {confirmation_time}\n"
            f"- VWAP reference: {vwap_reference}"
        )
    elif plan.strategy_version == VWAP_EMA_CROSS_STRATEGY_VERSION:
        vwap_reference = signal.get("vwap", "n/a")
        ema_reference = signal.get("ema9", "n/a")
        if isinstance(vwap_reference, float):
            vwap_reference = f"{vwap_reference:.2f}"
        if isinstance(ema_reference, float):
            ema_reference = f"{ema_reference:.2f}"
        what_happened = "3-minute VWAP and EMA9 cross confirmation detected."
        timing_lines = (
            f"- Cross: {signal.get('cross_timestamp', 'n/a')}\n"
            f"- Confirmation: {confirmation_time}\n"
            f"- VWAP reference: {vwap_reference}\n"
            f"- EMA9 reference: {ema_reference}"
        )
    elif plan.strategy_version == HTF_BREAKOUT_STRATEGY_VERSION:
        what_happened = "Higher-timeframe resistance breakout, retest, and confirmation detected."
        timing_lines = (
            f"- Breakout: {breakout_time}\n"
            f"- Retest: {retest_time}\n"
            f"- Confirmation: {confirmation_time}"
        )
    else:
        what_happened = "Opening range breakout, retest, and confirmation detected."
        timing_lines = (
            f"- Breakout: {breakout_time}\n"
            f"- Retest: {retest_time}\n"
            f"- Confirmation: {confirmation_time}"
        )
    return (
        f"{mode} TRADE ALERT - {args.symbol.upper()}\n\n"
        f"What happened: {what_happened}\n"
        f"Suggested action: {action}\n\n"
        f"Signal quality: {quality}\n"
        f"- {quality_reason}\n"
        f"{format_relative_strength_context(signal)}\n"
        f"{format_macro_risk_context(macro_risk_context)}\n"
        f"{format_timing_context(signal, args.cutoff)}\n\n"
        f"Manual order details:\n"
        f"- Shares: {plan.quantity}\n"
        f"- Entry reference: {plan.entry_price_reference:.2f}\n"
        f"- Stop loss: {plan.stop_price:.2f}\n"
        f"- Take profit: {plan.target_price:.2f}\n"
        f"- Planned max risk: ${plan.planned_risk_dollars:.2f}\n"
        f"- Approx position value: ${plan.notional_dollars:.2f}\n\n"
        f"Signal timing:\n"
        f"{timing_lines}\n\n"
        f"Reminder: This GitHub alert does not place the trade. "
        f"Only enter manually if the order still makes sense in Alpaca."
    )


def format_watch_recap_message(
    approved_lanes: set[tuple[str, str]],
    stopped_reasons: dict[str, list[tuple[str, str]]],
    active_lanes: set[tuple[str, str]],
    args,
    trade_outcomes: list[TradeOutcome] | None = None,
) -> str:
    lines = [
        "Trading watch recap",
        "",
        f"Cutoff: {args.cutoff} New York",
    ]

    if approved_lanes:
        lines.extend(["", "Trade alerts sent:"])
        for symbol, strategy in sorted(approved_lanes):
            label = _strategy_label(strategy)
            lines.append(f"- {symbol}: {label}")
    else:
        lines.extend(["", "Trade alerts sent: none"])

    if trade_outcomes:
        lines.extend(["", "Trade results:"])
        for outcome in trade_outcomes:
            lines.append(
                f"- {outcome.symbol}: {_strategy_label(outcome.strategy)}, "
                f"{outcome.outcome}, {outcome.r_multiple:+.2f}R / "
                f"${outcome.estimated_pnl:+.2f}"
            )

    if stopped_reasons:
        lines.extend(["", "No-trade / stopped reasons:"])
        for reason in sorted(stopped_reasons):
            lane_labels = [
                f"{symbol} ({_strategy_short_label(strategy)})"
                for symbol, strategy in sorted(stopped_reasons[reason])
            ]
            lines.append(f"- {reason}: {_format_symbol_list(lane_labels, max_items=6)}")

    if active_lanes:
        lane_labels = [
            f"{symbol} ({_strategy_short_label(strategy)})"
            for symbol, strategy in sorted(active_lanes)
        ]
        lines.extend(["", "Still had no approved signal by cutoff:"])
        lines.append(f"- {_format_symbol_list(lane_labels, max_items=6)}")

    lines.extend(
        [
            "",
            "Reminder: alerts are not automatic trades. Check current price vs entry/stop before acting.",
        ]
    )
    return "\n".join(lines)


def _strategy_label(strategy: str) -> str:
    if strategy == ORB_BODY_STRATEGY_VERSION:
        return "ORB retest"
    if strategy == HTF_BREAKOUT_STRATEGY_VERSION:
        return "HTF breakout"
    if strategy == VWAP_PULLBACK_STRATEGY_VERSION:
        return "VWAP pullback"
    if strategy == VWAP_EMA_CROSS_STRATEGY_VERSION:
        return "VWAP + EMA9 cross"
    return strategy


def _strategy_short_label(strategy: str) -> str:
    if strategy == ORB_BODY_STRATEGY_VERSION:
        return "ORB"
    if strategy == HTF_BREAKOUT_STRATEGY_VERSION:
        return "HTF"
    if strategy == VWAP_PULLBACK_STRATEGY_VERSION:
        return "VWAP"
    if strategy == VWAP_EMA_CROSS_STRATEGY_VERSION:
        return "XEMA"
    return strategy


def _tracking_start_time(decision) -> object:
    signal = decision.signal or {}
    return (
        signal.get("entry_timestamp")
        or signal.get("confirmation_timestamp")
        or signal.get("retest_timestamp")
        or signal.get("breakout_timestamp")
        or signal.get("cross_timestamp")
    )


def _r_multiple_at_price(plan, price: float) -> float:
    if plan.position_side == "long":
        return (price - plan.entry_price_reference) / plan.risk_per_share
    return (plan.entry_price_reference - price) / plan.risk_per_share


def _estimated_trade_pnl(plan, r_multiple: float) -> float:
    return plan.quantity * plan.risk_per_share * r_multiple


def _plain_result_line(plan, price: float, r_multiple: float) -> str:
    pnl = _estimated_trade_pnl(plan, r_multiple)
    return (
        f"Plain English: if you entered near {plan.entry_price_reference:.2f}, "
        f"this was about {r_multiple:+.2f}R / ${pnl:+.2f} on {plan.quantity} shares "
        f"at {price:.2f}."
    )


def build_trade_outcome(
    trade: WatchedTrade,
    outcome: str,
    r_multiple: float,
) -> TradeOutcome:
    return TradeOutcome(
        symbol=trade.symbol,
        strategy=trade.strategy,
        outcome=outcome,
        r_multiple=r_multiple,
        estimated_pnl=_estimated_trade_pnl(trade.plan, r_multiple),
    )


def evaluate_watched_trade(
    trade: WatchedTrade,
    session_df: pd.DataFrame,
) -> tuple[str | None, bool, TradeOutcome | None]:
    if trade.closed or session_df.empty:
        return None, False, None
    if "timestamp_ny" not in session_df.columns:
        raise ValueError("session_df must include timestamp_ny.")

    plan = trade.plan
    df = session_df.sort_values("timestamp_ny")
    if trade.signal_time is not None:
        df = df[df["timestamp_ny"] >= pd.Timestamp(trade.signal_time)]
    if df.empty:
        return None, False, None

    for _, candle in df.iterrows():
        high = float(candle["high"])
        low = float(candle["low"])
        timestamp = candle["timestamp_ny"]

        if plan.position_side == "long":
            stop_hit = low <= plan.stop_price
            target_hit = high >= plan.target_price
            one_r_hit = high >= plan.entry_price_reference + plan.risk_per_share
        else:
            stop_hit = high >= plan.stop_price
            target_hit = low <= plan.target_price
            one_r_hit = low <= plan.entry_price_reference - plan.risk_per_share

        if stop_hit and target_hit:
            trade.closed = True
            return (
                f"{plan.symbol} result: STOP assumed first in same candle\n"
                f"Strategy: {_strategy_label(trade.strategy)}\n"
                f"Time: {timestamp}\n"
                f"Result: -1.00R\n"
                f"Estimated P/L: ${_estimated_trade_pnl(plan, -1.0):+.2f}\n"
                f"Note: stop and target were both touched in the same 1-minute candle, "
                f"so we use the conservative stop-first rule.",
                True,
                build_trade_outcome(trade, "stop assumed first", -1.0),
            )

        if stop_hit:
            trade.closed = True
            return (
                f"{plan.symbol} result: stop hit\n"
                f"Strategy: {_strategy_label(trade.strategy)}\n"
                f"Time: {timestamp}\n"
                f"Exit reference: {plan.stop_price:.2f}\n"
                f"Result: -1.00R\n"
                f"Estimated P/L: ${_estimated_trade_pnl(plan, -1.0):+.2f}\n"
                f"{_plain_result_line(plan, plan.stop_price, -1.0)}",
                True,
                build_trade_outcome(trade, "stop hit", -1.0),
            )

        if target_hit:
            trade.closed = True
            return (
                f"{plan.symbol} result: target hit\n"
                f"Strategy: {_strategy_label(trade.strategy)}\n"
                f"Time: {timestamp}\n"
                f"Exit reference: {plan.target_price:.2f}\n"
                f"Result: +2.00R\n"
                f"Estimated P/L: ${_estimated_trade_pnl(plan, 2.0):+.2f}\n"
                f"{_plain_result_line(plan, plan.target_price, 2.0)}",
                True,
                build_trade_outcome(trade, "target hit", 2.0),
            )

        if one_r_hit and not trade.one_r_sent:
            trade.one_r_sent = True
            one_r_price = (
                plan.entry_price_reference + plan.risk_per_share
                if plan.position_side == "long"
                else plan.entry_price_reference - plan.risk_per_share
            )
            return (
                f"{plan.symbol} update: +1R reached\n"
                f"Strategy: {_strategy_label(trade.strategy)}\n"
                f"Time: {timestamp}\n"
                f"+1R reference: {one_r_price:.2f}\n"
                f"Target remains: {plan.target_price:.2f}\n"
                f"Stop remains: {plan.stop_price:.2f}",
                False,
                None,
            )

    return None, False, None


def format_watched_trade_cutoff_result(
    trade: WatchedTrade,
    session_df: pd.DataFrame,
    cutoff: str,
) -> tuple[str | None, TradeOutcome | None]:
    if trade.closed or session_df.empty:
        return None, None
    plan = trade.plan
    df = session_df.sort_values("timestamp_ny")
    if trade.signal_time is not None:
        df = df[df["timestamp_ny"] >= pd.Timestamp(trade.signal_time)]
    if df.empty:
        return None, None

    last = df.iloc[-1]
    close = float(last["close"])
    r_multiple = _r_multiple_at_price(plan, close)
    trade.closed = True
    return (
        f"{plan.symbol} result: cutoff close\n"
        f"Strategy: {_strategy_label(trade.strategy)}\n"
        f"Cutoff: {cutoff} New York\n"
        f"Last checked close: {close:.2f}\n"
        f"Open trade result: {r_multiple:+.2f}R\n"
        f"Estimated P/L if entered: ${_estimated_trade_pnl(plan, r_multiple):+.2f}\n"
        f"{_plain_result_line(plan, close, r_multiple)}\n"
        f"Note: target and stop were not hit before the watch ended."
    ), build_trade_outcome(trade, "cutoff close", r_multiple)


def load_live_session(symbol: str, trading_date) -> pd.DataFrame:
    from data.alpaca_client import get_minute_bars

    session_start = datetime.combine(
        trading_date,
        time(9, 30),
        tzinfo=NY_TZ,
    )
    now_ny = datetime.now(tz=NY_TZ)
    session_end = max(now_ny, session_start)

    bars = get_minute_bars(
        ticker=symbol.upper(),
        start=session_start.astimezone(ZoneInfo("UTC")),
        end=session_end.astimezone(ZoneInfo("UTC")),
    )

    df = bars.df.reset_index()
    if df.empty:
        return df

    df = df.rename(columns={"symbol": "ticker"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return add_new_york_time(df)


def load_live_daily(symbol: str, trading_date, lookback_days: int = 90) -> pd.DataFrame:
    from data.alpaca_client import get_daily_bars

    end = datetime.combine(
        trading_date,
        time(0, 0),
        tzinfo=NY_TZ,
    )
    start = end - pd.Timedelta(days=lookback_days)

    bars = get_daily_bars(
        ticker=symbol.upper(),
        start=start.astimezone(ZoneInfo("UTC")),
        end=end.astimezone(ZoneInfo("UTC")),
    )

    df = bars.df.reset_index()
    if df.empty:
        return df

    df = df.rename(columns={"symbol": "ticker"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def filter_expected_session_minutes(df: pd.DataFrame, trading_date) -> tuple[pd.DataFrame, object]:
    expected_minutes = get_expected_regular_session_minutes(trading_date)
    if expected_minutes.empty or df.empty:
        return df.iloc[0:0].copy(), compare_session_minutes(trading_date, [])

    quality_timestamps = None
    today_ny = datetime.now(tz=NY_TZ).date()
    if trading_date == today_ny:
        current_minute = pd.Timestamp.now(tz=NY_TZ).floor("min")
        future_minutes = expected_minutes[
            expected_minutes > current_minute
        ]
        quality_timestamps = future_minutes
        expected_minutes = expected_minutes[
            expected_minutes <= current_minute
        ]

    expected_set = set(expected_minutes)
    scheduled_df = df[
        df["timestamp_ny"].dt.floor("min").isin(expected_set)
    ].copy()

    timestamps_for_quality = scheduled_df["timestamp_ny"]
    if quality_timestamps is not None:
        timestamps_for_quality = pd.DatetimeIndex(
            pd.to_datetime(
                list(timestamps_for_quality) + list(quality_timestamps),
                utc=True,
            )
        ).tz_convert(NY_TZ)

    report = compare_session_minutes(
        trading_date,
        timestamps_for_quality,
    )
    return scheduled_df.sort_values("timestamp_ny"), report


def enrich_decision_with_market_context(args, trading_date, decision, session_df: pd.DataFrame) -> None:
    if decision is None or decision.plan is None or decision.signal is None:
        return

    benchmark_symbol = "SPY" if args.symbol.upper() == "QQQ" else "QQQ"
    try:
        if args.source == "live":
            benchmark_df = load_live_session(benchmark_symbol, trading_date)
        else:
            benchmark_df = load_db_session(benchmark_symbol, trading_date)
        benchmark_df, _ = filter_expected_session_minutes(
            benchmark_df,
            trading_date,
        )
    except Exception:
        return

    context = build_relative_strength_context(
        symbol=args.symbol,
        position_side=decision.plan.position_side,
        symbol_session_df=session_df,
        benchmark_session_df=benchmark_df,
        benchmark_symbol=benchmark_symbol,
    )
    if context:
        decision.signal["relative_strength"] = context


def run_once(args, trading_date, config, dry_run: bool, strategy_version: str) -> tuple[str, object | None]:
    dry_run = not args.submit

    trading_client = None
    account = None
    if args.equity is None or args.submit:
        trading_client = create_alpaca_paper_trading_client()
        account = get_account_snapshot(trading_client)
        account_equity = account.equity
    else:
        account_equity = args.equity

    if account is not None and account.trading_blocked:
        print("REJECTED: ACCOUNT_TRADING_BLOCKED")
        return "ACCOUNT_TRADING_BLOCKED", None

    if args.source == "live":
        session_df = load_live_session(args.symbol, trading_date)
    else:
        session_df = load_db_session(args.symbol, trading_date)

    session_df, quality = filter_expected_session_minutes(
        session_df,
        trading_date,
    )

    if quality.quality not in {
        SessionQuality.COMPLETE,
        SessionQuality.SCHEDULED_EARLY_CLOSE,
        SessionQuality.DATA_GAP,
    } or not quality.opening_range_complete:
        print(f"REJECTED: SESSION_NOT_READY quality={quality.quality.value}")
        return "SESSION_NOT_READY", None

    if strategy_version == ORB_BODY_STRATEGY_VERSION:
        decision = build_retest_body_paper_trade_decision(
            session_df=session_df,
            symbol=args.symbol,
            account_equity=account_equity,
            config=config,
            realized_daily_pnl=args.realized_daily_pnl,
        )
    elif strategy_version == HTF_BREAKOUT_STRATEGY_VERSION:
        if args.source != "live":
            return "HTF_REQUIRES_LIVE_SOURCE", None
        daily_df = load_live_daily(args.symbol, trading_date)
        decision = build_htf_breakout_paper_trade_decision(
            daily_df=daily_df,
            session_df=session_df,
            symbol=args.symbol,
            account_equity=account_equity,
            paper_config=config,
            htf_config=HTFBreakoutConfig(),
            realized_daily_pnl=args.realized_daily_pnl,
        )
    elif strategy_version == VWAP_PULLBACK_STRATEGY_VERSION:
        decision = build_vwap_pullback_paper_trade_decision(
            session_df=session_df,
            symbol=args.symbol,
            account_equity=account_equity,
            paper_config=config,
            vwap_config=VWAPPullbackConfig(),
            realized_daily_pnl=args.realized_daily_pnl,
        )
    elif strategy_version == VWAP_EMA_CROSS_STRATEGY_VERSION:
        decision = build_vwap_ema9_cross_paper_trade_decision(
            session_df=session_df,
            symbol=args.symbol,
            account_equity=account_equity,
            paper_config=config,
            cross_config=VWAPEMACrossConfig(),
            realized_daily_pnl=args.realized_daily_pnl,
        )
    else:
        raise ValueError(f"Unknown strategy lane: {strategy_version}")

    print(f"Symbol: {args.symbol.upper()}")
    print(f"Strategy: {strategy_version}")
    print(f"Date: {trading_date}")
    print(f"Source: {args.source}")
    print(f"Mode: {'DRY_RUN' if dry_run else 'SUBMIT'}")
    print(f"Decision: {decision.reason}")

    if not decision.approved or decision.plan is None:
        append_audit_record(
            decision_to_audit_record(
                decision=decision,
                symbol=args.symbol,
                trading_date=trading_date,
                source=args.source,
                dry_run=dry_run,
            )
        )
        return decision.reason, decision

    enrich_decision_with_market_context(
        args=args,
        trading_date=trading_date,
        decision=decision,
        session_df=session_df,
    )

    if args.submit:
        if trading_client is None:
            trading_client = create_alpaca_paper_trading_client()
        if not symbol_is_clear_to_trade(trading_client, args.symbol):
            print("REJECTED: SYMBOL_HAS_OPEN_POSITION_OR_ORDER")
            append_audit_record(
                decision_to_audit_record(
                    decision=decision,
                    symbol=args.symbol,
                    trading_date=trading_date,
                    source=args.source,
                    dry_run=dry_run,
                )
            )
            return "SYMBOL_HAS_OPEN_POSITION_OR_ORDER", decision

    result = submit_bracket_order(
        trading_client=trading_client,
        plan=decision.plan,
        dry_run=dry_run,
    )
    append_audit_record(
        decision_to_audit_record(
            decision=decision,
            symbol=args.symbol,
            trading_date=trading_date,
            source=args.source,
            dry_run=dry_run,
            order_result=result,
        )
    )

    payload = result.payload
    print(f"Submitted: {result.submitted}")
    print(f"Side: {payload.side}")
    print(f"Quantity: {payload.qty}")
    print(f"Stop: {payload.stop_loss_stop_price:.2f}")
    print(f"Target: {payload.take_profit_limit_price:.2f}")
    print(f"Planned risk: ${decision.plan.planned_risk_dollars:.2f}")
    print(f"Notional: ${decision.plan.notional_dollars:.2f}")
    return "APPROVED", decision


def main():
    args = parse_args()
    trading_date = trading_date_from_args(args.date)
    dry_run = not args.submit
    symbols = parse_symbols(args.symbol, args.symbols)
    strategies = parse_strategies(args.strategies)
    htf_symbols = (
        parse_symbols(args.symbol, args.htf_symbols)
        if args.htf_symbols is not None
        else None
    )

    if args.submit and (len(symbols) > 1 or len(strategies) > 1):
        print("REJECTED: SUBMIT_REQUIRES_SINGLE_SYMBOL_AND_STRATEGY")
        return

    if args.submit:
        today_ny = datetime.now(tz=NY_TZ).date()
        if args.source != "live" or trading_date != today_ny:
            print("REJECTED: SUBMIT_REQUIRES_LIVE_SOURCE_AND_TODAY")
            return

    if not is_trading_session(trading_date):
        print("REJECTED: NO_EXCHANGE_SESSION")
        if args.alert_status:
            send_mobile_alert(
                args,
                format_watch_skipped_message(
                    symbols=symbols,
                    htf_symbols=htf_symbols or symbols,
                    strategies=strategies,
                    reason="NO_EXCHANGE_SESSION",
                    args=args,
                    trading_date=trading_date,
                ),
            )
        return

    if args.watch and datetime.now(tz=NY_TZ).time() > parse_ny_clock(args.cutoff):
        print("REJECTED: AFTER_CUTOFF")
        if args.alert_status:
            send_mobile_alert(
                args,
                format_watch_skipped_message(
                    symbols=symbols,
                    htf_symbols=htf_symbols or symbols,
                    strategies=strategies,
                    reason="AFTER_CUTOFF",
                    args=args,
                ),
            )
        return

    config = PaperTradingConfig(
        risk_fraction=args.risk_fraction,
        max_trade_risk_fraction=args.risk_fraction,
        max_notional_fraction=args.max_notional_fraction,
        max_daily_loss_fraction=args.max_daily_loss_fraction,
        allow_short_selling=not args.disable_shorts,
    )
    macro_risk_context = build_macro_risk_context(
        trading_date=trading_date,
        start=args.macro_news_start,
        end=args.macro_news_end,
    )

    if not args.watch:
        reason, decision = run_once(
            args_for_symbol(args, symbols[0]),
            trading_date,
            config,
            dry_run,
            strategies[0],
        )
        if args.notify and reason == "APPROVED":
            notify(
                "Paper Trading Signal",
                f"{args.symbol.upper()} approved in {'dry-run' if dry_run else 'submit'} mode.",
            )
        if args.telegram and reason == "APPROVED" and decision is not None:
            send_mobile_alert(
                args,
                format_approved_message(
                    args,
                    decision,
                    dry_run,
                    macro_risk_context=macro_risk_context,
                ),
            )
        return

    cutoff = parse_ny_clock(args.cutoff)
    approved_lanes = set()
    active_lanes = build_strategy_lanes(
        symbols=symbols,
        strategies=strategies,
        htf_symbols=htf_symbols,
    )
    print(
        f"WATCH MODE: {', '.join(symbols)} every {args.poll_seconds}s "
        f"until {args.cutoff} NY; mode={'DRY_RUN' if dry_run else 'SUBMIT'}; "
        f"strategies={', '.join(strategies)}"
    )
    if args.alert_status:
        send_mobile_alert(
            args,
            format_watch_started_message(
                symbols=symbols,
                htf_symbols=htf_symbols or symbols,
                strategies=strategies,
                args=args,
                dry_run=dry_run,
            ),
        )

    stopped_reasons: dict[str, list[tuple[str, str]]] = {}
    watched_trades: list[WatchedTrade] = []
    trade_outcomes: list[TradeOutcome] = []
    while (
        datetime.now(tz=NY_TZ).time() <= cutoff
        and (active_lanes or any(not trade.closed for trade in watched_trades))
    ):
        for symbol, strategy in list(active_lanes):
            symbol_args = args_for_symbol(args, symbol)
            reason, decision = run_once(
                symbol_args,
                trading_date,
                config,
                dry_run,
                strategy,
            )

            if reason == "APPROVED":
                quality = "B"
                if decision is not None and decision.signal is not None:
                    quality, _ = signal_quality_from_context(decision.signal)
                if quality == "C":
                    stopped_reasons.setdefault("LOW_QUALITY_C", []).append((symbol, strategy))
                    active_lanes.remove((symbol, strategy))
                    continue

                notify(
                    "Paper Trading Signal",
                    f"{symbol} approved in {'dry-run' if dry_run else 'submit'} mode.",
                    enabled=args.notify,
                )
                if decision is not None:
                    send_mobile_alert(
                        symbol_args,
                        format_approved_message(
                            symbol_args,
                            decision,
                            dry_run,
                            macro_risk_context=macro_risk_context,
                        ),
                    )
                    watched_trades.append(
                        WatchedTrade(
                            symbol=symbol,
                            strategy=strategy,
                            plan=decision.plan,
                            signal_time=_tracking_start_time(decision),
                        )
                    )
                approved_lanes.add((symbol, strategy))
                active_lanes.remove((symbol, strategy))
                continue

            non_terminal_reasons = {
                "SESSION_NOT_READY",
                "NO_VALID_SIGNAL",
                "INSUFFICIENT_BARS",
                "NO_VWAP_PULLBACK_CONFIRMATION",
                "NO_FOLLOW_THROUGH_CANDLE",
                "NO_FOLLOW_THROUGH_CONFIRMATION",
                "NO_VWAP_EMA9_CROSS_CONFIRMATION",
                "NO_ENTRY_CANDLE",
            }
            if reason not in non_terminal_reasons:
                notify(
                    "Paper Trading Watch",
                    f"{symbol} stopped: {reason}",
                    enabled=args.notify,
                )
                stopped_reasons.setdefault(reason, []).append((symbol, strategy))
                active_lanes.remove((symbol, strategy))

        for trade in list(watched_trades):
            if trade.closed:
                continue
            symbol_args = args_for_symbol(args, trade.symbol)
            if args.source == "live":
                tracking_df = load_live_session(trade.symbol, trading_date)
            else:
                tracking_df = load_db_session(trade.symbol, trading_date)
            tracking_df, _ = filter_expected_session_minutes(
                tracking_df,
                trading_date,
            )
            update_message, _terminal, trade_outcome = evaluate_watched_trade(
                trade,
                tracking_df,
            )
            if update_message is not None:
                send_mobile_alert(symbol_args, update_message)
            if trade_outcome is not None:
                trade_outcomes.append(trade_outcome)

        sleep_time.sleep(args.poll_seconds)

    for trade in list(watched_trades):
        if trade.closed:
            continue
        symbol_args = args_for_symbol(args, trade.symbol)
        if args.source == "live":
            tracking_df = load_live_session(trade.symbol, trading_date)
        else:
            tracking_df = load_db_session(trade.symbol, trading_date)
        tracking_df, _ = filter_expected_session_minutes(
            tracking_df,
            trading_date,
        )
        result_message, trade_outcome = format_watched_trade_cutoff_result(
            trade,
            tracking_df,
            args.cutoff,
        )
        if result_message is not None:
            send_mobile_alert(symbol_args, result_message)
        if trade_outcome is not None:
            trade_outcomes.append(trade_outcome)

    if args.alert_status:
        if active_lanes:
            print("WATCH COMPLETE: no approved signal before cutoff.")
        send_mobile_alert(
            args,
            format_watch_recap_message(
                approved_lanes=approved_lanes,
                stopped_reasons=stopped_reasons,
                active_lanes=active_lanes,
                args=args,
                trade_outcomes=trade_outcomes,
            ),
        )
    elif active_lanes:
        print("WATCH COMPLETE: no approved signal before cutoff.")

    if approved_lanes:
        approved = [
            f"{symbol}:{strategy}"
            for symbol, strategy in sorted(approved_lanes)
        ]
        print(f"APPROVED LANES: {', '.join(approved)}")


if __name__ == "__main__":
    main()
