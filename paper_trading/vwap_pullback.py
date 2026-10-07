from dataclasses import dataclass
from typing import Optional

import pandas as pd

from paper_trading.config import PaperTradingConfig
from paper_trading.order_plan import BracketOrderPlan, build_bracket_order_plan
from paper_trading.risk import RiskCheckResult, check_order_risk


STRATEGY_VERSION = "VWAP_TREND_PULLBACK_2R"


@dataclass(frozen=True)
class VWAPPullbackConfig:
    earliest_signal_minute: str = "09:40"
    trend_lookback_bars: int = 5
    min_bars_on_trend_side: int = 3
    vwap_tolerance_percent: float = 0.0015
    stop_buffer_percent: float = 0.001
    target_r: float = 2.0
    require_follow_through: bool = True


@dataclass(frozen=True)
class VWAPTradeDecision:
    approved: bool
    reason: str
    plan: Optional[BracketOrderPlan] = None
    signal: Optional[dict] = None
    risk_check: Optional[RiskCheckResult] = None


def add_session_vwap(session_df: pd.DataFrame) -> pd.DataFrame:
    if session_df.empty:
        return session_df.copy()

    df = session_df.copy().sort_values("timestamp_ny").reset_index(drop=True)
    typical_price = (
        df["high"].astype(float)
        + df["low"].astype(float)
        + df["close"].astype(float)
    ) / 3
    volume = df["volume"].astype(float).clip(lower=0)
    cumulative_volume = volume.cumsum()
    df["session_vwap"] = (typical_price * volume).cumsum() / cumulative_volume
    df.loc[cumulative_volume == 0, "session_vwap"] = typical_price
    return df


def _is_after_earliest_signal(timestamp, earliest_signal_minute: str) -> bool:
    clock = pd.Timestamp(timestamp).strftime("%H:%M")
    return clock >= earliest_signal_minute


def find_vwap_trend_pullback_signal(
    session_df: pd.DataFrame,
    config: VWAPPullbackConfig = VWAPPullbackConfig(),
) -> tuple[Optional[dict], str]:
    if session_df.empty:
        return None, "NO_SESSION_DATA"
    if "timestamp_ny" not in session_df.columns:
        raise ValueError("session_df must include timestamp_ny.")

    df = add_session_vwap(session_df)
    if len(df) < config.trend_lookback_bars + 2:
        return None, "INSUFFICIENT_BARS"

    tolerance = config.vwap_tolerance_percent
    pullback_index = None
    direction = None

    for index in range(config.trend_lookback_bars, len(df) - 1):
        candle = df.iloc[index]
        if not _is_after_earliest_signal(
            candle["timestamp_ny"],
            config.earliest_signal_minute,
        ):
            continue

        trend_window = df.iloc[index - config.trend_lookback_bars:index]
        closes = trend_window["close"].astype(float)
        vwaps = trend_window["session_vwap"].astype(float)
        above_count = int((closes > vwaps).sum())
        below_count = int((closes < vwaps).sum())

        vwap = float(candle["session_vwap"])
        lower_band = vwap * (1 - tolerance)
        upper_band = vwap * (1 + tolerance)
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])

        if (
            above_count >= config.min_bars_on_trend_side
            and low <= upper_band
            and close_price > vwap
            and close_price > open_price
        ):
            pullback_index = index
            direction = "LONG"
            break

        if (
            below_count >= config.min_bars_on_trend_side
            and high >= lower_band
            and close_price < vwap
            and close_price < open_price
        ):
            pullback_index = index
            direction = "SHORT"
            break

    if pullback_index is None or direction is None:
        return None, "NO_VWAP_PULLBACK_CONFIRMATION"

    pullback = df.iloc[pullback_index]
    confirmation_index = pullback_index
    if config.require_follow_through:
        if pullback_index + 1 >= len(df):
            return None, "NO_FOLLOW_THROUGH_CANDLE"

        follow_through = df.iloc[pullback_index + 1]
        follow_open = float(follow_through["open"])
        follow_close = float(follow_through["close"])
        follow_vwap = float(follow_through["session_vwap"])
        pullback_close = float(pullback["close"])

        if direction == "LONG":
            has_follow_through = (
                follow_close > follow_vwap
                and follow_close > follow_open
                and follow_close > pullback_close
            )
        else:
            has_follow_through = (
                follow_close < follow_vwap
                and follow_close < follow_open
                and follow_close < pullback_close
            )
        if not has_follow_through:
            return None, "NO_FOLLOW_THROUGH_CONFIRMATION"
        confirmation_index = pullback_index + 1

    if confirmation_index + 1 >= len(df):
        return None, "NO_ENTRY_CANDLE"

    confirmation = df.iloc[confirmation_index]
    entry = df.iloc[confirmation_index + 1]
    entry_price = float(entry["open"])
    vwap = float(pullback["session_vwap"])

    return {
        "strategy_version": STRATEGY_VERSION,
        "direction": direction,
        "vwap": vwap,
        "pullback_timestamp": pullback["timestamp_ny"],
        "confirmation_timestamp": confirmation["timestamp_ny"],
        "entry_timestamp": entry["timestamp_ny"],
        "entry_price": entry_price,
        "pullback_low": float(pullback["low"]),
        "pullback_high": float(pullback["high"]),
    }, "APPROVED"


def build_vwap_pullback_paper_trade_decision(
    session_df: pd.DataFrame,
    symbol: str,
    account_equity: float,
    paper_config: PaperTradingConfig,
    vwap_config: VWAPPullbackConfig = VWAPPullbackConfig(),
    realized_daily_pnl: float = 0.0,
) -> VWAPTradeDecision:
    signal, reason = find_vwap_trend_pullback_signal(
        session_df=session_df,
        config=vwap_config,
    )
    if signal is None:
        return VWAPTradeDecision(False, reason)

    entry_price = float(signal["entry_price"])
    if signal["direction"] == "LONG":
        stop_price = float(signal["pullback_low"]) * (1 - vwap_config.stop_buffer_percent)
        risk = entry_price - stop_price
        target_price = entry_price + risk * vwap_config.target_r
    else:
        stop_price = float(signal["pullback_high"]) * (1 + vwap_config.stop_buffer_percent)
        risk = stop_price - entry_price
        target_price = entry_price - risk * vwap_config.target_r

    if risk <= 0:
        return VWAPTradeDecision(False, "INVALID_RISK", signal=signal)

    strategy_config = PaperTradingConfig(
        strategy_version=STRATEGY_VERSION,
        target_r=vwap_config.target_r,
        risk_fraction=paper_config.risk_fraction,
        max_trade_risk_fraction=paper_config.max_trade_risk_fraction,
        max_notional_fraction=paper_config.max_notional_fraction,
        max_daily_loss_fraction=paper_config.max_daily_loss_fraction,
        allow_short_selling=paper_config.allow_short_selling,
    )
    plan = build_bracket_order_plan(
        symbol=symbol,
        strategy_version=STRATEGY_VERSION,
        direction=signal["direction"],
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        account_equity=account_equity,
        risk_fraction=strategy_config.risk_fraction,
        max_notional_fraction=strategy_config.max_notional_fraction,
    )
    risk_check = check_order_risk(
        plan=plan,
        account_equity=account_equity,
        limits=strategy_config.risk_limits(),
        realized_daily_pnl=realized_daily_pnl,
    )

    return VWAPTradeDecision(
        approved=risk_check.approved,
        reason=risk_check.reason,
        plan=plan,
        signal=signal,
        risk_check=risk_check,
    )
