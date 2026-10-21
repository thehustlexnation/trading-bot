from datetime import datetime

import pandas as pd

from paper_trading.vwap_ema_cross import STRATEGY_VERSION, VWAPEMACrossConfig
from realtime.config import clock


def strategy_readiness(config):
    """Expose schedule conflicts without changing historical detector parameters."""
    result = {}
    if STRATEGY_VERSION in config.strategies:
        cross = VWAPEMACrossConfig()
        # The detector requires ema_length + 2 bars, including the entry reference.
        start = datetime(2000, 1, 1, 9, 30)
        earliest = start + (cross.ema_length + 1) * pd.Timedelta(cross.timeframe)
        earliest = max(earliest, datetime.combine(start.date(), clock(cross.earliest_signal_minute))
                       + pd.Timedelta(cross.timeframe))
        eligible = earliest.time() < clock(config.last_entry)
        result[STRATEGY_VERSION] = {
            "status": "ENTRY_WINDOW_AVAILABLE" if eligible else "NO_ENTRY_WINDOW",
            "earliest_readiness_ny": earliest.strftime("%H:%M"),
            "last_entry_ny": config.last_entry,
            "reason": "Completed-bar warmup must finish strictly before the entry cutoff.",
        }
    return result


def preparation_phase(now, config):
    local = now.tz_convert("America/New_York")
    if local.time() >= clock("09:35"):
        return "opening"
    if local.time() >= clock(config.watch_start):
        return "watch"
    return "initial"
