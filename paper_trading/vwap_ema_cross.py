from dataclasses import dataclass
from typing import Optional

import pandas as pd

from paper_trading.config import PaperTradingConfig
from paper_trading.order_plan import BracketOrderPlan, build_bracket_order_plan
from paper_trading.risk import RiskCheckResult, check_order_risk


STRATEGY_VERSION = "VWAP_EMA9_CROSS_2R"


@dataclass(frozen=True)
class VWAPEMACrossConfig:
    timeframe: str = "3min"
    ema_length: int = 9
    earliest_signal_minute: str = "09:45"
    stop_lookback_bars: int = 3
    stop_buffer_percent: float = 0.001
    target_r: float = 2.0
    max_entry_extension_percent: float = 0.004


@dataclass(frozen=True)
class VWAPEMACrossDecision:
    approved: bool
    reason: str
    plan: Optional[BracketOrderPlan] = None
    signal: Optional[dict] = None
    risk_check: Optional[RiskCheckResult] = None


def build_three_minute_bars(session_df: pd.DataFrame, timeframe: str = "3min") -> pd.DataFrame:
    if session_df.empty:
        return session_df.copy()
    if "timestamp_ny" not in session_df.columns:
        raise ValueError("session_df must include timestamp_ny.")

    df = session_df.copy().sort_values("timestamp_ny")
    indexed = df.set_index(pd.DatetimeIndex(df["timestamp_ny"]))
    bars = indexed.resample(
        timeframe,
        label="left",
        closed="left",
        origin="start_day",
        offset="9h30min",
    ).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    bars = bars.dropna(subset=["open", "high", "low", "close"]).reset_index()
    bars = bars.rename(columns={"index": "timestamp_ny"})
    return bars


def add_vwap_ema9(bars_df: pd.DataFrame, ema_length: int = 9) -> pd.DataFrame:
    if bars_df.empty:
        return bars_df.copy()

    df = bars_df.copy().sort_values("timestamp_ny").reset_index(drop=True)
    ohlc4 = (
        df["open"].astype(float)
        + df["high"].astype(float)
        + df["low"].astype(float)
        + df["close"].astype(float)
    ) / 4
    volume = df["volume"].astype(float).clip(lower=0)
    cumulative_volume = volume.cumsum()
    df["session_vwap"] = (ohlc4 * volume).cumsum() / cumulative_volume
    df.loc[cumulative_volume == 0, "session_vwap"] = ohlc4
    df["ema9"] = df["close"].astype(float).ewm(
        span=ema_length,
        adjust=False,
    ).mean()
    return df


def _is_after_earliest_signal(timestamp, earliest_signal_minute: str) -> bool:
    return pd.Timestamp(timestamp).strftime("%H:%M") >= earliest_signal_minute


def find_vwap_ema9_cross_signal(
    session_df: pd.DataFrame,
    config: VWAPEMACrossConfig = VWAPEMACrossConfig(),
) -> tuple[Optional[dict], str]:
    if session_df.empty:
        return None, "NO_SESSION_DATA"
    if "timestamp_ny" not in session_df.columns:
        raise ValueError("session_df must include timestamp_ny.")

    bars = add_vwap_ema9(
        build_three_minute_bars(session_df, timeframe=config.timeframe),
        ema_length=config.ema_length,
    )
    if len(bars) < config.ema_length + 2:
        return None, "INSUFFICIENT_BARS"

    for index in range(1, len(bars) - 1):
        candle = bars.iloc[index]
        previous = bars.iloc[index - 1]
        if not _is_after_earliest_signal(
            candle["timestamp_ny"],
            config.earliest_signal_minute,
        ):
            continue

        previous_ema = float(previous["ema9"])
        previous_vwap = float(previous["session_vwap"])
        current_ema = float(candle["ema9"])
        current_vwap = float(candle["session_vwap"])
        close_price = float(candle["close"])
        direction = None

        if (
            previous_ema <= previous_vwap
            and current_ema > current_vwap
            and close_price > current_ema
            and close_price > current_vwap
        ):
            direction = "LONG"
        elif (
            previous_ema >= previous_vwap
            and current_ema < current_vwap
            and close_price < current_ema
            and close_price < current_vwap
        ):
            direction = "SHORT"

        if direction is None:
            continue

        entry = bars.iloc[index + 1]
        stop_window = bars.iloc[max(0, index - config.stop_lookback_bars + 1): index + 1]
        entry_price = float(entry["open"])
        if direction == "LONG":
            reference_price = max(current_ema, current_vwap)
            extension = (entry_price - reference_price) / reference_price
        else:
            reference_price = min(current_ema, current_vwap)
            extension = (reference_price - entry_price) / reference_price

        if extension > config.max_entry_extension_percent:
            return None, "VWAP_EMA9_ENTRY_TOO_EXTENDED"

        return {
            "strategy_version": STRATEGY_VERSION,
            "direction": direction,
            "timeframe": config.timeframe,
            "vwap": current_vwap,
            "ema9": current_ema,
            "entry_extension_percent": extension,
            "cross_timestamp": candle["timestamp_ny"],
            "confirmation_timestamp": candle["timestamp_ny"],
            "entry_timestamp": entry["timestamp_ny"],
            "entry_price": entry_price,
            "swing_low": float(stop_window["low"].astype(float).min()),
            "swing_high": float(stop_window["high"].astype(float).max()),
        }, "APPROVED"

    return None, "NO_VWAP_EMA9_CROSS_CONFIRMATION"


def build_vwap_ema9_cross_paper_trade_decision(
    session_df: pd.DataFrame,
    symbol: str,
    account_equity: float,
    paper_config: PaperTradingConfig,
    cross_config: VWAPEMACrossConfig = VWAPEMACrossConfig(),
    realized_daily_pnl: float = 0.0,
) -> VWAPEMACrossDecision:
    signal, reason = find_vwap_ema9_cross_signal(
        session_df=session_df,
        config=cross_config,
    )
    if signal is None:
        return VWAPEMACrossDecision(False, reason)

    entry_price = float(signal["entry_price"])
    if signal["direction"] == "LONG":
        stop_price = float(signal["swing_low"]) * (1 - cross_config.stop_buffer_percent)
        risk = entry_price - stop_price
        target_price = entry_price + risk * cross_config.target_r
    else:
        stop_price = float(signal["swing_high"]) * (1 + cross_config.stop_buffer_percent)
        risk = stop_price - entry_price
        target_price = entry_price - risk * cross_config.target_r

    if risk <= 0:
        return VWAPEMACrossDecision(False, "INVALID_RISK", signal=signal)

    strategy_config = PaperTradingConfig(
        strategy_version=STRATEGY_VERSION,
        target_r=cross_config.target_r,
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

    return VWAPEMACrossDecision(
        approved=risk_check.approved,
        reason=risk_check.reason,
        plan=plan,
        signal=signal,
        risk_check=risk_check,
    )
