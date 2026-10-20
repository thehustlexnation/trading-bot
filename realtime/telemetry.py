from collections import Counter, deque
import math

import pandas as pd

from realtime.market import aware_timestamp


class RuntimeTelemetry:
    """Bounded recent latency samples with exact counts and maxima per window."""

    def __init__(self, sample_limit=2048):
        self.sample_limit = sample_limit
        self.reset()

    def reset(self):
        self.counts = Counter()
        self.symbols = {}
        self.samples = {}
        self.maxima = {}
        self.queue_high_water = 0

    def observe(self, name, seconds):
        seconds = float(seconds)
        if not math.isfinite(seconds):
            return
        self.counts[name] += 1
        self.samples.setdefault(name, deque(maxlen=self.sample_limit)).append(seconds)
        self.maxima[name] = max(self.maxima.get(name, seconds), seconds)

    def market_event(self, kind, payload, received, processed, queue_size):
        self.queue_high_water = max(self.queue_high_water, queue_size)
        self.observe("queue_wait_seconds", (aware_timestamp(processed) - aware_timestamp(received)).total_seconds())
        stamp = aware_timestamp(payload["timestamp"])
        # Minute bars are timestamped at their start, so measure from completion.
        is_bar = kind in {"bar", "bar_revision"}
        available = stamp + pd.Timedelta(minutes=1) if is_bar else stamp
        self.observe(f"{kind}_receive_seconds", (aware_timestamp(received) - available).total_seconds())
        symbol = str(payload["symbol"]).upper()
        item = self.symbols.setdefault(symbol, {"bars": 0, "quotes": 0})
        item["bars" if is_bar else "quotes"] += 1
        field = f"last_{kind}_timestamp"
        item[field] = max(item.get(field, stamp.isoformat()), stamp.isoformat())

    def snapshot(self, now):
        latencies = {}
        for name, samples in self.samples.items():
            series = pd.Series(samples, dtype=float)
            latencies[name] = {
                "count": self.counts[name], "sample_count": len(samples),
                "p50": float(series.quantile(0.50)), "p95": float(series.quantile(0.95)),
                "p99": float(series.quantile(0.99)), "max": self.maxima[name],
            }
        return {"asof": aware_timestamp(now).isoformat(), "symbols": self.symbols,
                "latencies": latencies, "queue_high_water": self.queue_high_water,
                "percentile_scope": f"last {self.sample_limit} observations per metric in this window"}
