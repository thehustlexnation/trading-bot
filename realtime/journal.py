from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from sqlalchemy import (JSON, Column, Float, MetaData, String, Table, Text,
                        create_engine, delete, insert, select, update)
from sqlalchemy.exc import IntegrityError


UTC = timezone.utc


def utc_string(value):
    if value.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def serializable(value):
    def convert(item):
        if is_dataclass(item):
            return asdict(item)
        if hasattr(item, "isoformat"):
            return item.isoformat()
        raise TypeError(f"Unsupported journal value type: {type(item).__name__}")

    return json.loads(json.dumps(value, default=convert, allow_nan=False))


def stable_id(*values):
    return hashlib.sha256(json.dumps(serializable(values), sort_keys=True).encode()).hexdigest()


def entry_message(payload):
    return (f"SHADOW CANDIDATE - {payload['symbol']}\nStrategy: {payload.get('strategy', 'UNKNOWN')}\n"
            f"Direction: {payload['side'].upper()} | quantity: {payload['quantity']}\n"
            f"Hypothetical quote entry: {payload['entry']:.2f} | stop: {payload['stop']:.2f} | target: {payload['target']:.2f}\n"
            f"Reserved hypothetical risk: ${payload['risk']:.2f}\n"
            "No broker order or fill. Target is 2R, not a forecast.")


def exit_message(payload):
    from realtime.diagnostics import quote_spread_bps

    unverified = payload.get("path_gap", False)
    title = "UNVERIFIED SHADOW OBSERVATION" if unverified else "SHADOW RESULT"
    lines = [f"{title} - {payload['symbol']} | {payload['outcome']}",
             f"Observed quote mark: {payload['r_multiple']:+.2f}R / ${payload['pnl']:+.2f} before fees."]
    if all(name in payload for name in ("entry", "stop", "exit", "risk")):
        lines.extend([f"Entry: {payload['entry']:.2f} | planned stop: {payload['stop']:.2f} | observed exit: {payload['exit']:.2f}",
                      f"1R planned risk: ${payload['risk']:.2f}; R = observed P/L / planned risk."])
    spread = quote_spread_bps(payload.get("exit_quote"))
    if spread is not None:
        lines.append(f"Exit quote spread: {spread:.1f} bps.")
    if unverified:
        reasons = ", ".join(payload.get("unverified_reasons", {})) or "LEGACY_UNVERIFIED_PATH"
        lines.extend([f"Unverified reasons: {reasons}.",
                      "Excluded from verified-path P/L. A quote mark beyond the stop does not establish a fill at that price."])
    lines.append("Quote-based simulation only; no broker position was opened or closed.")
    return "\n".join(lines)


