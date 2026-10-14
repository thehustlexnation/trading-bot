from dataclasses import dataclass
from typing import Optional

import pandas as pd

from paper_trading.config import PaperTradingConfig
from paper_trading.order_plan import BracketOrderPlan, build_bracket_order_plan
from paper_trading.risk import RiskCheckResult, check_order_risk
from paper_trading.vwap_ema_cross import add_vwap_ema9, build_three_minute_bars


STRATEGY_VERSION = "POST_NEWS_BREAKOUT_RETEST_2R"


@dataclass(frozen=True)
class PostNewsBreakoutConfig:
    news_window_end: str = "10:30"
    range_bars: int = 5
    timeframe: str = "3min"
    retest_max_bars: int = 5
    ema_length: int = 9
    stop_buffer_percent: float = 0.001
    target_r: float = 2.0


@dataclass(frozen=True)
class PostNewsBreakoutDecision:
    approved: bool
    reason: str
    plan: Optional[BracketOrderPlan] = None
    signal: Optional[dict] = None
    risk_check: Optional[RiskCheckResult] = None


def _after_clock(timestamp, clock: str) -> bool:
    return pd.Timestamp(timestamp).strftime("%H:%M") >= clock


def find_post_news_breakout_signal(
    session_df: pd.DataFrame,
    config: PostNewsBreakoutConfig = PostNewsBreakoutConfig(),
) -> tuple[Optional[dict], str]:
    if session_df.empty:
        return None, "NO_SESSION_DATA"
    bars = build_three_minute_bars(session_df, timeframe=config.timeframe)
    if len(bars) < config.range_bars + 2:
        return None, "INSUFFICIENT_BARS"
    bars = add_vwap_ema9(bars, ema_length=config.ema_length)
    post_news = bars[bars["timestamp_ny"].map(lambda value: _after_clock(value, config.news_window_end))]
    if len(post_news) < config.range_bars + 2:
        return None, "POST_NEWS_RANGE_NOT_READY"
    post_news = post_news.reset_index(drop=True)
    range_df = post_news.iloc[:config.range_bars]
    range_high = float(range_df["high"].max())
    range_low = float(range_df["low"].min())

    for index in range(config.range_bars, len(post_news) - 1):
        candle = post_news.iloc[index]
        close = float(candle["close"])
        vwap = float(candle["session_vwap"])
        ema9 = float(candle["ema9"])
        direction = None
        boundary = None
        if close > range_high and close > vwap and close > ema9:
            direction, boundary = "LONG", range_high
        elif close < range_low and close < vwap and close < ema9:
            direction, boundary = "SHORT", range_low
        if direction is None:
            continue

        end = min(len(post_news), index + 1 + config.retest_max_bars)
        for retest_index in range(index + 1, end):
            retest = post_news.iloc[retest_index]
            retest_close = float(retest["close"])
            if direction == "LONG":
                touched = float(retest["low"]) <= boundary
                confirmed = touched and retest_close > boundary and retest_close > float(retest["session_vwap"])
            else:
                touched = float(retest["high"]) >= boundary
                confirmed = touched and retest_close < boundary and retest_close < float(retest["session_vwap"])
            if not confirmed or retest_index + 1 >= len(post_news):
                continue
            entry = post_news.iloc[retest_index + 1]
            return {
                "strategy_version": STRATEGY_VERSION,
                "direction": direction,
                "timeframe": config.timeframe,
                "news_window_end": config.news_window_end,
                "post_news_range_high": range_high,
                "post_news_range_low": range_low,
                "breakout_timestamp": candle["timestamp_ny"],
                "retest_timestamp": retest["timestamp_ny"],
                "confirmation_timestamp": retest["timestamp_ny"],
                "entry_timestamp": entry["timestamp_ny"],
                "entry_price": float(entry["open"]),
                "vwap": float(retest["session_vwap"]),
                "ema9": float(retest["ema9"]),
                "swing_low": float(post_news.iloc[index:retest_index + 1]["low"].min()),
                "swing_high": float(post_news.iloc[index:retest_index + 1]["high"].max()),
            }, "APPROVED"
    return None, "NO_POST_NEWS_BREAKOUT_RETEST"


def build_post_news_breakout_paper_trade_decision(
    session_df: pd.DataFrame,
    symbol: str,
    account_equity: float,
    paper_config: PaperTradingConfig,
    news_config: PostNewsBreakoutConfig = PostNewsBreakoutConfig(),
    realized_daily_pnl: float = 0.0,
) -> PostNewsBreakoutDecision:
    signal, reason = find_post_news_breakout_signal(session_df, news_config)
    if signal is None:
        return PostNewsBreakoutDecision(False, reason)
    entry = float(signal["entry_price"])
    if signal["direction"] == "LONG":
        stop = float(signal["swing_low"]) * (1 - news_config.stop_buffer_percent)
        risk = entry - stop
        target = entry + risk * news_config.target_r
    else:
        stop = float(signal["swing_high"]) * (1 + news_config.stop_buffer_percent)
        risk = stop - entry
        target = entry - risk * news_config.target_r
    if risk <= 0:
        return PostNewsBreakoutDecision(False, "INVALID_RISK", signal=signal)
    plan = build_bracket_order_plan(
        symbol=symbol,
        strategy_version=STRATEGY_VERSION,
        direction=signal["direction"],
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        account_equity=account_equity,
        risk_fraction=paper_config.risk_fraction,
        max_notional_fraction=paper_config.max_notional_fraction,
    )
    limits = PaperTradingConfig(
        strategy_version=STRATEGY_VERSION,
        target_r=news_config.target_r,
        risk_fraction=paper_config.risk_fraction,
        max_trade_risk_fraction=paper_config.max_trade_risk_fraction,
        max_notional_fraction=paper_config.max_notional_fraction,
        max_daily_loss_fraction=paper_config.max_daily_loss_fraction,
        allow_short_selling=paper_config.allow_short_selling,
    )
    risk_check = check_order_risk(
        plan=plan,
        account_equity=account_equity,
        limits=limits.risk_limits(),
        realized_daily_pnl=realized_daily_pnl,
    )
    return PostNewsBreakoutDecision(
        approved=risk_check.approved,
        reason=risk_check.reason,
        plan=plan,
        signal=signal,
        risk_check=risk_check,
    )
