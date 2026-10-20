from dataclasses import dataclass
from datetime import datetime
import math

import pandas as pd

from strategy.market_calendar import get_expected_regular_session_minutes


NY = "America/New_York"


def aware_timestamp(value):
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("Market timestamps must be timezone-aware.")
    return result.tz_convert("UTC")


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp: pd.Timestamp
    bid: float
    ask: float
    received_at: pd.Timestamp

    @classmethod
    def from_message(cls, message, received_at):
        result = cls(str(message["symbol"]).upper(), aware_timestamp(message["timestamp"]),
            float(message["bid_price"]), float(message["ask_price"]), aware_timestamp(received_at))
        if not all(math.isfinite(value) and value > 0 for value in (result.bid, result.ask)) or result.ask < result.bid:
            raise ValueError("Invalid or crossed quote.")
        return result

    def rejection(self, now, max_age, max_spread_bps):
        age = (aware_timestamp(now) - self.timestamp).total_seconds()
        if age < -1 or age > max_age:
            return "STALE_QUOTE"
        spread = (self.ask - self.bid) / ((self.ask + self.bid) / 2) * 10_000
        return "SPREAD_LIMIT" if spread > max_spread_bps else None


class MarketState:
    def __init__(self, session, feed, journal):
        self.session = session
        self.feed = feed
        self.journal = journal
        self.rows = {}
        self.quotes = {}
        self._frames = {}
        for row in journal.load_bars(session):
            if row["payload"].get("feed") == feed:
                self.rows.setdefault(row["symbol"], {})[row["timestamp"]] = row["payload"]

    def bar(self, message, received_at):
        stamp = aware_timestamp(message["timestamp"])
        if stamp != stamp.floor("min"):
            raise ValueError("Minute bar timestamp must be aligned to a minute.")
        symbol = str(message["symbol"]).upper()
        prices = {name: float(message[name]) for name in ("open", "high", "low", "close")}
        if not all(math.isfinite(value) and value > 0 for value in prices.values()):
            raise ValueError("Invalid OHLC prices.")
        if not (prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]):
            raise ValueError("OHLC prices outside high/low bounds.")
        volume = float(message["volume"])
        if not math.isfinite(volume) or volume < 0 or not volume.is_integer():
            raise ValueError("Invalid volume.")
        received = aware_timestamp(received_at)
        if stamp > received:
            raise ValueError("Future bar received.")
        if stamp.tz_convert(NY).date().isoformat() != self.session:
            return False
        row = {**prices, "volume": int(volume), "timestamp": stamp.isoformat(),
               "received_at": received.isoformat(), "feed": self.feed, "ticker": symbol}
        key = stamp.isoformat(timespec="microseconds")
        previous = self.rows.setdefault(symbol, {}).get(key)
        if previous and all(previous.get(name) == row.get(name) for name in (*prices, "volume")):
            return False
        self.journal.event(self.session, "BAR_OBSERVATION", row)
        self.journal.put_bar(self.session, symbol, stamp, row)
        self.rows[symbol][key] = row
        self._frames.pop(symbol, None)
        return True

    def quote(self, message, received_at):
        quote = Quote.from_message(message, received_at)
        previous = self.quotes.get(quote.symbol)
        if previous is None or quote.timestamp > previous.timestamp:
            self.quotes[quote.symbol] = quote
            return quote
        return None

    def frame(self, symbol, now, regular_only=True):
        cache_key = (aware_timestamp(now).floor("min"), regular_only)
        cached = self._frames.get(symbol)
        if cached and cached[0] == cache_key:
            return cached[1].copy()
        rows = list(self.rows.get(symbol, {}).values())
        if not rows:
            return pd.DataFrame(columns=["timestamp", "timestamp_ny", "open", "high", "low", "close", "volume"])
        result = pd.DataFrame(rows)
        result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
        result["timestamp_ny"] = result["timestamp"].dt.tz_convert(NY)
        result = result[result["timestamp"] + pd.Timedelta(minutes=1) <= aware_timestamp(now)]
        if regular_only:
            expected = get_expected_regular_session_minutes(self.session)
            result = result[result["timestamp_ny"].isin(expected)]
        result = result.sort_values("timestamp_ny").reset_index(drop=True)
        self._frames[symbol] = (cache_key, result)
        return result.copy()

    def ready_frame(self, symbol, now):
        frame = self.frame(symbol, now)
        if frame.empty:
            return frame, "NO_SESSION_DATA"
        expected = get_expected_regular_session_minutes(self.session)
        expected = expected[expected + pd.Timedelta(minutes=1) <= aware_timestamp(now)]
        if len(expected) < 5:
            return frame, "OPENING_RANGE_INCOMPLETE"
        if set(expected) - set(frame["timestamp_ny"]):
            return frame, "DATA_GAP_WAITING_FOR_BACKFILL"
        return frame, None


def with_entry_observation(frame, quote, now):
    """Append a quote-only next entry reference; no fabricated OHLC signal input."""
    stamp = aware_timestamp(now).floor("min").tz_convert(NY)
    row = {"timestamp": stamp.tz_convert("UTC"), "timestamp_ny": stamp,
           "open": (quote.bid + quote.ask) / 2, "high": (quote.bid + quote.ask) / 2,
           "low": (quote.bid + quote.ask) / 2, "close": (quote.bid + quote.ask) / 2,
           "volume": 0, "entry_only": True}
    result = frame.copy()
    result["entry_only"] = False
    return pd.concat([result, pd.DataFrame([row])], ignore_index=True)