class Journal:
    """Portable durable shadow state; no broker orders or broker fill claims."""

    def __init__(self, url="sqlite:///logs/realtime.sqlite3", schema=None):
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"connect_args": {"timeout": 15}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        self.engine = create_engine(url, **kwargs)
        meta = MetaData(schema=schema)
        self.events = Table("realtime_events", meta,
            Column("id", String(64), primary_key=True), Column("session", String(32), index=True),
            Column("kind", String(64), index=True), Column("created_at", String(40)), Column("payload", JSON))
        self.leases = Table("realtime_leases", meta,
            Column("name", String(64), primary_key=True), Column("owner", String(64)), Column("expires", String(40)))
        self.bars = Table("realtime_bars", meta,
            Column("key", String(160), primary_key=True), Column("session", String(32), index=True),
            Column("symbol", String(10)), Column("timestamp", String(40)), Column("payload", JSON))
        self.positions = Table("realtime_shadow_positions", meta,
            Column("key", String(96), primary_key=True), Column("session", String(32), index=True),
            Column("symbol", String(10)), Column("group_name", String(48)), Column("state", String(32)),
            Column("risk", Float), Column("notional", Float), Column("pnl", Float), Column("payload", JSON))
        self.outbox = Table("realtime_outbox", meta,
            Column("id", String(64), primary_key=True), Column("session", String(32), index=True),
            Column("message", Text), Column("state", String(16)), Column("created_at", String(40)))
        meta.create_all(self.engine)

    @contextmanager
    def transaction(self):
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def event(self, session, kind, payload, key=None, now=None):
        payload = serializable(payload)
        key = key or stable_id(session, kind, payload)
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(self.events).values(id=key, session=session, kind=kind,
                    payload=payload, created_at=utc_string(now or datetime.now(UTC))))
        except IntegrityError:
            return False
        return True

    def history(self, session, kind):
        with self.engine.connect() as conn:
            return list(conn.execute(select(self.events.c.payload).where(
                self.events.c.session == session, self.events.c.kind == kind,
            ).order_by(self.events.c.created_at, self.events.c.id)).scalars())

    def acquire(self, owner, now, ttl=60):
        with self.transaction() as conn:
            lease = conn.execute(select(self.leases).where(self.leases.c.name == "shadow_runtime").with_for_update()).mappings().first()
            if lease and lease["owner"] != owner and lease["expires"] > utc_string(now):
                return False
            values = {"owner": owner, "expires": utc_string(now + timedelta(seconds=ttl))}
            if lease:
                conn.execute(update(self.leases).where(self.leases.c.name == "shadow_runtime").values(**values))
            else:
                try:
                    with conn.begin_nested():
                        conn.execute(insert(self.leases).values(name="shadow_runtime", **values))
                except IntegrityError:
                    return False
        return True

    def release(self, owner):
        with self.engine.begin() as conn:
            conn.execute(delete(self.leases).where(self.leases.c.name == "shadow_runtime", self.leases.c.owner == owner))

    def put_bar(self, session, symbol, timestamp, payload):
        key = f"{session}:{symbol}:{utc_string(timestamp)}"
        values = dict(session=session, symbol=symbol, timestamp=utc_string(timestamp), payload=serializable(payload))
        with self.transaction() as conn:
            changed = conn.execute(update(self.bars).where(self.bars.c.key == key).values(**values))
            if not changed.rowcount:
                conn.execute(insert(self.bars).values(key=key, **values))

    def load_bars(self, session):
        with self.engine.connect() as conn:
            return list(conn.execute(select(self.bars).where(self.bars.c.session == session).order_by(self.bars.c.timestamp)).mappings())

    def shadow_positions(self, session, open_only=False):
        query = select(self.positions).where(self.positions.c.session == session)
        if open_only:
            query = query.where(self.positions.c.state == "OPEN")
        with self.engine.connect() as conn:
            return list(conn.execute(query.order_by(self.positions.c.symbol)).mappings())

    def open_shadow(self, session, symbol, group_name, payload, config, owner, now):
        from realtime.config import clock
        from realtime.market import aware_timestamp, NY

        local_now = aware_timestamp(now).tz_convert(NY)
        if local_now.date().isoformat() != session or local_now.time() >= clock(config.last_entry):
            return "AFTER_LAST_ENTRY"
        if any(not math.isfinite(float(payload[name])) or float(payload[name]) <= 0
               for name in ("entry", "stop", "target", "quantity", "risk", "notional")):
            return "INVALID_RISK"
        expected_risk = abs(payload["entry"] - payload["stop"]) * payload["quantity"]
        if payload["quantity"] != int(payload["quantity"]) or abs(payload["notional"] - payload["entry"] * payload["quantity"]) > 1e-6:
            return "INVALID_QUANTITY_OR_NOTIONAL"
        if payload["side"] == "long":
            valid_prices = payload["stop"] < payload["entry"] < payload["target"]
        else:
            valid_prices = payload["side"] == "short" and payload["target"] < payload["entry"] < payload["stop"]
        if not valid_prices:
            return "INVALID_RISK"
        if abs(expected_risk - payload["risk"]) > 1e-6 or payload["risk"] > config.equity * config.risk_fraction + 1e-8:
            return "TRADE_RISK_LIMIT"
        if payload["notional"] > config.equity * config.max_notional_fraction + 1e-8:
            return "NOTIONAL_LIMIT"
        key = f"{session}:{symbol}"
        with self.transaction() as conn:
            lease = conn.execute(select(self.leases).where(self.leases.c.name == "shadow_runtime").with_for_update()).mappings().first()
            if not lease or lease["owner"] != owner or lease["expires"] <= utc_string(now):
                return "LEADERSHIP_LOST"
            positions = list(conn.execute(select(self.positions).where(self.positions.c.session == session)).mappings())
            if any(row["symbol"] == symbol for row in positions):
                return "SYMBOL_ALREADY_EVALUATED"
            active = [row for row in positions if row["state"] == "OPEN"]
            realized_loss = max(0, -sum(
                min(row["pnl"] or 0, -row["risk"]) if row["payload"].get("path_gap") else (row["pnl"] or 0)
                for row in positions if row["state"] == "CLOSED"
            ))
            if realized_loss + sum(row["risk"] for row in active) + payload["risk"] > config.equity * config.daily_loss_fraction + 1e-8:
                return "PORTFOLIO_RISK_LIMIT"
            if sum(row["notional"] for row in active) + payload["notional"] > config.equity * config.portfolio_notional_fraction + 1e-8:
                return "PORTFOLIO_NOTIONAL_LIMIT"
            if sum(row["notional"] for row in active if row["group_name"] == group_name) + payload["notional"] > config.equity * config.group_notional_fraction + 1e-8:
                return "GROUP_NOTIONAL_LIMIT"
            conn.execute(insert(self.positions).values(key=key, session=session, symbol=symbol,
                group_name=group_name, state="OPEN", risk=payload["risk"], notional=payload["notional"],
                pnl=0, payload=serializable({**payload, "symbol": symbol})))
            conn.execute(insert(self.outbox).values(id=stable_id(session, f"entry:{symbol}"),
                session=session, state="PENDING", created_at=utc_string(now),
                message=entry_message({**payload, "symbol": symbol})))
        return "APPROVED"

    def close_shadow(self, session, symbol, price, reason, now, exit_quote=None):
        key = f"{session}:{symbol}"
        with self.transaction() as conn:
            row = conn.execute(select(self.positions).where(self.positions.c.key == key).with_for_update()).mappings().first()
            if not row or row["state"] != "OPEN":
                return None
            payload = dict(row["payload"])
            sign = 1 if payload["side"] == "long" else -1
            pnl = sign * (price - payload["entry"]) * payload["quantity"]
            payload.update(exit=price, exit_time=utc_string(now), outcome=reason, pnl=pnl, r_multiple=pnl / row["risk"])
            payload["exit_quote"] = serializable(exit_quote)
            conn.execute(update(self.positions).where(self.positions.c.key == key).values(state="CLOSED", pnl=pnl, payload=payload))
            conn.execute(insert(self.outbox).values(id=stable_id(session, f"exit:{symbol}"),
                session=session, state="PENDING", created_at=utc_string(now), message=exit_message(payload)))
        return payload

    def mark_path_gap(self, session, symbol, reason="QUOTE_OBSERVATION_GAP", detail=None):
        with self.transaction() as conn:
            row = conn.execute(select(self.positions).where(
                self.positions.c.key == f"{session}:{symbol}", self.positions.c.state == "OPEN",
            ).with_for_update()).mappings().first()
            if row:
                reasons = {**row["payload"].get("unverified_reasons", {}), reason: serializable(detail or {})}
                payload = {**row["payload"], "path_gap": True, "unverified_reasons": reasons}
                conn.execute(update(self.positions).where(self.positions.c.key == row["key"]).values(payload=payload))

    def mark_one_r(self, session, symbol):
        with self.transaction() as conn:
            row = conn.execute(select(self.positions).where(
                self.positions.c.key == f"{session}:{symbol}", self.positions.c.state == "OPEN",
            ).with_for_update()).mappings().first()
            if row:
                payload = {**row["payload"], "one_r_reached": True}
                conn.execute(update(self.positions).where(self.positions.c.key == row["key"]).values(payload=payload))

    def enqueue(self, session, key, message, now=None):
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(self.outbox).values(id=stable_id(session, key), session=session,
                    message=message, state="PENDING", created_at=utc_string(now or datetime.now(UTC))))
        except IntegrityError:
            return False
        return True

    def pending_notifications(self):
        with self.engine.connect() as conn:
            return list(conn.execute(select(self.outbox).where(self.outbox.c.state == "PENDING").order_by(self.outbox.c.created_at).limit(50)).mappings())

    def mark_delivered(self, key):
        with self.engine.begin() as conn:
            conn.execute(update(self.outbox).where(self.outbox.c.id == key).values(state="DELIVERED"))
