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
    notify_discord,
    notify_email,
    notify_telegram,
)
from paper_trading.signal_builder import build_retest_body_paper_trade_decision
from paper_trading.vwap_pullback import (
    STRATEGY_VERSION as VWAP_PULLBACK_STRATEGY_VERSION,
    VWAPPullbackConfig,
    build_vwap_pullback_paper_trade_decision,
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
    "JPM,V,MA,NFLX,COST,ORCL,CRM,ADBE"
)


@dataclass
class WatchedTrade:
    symbol: str
    strategy: str
    plan: object
    signal_time: object
    one_r_sent: bool = False
    closed: bool = False


def parse_args():
    parser = argparse.ArgumentParser(
        description="Dry-run or submit one paper ORB retest bracket order."
    )
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols to scan in watch mode.")
    parser.add_argument(
        "--htf-symbols",
        default=None,
        help="Comma-separated symbols for HTF_BREAKOUT_RETEST_2R. Defaults to --symbols when omitted.",
    )
    parser.add_argument(
        "--strategies",
        default=(
            f"{ORB_BODY_STRATEGY_VERSION},"
            f"{HTF_BREAKOUT_STRATEGY_VERSION},"
            f"{VWAP_PULLBACK_STRATEGY_VERSION}"
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
    parser.add_argument("--discord", action="store_true", help="Send Discord notifications using DISCORD_WEBHOOK_URL.")
    parser.add_argument("--email", action="store_true", help="Send email notifications using SMTP_* secrets.")
    parser.add_argument("--alert-status", action="store_true", help="Send start and no-signal completion alerts.")
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


def build_discord_approval_components(args, decision) -> list[dict] | None:
    dry_run_url = build_approval_url(
        args=args,
        decision=decision,
        risk_dollars=100.0,
        submit_paper=False,
    )
    paper_url = build_approval_url(
        args=args,
        decision=decision,
        risk_dollars=100.0,
        submit_paper=True,
    )
    if not dry_run_url and not paper_url:
        return None

    buttons = []
    if dry_run_url:
        buttons.append(
            {
                "type": 2,
                "style": 5,
                "label": "Dry-run $100",
                "url": dry_run_url,
            }
        )
    if paper_url:
        buttons.append(
            {
                "type": 2,
                "style": 5,
                "label": "Approve Paper $100",
                "url": paper_url,
            }
        )

    return [{"type": 1, "components": buttons}]


def send_mobile_alert(args, message: str, components: list[dict] | None = None) -> bool:
    sent = notify_telegram(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        message=message,
        enabled=args.telegram,
    )
    sent = notify_discord(
        webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        message=message,
        components=components,
        enabled=args.discord,
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
    if HTF_BREAKOUT_STRATEGY_VERSION in strategies:
        lines.append(f"- HTF breakout: {_format_symbol_list(htf_symbols)}")
    lines.extend(
        [
            "",
            "I will send trade alerts immediately. No-trade reasons will be grouped "
            "into a recap so Discord stays readable.",
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


def format_approved_message(args, decision, dry_run: bool) -> str:
    plan = decision.plan
    signal = decision.signal or {}
    mode = "DRY RUN" if dry_run else "PAPER SUBMIT"
    action = "BUY / go LONG" if plan.entry_side == "buy" else "SELL SHORT"
    breakout_time = signal.get("breakout_timestamp", "n/a")
    retest_time = signal.get("retest_timestamp", "n/a")
    pullback_time = signal.get("pullback_timestamp", "n/a")
    confirmation_time = signal.get("confirmation_timestamp", "n/a")
    if plan.strategy_version == VWAP_PULLBACK_STRATEGY_VERSION:
        what_happened = "VWAP trend pullback and confirmation detected."
        timing_lines = (
            f"- Pullback: {pullback_time}\n"
            f"- Confirmation: {confirmation_time}\n"
            f"- VWAP reference: {signal.get('vwap', 'n/a')}"
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
    return strategy


def _strategy_short_label(strategy: str) -> str:
    if strategy == ORB_BODY_STRATEGY_VERSION:
        return "ORB"
    if strategy == HTF_BREAKOUT_STRATEGY_VERSION:
        return "HTF"
    if strategy == VWAP_PULLBACK_STRATEGY_VERSION:
        return "VWAP"
    return strategy


def _tracking_start_time(decision) -> object:
    signal = decision.signal or {}
    return (
        signal.get("entry_timestamp")
        or signal.get("confirmation_timestamp")
        or signal.get("retest_timestamp")
        or signal.get("breakout_timestamp")
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


def evaluate_watched_trade(
    trade: WatchedTrade,
    session_df: pd.DataFrame,
) -> tuple[str | None, bool]:
    if trade.closed or session_df.empty:
        return None, False
    if "timestamp_ny" not in session_df.columns:
        raise ValueError("session_df must include timestamp_ny.")

    plan = trade.plan
    df = session_df.sort_values("timestamp_ny")
    if trade.signal_time is not None:
        df = df[df["timestamp_ny"] >= pd.Timestamp(trade.signal_time)]
    if df.empty:
        return None, False

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
            )

    return None, False


def format_watched_trade_cutoff_result(
    trade: WatchedTrade,
    session_df: pd.DataFrame,
    cutoff: str,
) -> str | None:
    if trade.closed or session_df.empty:
        return None
    plan = trade.plan
    df = session_df.sort_values("timestamp_ny")
    if trade.signal_time is not None:
        df = df[df["timestamp_ny"] >= pd.Timestamp(trade.signal_time)]
    if df.empty:
        return None

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
    )


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
                format_approved_message(args, decision, dry_run),
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
                notify(
                    "Paper Trading Signal",
                    f"{symbol} approved in {'dry-run' if dry_run else 'submit'} mode.",
                    enabled=args.notify,
                )
                if decision is not None:
                    send_mobile_alert(
                        symbol_args,
                        format_approved_message(symbol_args, decision, dry_run),
                        components=build_discord_approval_components(
                            symbol_args,
                            decision,
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
            update_message, _terminal = evaluate_watched_trade(
                trade,
                tracking_df,
            )
            if update_message is not None:
                send_mobile_alert(symbol_args, update_message)

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
        result_message = format_watched_trade_cutoff_result(
            trade,
            tracking_df,
            args.cutoff,
        )
        if result_message is not None:
            send_mobile_alert(symbol_args, result_message)

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
