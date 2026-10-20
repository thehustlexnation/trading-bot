from dataclasses import dataclass
from typing import Optional

import pandas as pd

from paper_trading.config import PaperTradingConfig
from paper_trading.order_plan import BracketOrderPlan, build_bracket_order_plan
from paper_trading.risk import RiskCheckResult, check_order_risk
from paper_trading.vwap_ema_cross import add_vwap_ema9
from strategy.orb import calculate_opening_range


STRATEGY_VERSION = "FAILED_BREAKDOWN_REVERSAL_2R"


@dataclass(frozen=True)
class FailedBreakdownReversalConfig:
    confirmation_max_bars: int = 8
    ema_length: int = 9
    stop_buffer_percent: float = 0.001
    target_r: float = 2.0


@dataclass(frozen=True)
class FailedBreakdownReversalDecision:
    approved: bool
    reason: str
    plan: Optional[BracketOrderPlan] = None
    signal: Optional[dict] = None
    risk_check: Optional[RiskCheckResult] = None


def find_failed_breakdown_reversal_signal(
    session_df: pd.DataFrame,
    config: FailedBreakdownReversalConfig = FailedBreakdownReversalConfig(),
) -> tuple[Optional[dict], str]:
    if session_df.empty:
        return None, "NO_SESSION_DATA"
    df = session_df.sort_values("timestamp_ny").reset_index(drop=True)
    try:
        opening_range = calculate_opening_range(df, opening_minutes=5)
    except ValueError:
        return None, "OPENING_RANGE_INCOMPLETE"
    df = add_vwap_ema9(df, ema_length=config.ema_length)
    or_low = float(opening_range["or_low"])
    breakout_index = None
    for index in range(len(df)):
        candle = df.iloc[index]
        if candle["timestamp_ny"].time() >= pd.Timestamp("09:35").time() and float(candle["close"]) < or_low:
            breakout_index = index
            break
    if breakout_index is None:
        return None, "NO_BREAKDOWN"

    end = min(len(df) - 1, breakout_index + config.confirmation_max_bars)
    for index in range(breakout_index + 1, end + 1):
        candle = df.iloc[index]
        close = float(candle["close"])
        if close <= or_low or close <= float(candle["session_vwap"]) or close <= float(candle["ema9"]):
            continue
        if index + 1 >= len(df):
            return None, "NO_ENTRY_CANDLE"
        entry = df.iloc[index + 1]
        return {
            "strategy_version": STRATEGY_VERSION,
            "direction": "LONG",
            "breakdown_timestamp": df.iloc[breakout_index]["timestamp_ny"],
            "failure_timestamp": candle["timestamp_ny"],
            "confirmation_timestamp": candle["timestamp_ny"],
            "entry_timestamp": entry["timestamp_ny"],
            "entry_price": float(entry["open"]),
            "or_low": or_low,
            "vwap": float(candle["session_vwap"]),
            "ema9": float(candle["ema9"]),
            "swing_low": float(df.iloc[breakout_index:index + 1]["low"].min()),
        }, "APPROVED"
    return None, "NO_FAILED_BREAKDOWN_CONFIRMATION"


def build_failed_breakdown_reversal_paper_trade_decision(
    session_df: pd.DataFrame,
    symbol: str,
    account_equity: float,
    paper_config: PaperTradingConfig,
    reversal_config: FailedBreakdownReversalConfig = FailedBreakdownReversalConfig(),
    realized_daily_pnl: float = 0.0,
) -> FailedBreakdownReversalDecision:
    signal, reason = find_failed_breakdown_reversal_signal(session_df, reversal_config)
    if signal is None:
        return FailedBreakdownReversalDecision(False, reason)
    entry = float(signal["entry_price"])
    stop = float(signal["swing_low"]) * (1 - reversal_config.stop_buffer_percent)
    risk = entry - stop
    if risk <= 0:
        return FailedBreakdownReversalDecision(False, "INVALID_RISK", signal=signal)
    target = entry + risk * reversal_config.target_r
    plan = build_bracket_order_plan(
        symbol=symbol,
        strategy_version=STRATEGY_VERSION,
        direction="LONG",
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        account_equity=account_equity,
        risk_fraction=paper_config.risk_fraction,
        max_notional_fraction=paper_config.max_notional_fraction,
    )
    limits = PaperTradingConfig(
        strategy_version=STRATEGY_VERSION,
        target_r=reversal_config.target_r,
        risk_fraction=paper_config.risk_fraction,
        max_trade_risk_fraction=paper_config.max_trade_risk_fraction,
        max_notional_fraction=paper_config.max_notional_fraction,
        max_daily_loss_fraction=paper_config.max_daily_loss_fraction,
        allow_short_selling=paper_config.allow_short_selling,
    )
    risk_check = check_order_risk(plan, account_equity, limits.risk_limits(), realized_daily_pnl)
    return FailedBreakdownReversalDecision(risk_check.approved, risk_check.reason, plan, signal, risk_check)
