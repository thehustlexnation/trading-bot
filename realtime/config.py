from dataclasses import asdict, dataclass
from datetime import datetime, time
import hashlib
import json
import math
import os
import re


FOCUSED_RESEARCH_SYMBOLS = "NVDA,AAPL,META,GOOGL,TQQQ,AMD,AVGO,SNDK,INTC,TSLA"
INTRADAY_SYMBOLS = FOCUSED_RESEARCH_SYMBOLS
HTF_SYMBOLS = FOCUSED_RESEARCH_SYMBOLS
STRATEGIES = (
    "ORB_RETEST_RECLAIM_BODY_2R", "HTF_BREAKOUT_RETEST_2R", "VWAP_EMA9_CROSS_2R",
)
GROUPS = {
    **dict.fromkeys(("NVDA", "AMD", "AVGO", "SNDK", "INTC"), "chips_storage"),
    **dict.fromkeys(("AAPL", "MSFT", "ORCL", "CRM", "ADBE"), "software_hardware"),
    **dict.fromkeys(("GOOGL", "META", "NFLX"), "platforms_media"),
    **dict.fromkeys(("AMZN", "TSLA"), "consumer_discretionary"),
    **dict.fromkeys(("JPM", "V", "MA"), "financials_payments"),
    "COST": "consumer_staples", "QQQ": "broad_market", "TQQQ": "broad_market",
}


def symbols(value):
    result = tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))
    if not result or any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", item) for item in result):
        raise ValueError("Invalid or empty symbol list.")
    return result


def clock(value):
    return datetime.strptime(value, "%H:%M").time()


@dataclass(frozen=True)
class RuntimeConfig:
    intraday: tuple[str, ...] = symbols(INTRADAY_SYMBOLS)
    htf: tuple[str, ...] = symbols(HTF_SYMBOLS)
    strategies: tuple[str, ...] = STRATEGIES
    feed: str = "iex"
    preparation_start: str = "07:45"
    watch_start: str = "09:25"
    last_entry: str = "10:00"
    cutoff: str = "10:15"
    equity: float = 10_000
    risk_fraction: float = 0.0025
    daily_loss_fraction: float = 0.01
    max_notional_fraction: float = 0.25
    portfolio_notional_fraction: float = 1.0
    group_notional_fraction: float = 0.5
    max_quote_age_seconds: float = 10
    max_signal_age_seconds: float = 90
    max_spread_bps: float = 20
    max_entry_drift_bps: float = 25
    history_days: int = 35
    policy_version: str = "STREAM_SHADOW_V1"

    def __post_init__(self):
        if self.feed not in {"iex", "sip"}:
            raise ValueError("Real-time mode requires an explicitly selected live iex or sip feed.")
        if not self.strategies or set(self.strategies) - set(STRATEGIES):
            raise ValueError("Only the reviewed ORB, HTF and EMA9 lanes are enabled in this release.")
        if not (clock(self.preparation_start) < clock(self.watch_start) <= time(9, 30)
                < clock(self.last_entry) < clock(self.cutoff) <= time(16)):
            raise ValueError("Invalid preparation/watch/entry/cutoff sequence.")
        for field in ("equity", "max_quote_age_seconds", "max_signal_age_seconds",
                      "max_spread_bps", "max_entry_drift_bps"):
            value = getattr(self, field)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be finite and positive.")
        for field in ("risk_fraction", "daily_loss_fraction", "max_notional_fraction",
                      "portfolio_notional_fraction", "group_notional_fraction"):
            value = getattr(self, field)
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"Invalid {field}.")
        if self.risk_fraction > self.daily_loss_fraction or self.history_days < 21:
            raise ValueError("Invalid risk or historical lookback configuration.")
        if self.feed == "iex" and len(self.subscriptions) > 30:
            raise ValueError("Basic IEX runtime is limited to 30 subscribed symbols.")

    @property
    def watchlist(self):
        return tuple(dict.fromkeys(self.intraday + self.htf))

    @property
    def subscriptions(self):
        return tuple(dict.fromkeys(self.watchlist + ("QQQ", "SPY", "SMH")))

    @property
    def lanes(self):
        return tuple((symbol, strategy) for strategy in self.strategies for symbol in (
            self.htf if strategy == "HTF_BREAKOUT_RETEST_2R" else self.intraday
        ))

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:20]

    @classmethod
    def from_env(cls):
        if os.getenv("REALTIME_SUBMIT_PAPER", "false").lower() not in {"false", "0", "no", "off", ""}:
            raise ValueError("PAPER_SUBMISSION_DISABLED: set REALTIME_SUBMIT_PAPER=false.")
        values = {
            "intraday": symbols(os.getenv("REALTIME_SYMBOLS", INTRADAY_SYMBOLS)),
            "htf": symbols(os.getenv("REALTIME_HTF_SYMBOLS", HTF_SYMBOLS)),
            "strategies": tuple(dict.fromkeys(os.getenv("REALTIME_STRATEGIES", ",".join(STRATEGIES)).split(","))),
            "feed": os.getenv("ALPACA_DATA_FEED", "iex").lower(),
        }
        for name, env in (("preparation_start", "REALTIME_PREPARATION_START"), ("watch_start", "REALTIME_WATCH_START"),
                          ("last_entry", "REALTIME_LAST_ENTRY"), ("cutoff", "REALTIME_CUTOFF")):
            if os.getenv(env):
                values[name] = os.environ[env]
        if "watch_start" not in values and os.getenv("REALTIME_START"):
            values["watch_start"] = os.environ["REALTIME_START"]
        for name in ("equity", "risk_fraction", "daily_loss_fraction", "max_notional_fraction",
                     "portfolio_notional_fraction", "group_notional_fraction", "max_quote_age_seconds",
                     "max_signal_age_seconds", "max_spread_bps", "max_entry_drift_bps"):
            if os.getenv("REALTIME_" + name.upper()):
                values[name] = float(os.environ["REALTIME_" + name.upper()])
        values["strategies"] = tuple(item.strip() for item in values["strategies"] if item.strip())
        return cls(**values)
