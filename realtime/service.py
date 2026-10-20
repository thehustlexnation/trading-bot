import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import signal
import threading
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from realtime.acceptance import session_report
from realtime.config import RuntimeConfig, clock
from realtime.engine import ShadowEngine
from realtime.journal import Journal
from realtime.market import NY
from realtime.network import BoundedHTTPAdapter
from realtime.preparation import build_dossier, collect_catalysts, format_preparation, prepare_history
from realtime.readiness import preparation_phase, strategy_readiness
from realtime.telemetry import RuntimeTelemetry

UTC = timezone.utc
LOG = logging.getLogger("realtime")
HEALTH = {"phase": "STARTING", "mode": "SHADOW", "heartbeat": time.monotonic(), "faulted": False}


def database_url():
    explicit = os.getenv("REALTIME_DATABASE_URL") or os.getenv("DATABASE_URL")
    if explicit:
        if os.getenv("RAILWAY_ENVIRONMENT_ID") and not explicit.startswith("postgres"):
            raise ValueError("Railway requires shared PostgreSQL for leadership and durable state.")
        return explicit
    if os.getenv("RAILWAY_ENVIRONMENT_ID"):
        raise ValueError("A PostgreSQL database URL is required on Railway; ephemeral state is not accepted.")
    return "sqlite:///logs/realtime.sqlite3"


def configuration_report(config):
    return {
        "mode": "SHADOW_ONLY", "broker_submission": "DISABLED",
        "policy": config.policy_version, "config_hash": config.fingerprint,
        "feed": config.feed, "watchlist": config.watchlist,
        "subscriptions": config.subscriptions, "lane_count": len(config.lanes),
        "lanes": config.lanes, "strategy_readiness": strategy_readiness(config),
        "preparation_start_ny": config.preparation_start, "watch_start_ny": config.watch_start,
        "last_entry_ny": config.last_entry, "cutoff_ny": config.cutoff,
        "storage": "PostgreSQL" if database_url().startswith("postgres") else "local SQLite",
        "credentials_configured": bool(os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY")),
        "slack_configured": bool(os.getenv("SLACK_WEBHOOK_URL")),
        "finnhub_configured": bool(os.getenv("FINNHUB_API_KEY")),
        "sec_configured": bool(os.getenv("SEC_USER_AGENT")),
        "warnings": [f"{strategy}: warmup leaves no entry window before {config.last_entry} NY."
                     for strategy, row in strategy_readiness(config).items() if row["status"] == "NO_ENTRY_WINDOW"],
    }


def health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/healthz":
                self.send_error(404)
                return
            healthy = not HEALTH["faulted"] and time.monotonic() - HEALTH["heartbeat"] < 120
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({k: v for k, v in HEALTH.items() if k != "heartbeat"}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def fetch_history(config, now, recent=False):
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise ValueError("Missing Alpaca market-data credentials.")
    client = StockHistoricalDataClient(key, secret)
    client._session.mount("https://", BoundedHTTPAdapter())
    start_day = datetime.combine(now.astimezone(ZoneInfo(NY)).date(), datetime.min.time(), ZoneInfo(NY))
    start = now - timedelta(minutes=8) if recent else start_day - timedelta(days=config.history_days)
    minute = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=list(config.subscriptions),
        start=start, end=now, timeframe=TimeFrame.Minute, feed=DataFeed(config.feed), adjustment="raw")).df
    if recent:
        return prepare_history(minute), pd.DataFrame()
    daily = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=list(config.watchlist),
        start=start_day - timedelta(days=100), end=start_day,
        timeframe=TimeFrame.Day, feed=DataFeed(config.feed), adjustment="raw")).df
    return prepare_history(minute), prepare_history(daily)


async def stream_smoke(config, seconds):
    """Read-only, time-bounded authentication/subscription check; no Slack or orders."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.live import StockDataStream

    stream = StockDataStream(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], feed=DataFeed(config.feed))
    counts = {"bars": 0, "quotes": 0}

    async def bar(_):
        counts["bars"] += 1

    async def quote(_):
        counts["quotes"] += 1

    stream.subscribe_bars(bar, *config.subscriptions)
    stream.subscribe_quotes(quote, *config.subscriptions)
    stream.subscribe_updated_bars(bar, *config.subscriptions)
    task = asyncio.create_task(stream._run_forever())
    connected = False
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not task.done():
            connected = connected or bool(stream._running)
            await asyncio.sleep(0.25)
    finally:
        await stream.stop_ws()
        await stream.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    print(json.dumps({"authenticated_and_subscribed": connected, "feed": config.feed,
        "symbol_count": len(config.subscriptions), "received": counts, "orders_sent": 0}), flush=True)
    if not connected:
        raise RuntimeError("Market-data stream authentication/subscription was not confirmed.")


async def acquire_session_lease(journal, owner, shutdown, timeout=90):
    deadline = time.monotonic() + timeout
    while not shutdown.is_set():
        if journal.acquire(owner, datetime.now(UTC)):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Another shadow worker still owns the database lease after the handover timeout.")
        HEALTH.update(phase="WAITING_FOR_LEASE", heartbeat=time.monotonic())
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=min(2, remaining))
        except TimeoutError:
            pass
    return False


async def run_session(config, journal, now, shutdown):
    from alpaca.data.enums import DataFeed
    from alpaca.data.live import StockDataStream
    from paper_trading.notifications import notify_slack
    from scripts.paper_trade import build_macro_risk_context

    session = now.astimezone(ZoneInfo(NY)).date().isoformat()
    owner = uuid4().hex
    if not await acquire_session_lease(journal, owner, shutdown):
        return
    now = datetime.now(UTC)
    HEALTH.update(phase="BOOTSTRAP", session=session, feed=config.feed,
                  config_hash=config.fingerprint, faulted=False)
    engine = ShadowEngine(config, journal, session, owner,
        code_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA", os.getenv("GITHUB_SHA", "local")),
        now_fn=lambda: datetime.now(UTC))
    queue = asyncio.Queue(maxsize=10000)
    telemetry = RuntimeTelemetry()
    stream = None
    stream_task = None
    tasks = []
    history, daily, catalysts = pd.DataFrame(), pd.DataFrame(), {}
    lost_lease = asyncio.Event()
    source_stop = threading.Event()

    async def heartbeat():
        while not shutdown.is_set():
            if not journal.acquire(owner, datetime.now(UTC)):
                lost_lease.set()
                engine.stopped = True
                HEALTH["faulted"] = True
                return
            HEALTH["heartbeat"] = time.monotonic()
            await asyncio.sleep(10)

    async def ingest_history(frame):
        for index, row in enumerate(frame.to_dict("records")):
            if row["timestamp_ny"].date().isoformat() == session:
                engine.ingest_bar({**row, "symbol": row["ticker"]}, datetime.now(UTC))
            if index % 100 == 0:
                await asyncio.sleep(0)

    def save_preparation():
        nonlocal history
        current = [engine.market.frame(symbol, datetime.now(UTC), False) for symbol in config.watchlist]
        current = [frame for frame in current if not frame.empty]
        if current:
            history = pd.concat([history, *current], ignore_index=True).drop_duplicates(["ticker", "timestamp"], keep="last")
        dossiers = [build_dossier(symbol, history, daily, datetime.now(UTC), catalysts.get(symbol)) for symbol in config.watchlist]
        for row in dossiers:
            journal.event(session, "PREPARATION", row)
        return dossiers

    async def preparation_refresh():
        nonlocal catalysts
        while not shutdown.is_set():
            try:
                catalysts = await asyncio.to_thread(collect_catalysts, config.watchlist, datetime.now(UTC), stop_event=source_stop)
                save_preparation()
            except Exception as exc:
                journal.event(session, "PREPARATION_SOURCE_ERROR", {"error_type": type(exc).__name__})
            await asyncio.sleep(600)

    async def context_refresh():
        while not shutdown.is_set():
            context = await asyncio.to_thread(build_macro_risk_context, datetime.fromisoformat(session).date(), "08:00", "10:30")
            engine.set_macro(context, datetime.now(UTC))
            await asyncio.sleep(300)

    async def notification_delivery():
        while not shutdown.is_set():
            if os.getenv("REALTIME_NOTIFY", "true").lower() == "true" and os.getenv("SLACK_WEBHOOK_URL"):
                for row in journal.pending_notifications():
                    sent = await asyncio.to_thread(notify_slack, os.getenv("SLACK_WEBHOOK_URL"), row["message"], True)
                    if sent:
                        journal.mark_delivered(row["id"])
                    else:
                        break
            await asyncio.sleep(5)

    async def enqueue(kind, message):
        payload = message.model_dump() if hasattr(message, "model_dump") else dict(message)
        await queue.put((kind, payload, datetime.now(UTC)))

    async def bar(message):
        await enqueue("bar", message)

    async def revised_bar(message):
        await enqueue("bar_revision", message)

    async def quote(message):
        await enqueue("quote", message)

    async def consume():
        while not shutdown.is_set():
            kind, message, received = await queue.get()
            try:
                processed, depth = datetime.now(UTC), queue.qsize() + 1
                if kind in {"bar", "bar_revision"}:
                    engine.ingest_bar(message, received)
                else:
                    engine.ingest_quote(message, received)
                telemetry.market_event(kind, message, received, processed, depth)
            except ValueError:
                journal.event(session, "INVALID_MARKET_EVENT", {"kind": kind, "symbol": message.get("symbol")})
            finally:
                queue.task_done()

    try:
        tasks.append(asyncio.create_task(heartbeat()))
        history, daily = await asyncio.to_thread(fetch_history, config, now)
        await ingest_history(history)
        engine.daily = {symbol: daily[daily["ticker"] == symbol].copy() if "ticker" in daily else pd.DataFrame()
                        for symbol in config.htf}
        identity = {**configuration_report(config), "code_sha": engine.code_sha}
        journal.event(session, "SESSION_START", identity)
        LOG.info("Shadow worker identity %s", json.dumps(identity, sort_keys=True))
        journal.enqueue(session, f"start:{engine.code_sha}",
            f"Shadow worker starting | {session}\nStream connection not yet confirmed. Broker submission disabled.\n"
            f"Feed: {config.feed} | {len(config.watchlist)} instruments | {len(config.lanes)} lanes\n"
            f"Entry cutoff: {config.last_entry} NY | monitoring cutoff: {config.cutoff} NY\n"
            f"Code: {engine.code_sha} | config: {config.fingerprint}")
        stream = StockDataStream(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], feed=DataFeed(config.feed))
        stream.subscribe_bars(bar, *config.subscriptions)
        stream.subscribe_updated_bars(revised_bar, *config.subscriptions)
        stream.subscribe_quotes(quote, *config.subscriptions)
        stream_task = asyncio.create_task(stream._run_forever())
        tasks.extend([asyncio.create_task(consume()), asyncio.create_task(context_refresh()),
                      asyncio.create_task(preparation_refresh()), asyncio.create_task(notification_delivery())])
        HEALTH["phase"] = "STREAM_CONNECTING"
        connection_deadline = time.monotonic() + 45
        was_connected = False
        last_backfill, last_preparation, last_telemetry = time.monotonic(), 0, time.monotonic()
        last_preparation_phase = None
        while not shutdown.is_set():
            current = datetime.now(UTC)
            if lost_lease.is_set() or any(task.done() for task in tasks):
                raise RuntimeError("Required background task stopped; shadow evaluation halted.")
            if stream_task.done():
                raise RuntimeError("Market-data stream stopped.")
            if stream._running:
                if not was_connected:
                    journal.event(session, "STREAM_SUBSCRIPTION_CONFIRMED", {"symbols": config.subscriptions, "feed": config.feed})
                    journal.enqueue(session, f"stream_connected:{engine.code_sha}", f"Market-data subscription confirmed | {session}\n"
                        f"Feed: {config.feed} | {len(config.subscriptions)} symbols.\n"
                        f"Code: {engine.code_sha}\n"
                        "Shadow only. Per-symbol data completeness and quote freshness are checked separately.")
                was_connected = True
                connection_deadline = time.monotonic() + 60
                HEALTH["phase"] = "STREAMING_SHADOW"
            elif time.monotonic() > connection_deadline:
                raise RuntimeError("Market-data stream authentication/recovery deadline exceeded.")
            else:
                HEALTH["phase"] = "STREAM_RECONNECTING" if was_connected else "STREAM_CONNECTING"
            if time.monotonic() - last_backfill >= 60:
                try:
                    recent, _ = await asyncio.to_thread(fetch_history, config, current, True)
                    await ingest_history(recent)
                except Exception as exc:
                    journal.event(session, "BACKFILL_ERROR", {"error_type": type(exc).__name__, "at": current})
                last_backfill = time.monotonic()
            phase = preparation_phase(pd.Timestamp(current), config)
            if phase != last_preparation_phase or time.monotonic() - last_preparation >= 300:
                journal.enqueue(session, f"preparation:{phase}", format_preparation(save_preparation(), config.feed, phase))
                last_preparation = time.monotonic()
                last_preparation_phase = phase
            if bool(stream._running) and queue.empty():
                started = time.monotonic()
                engine.tick(datetime.now(UTC))
                telemetry.observe("evaluation_seconds", time.monotonic() - started)
                HEALTH.update(last_tick_seconds=time.monotonic() - started,
                              open_shadow_positions=len(engine.positions), queued_events=queue.qsize())
            if time.monotonic() - last_telemetry >= 60:
                snapshot = telemetry.snapshot(datetime.now(UTC))
                journal.event(session, "RUNTIME_TELEMETRY", snapshot)
                LOG.info("Shadow telemetry %s", json.dumps(snapshot, sort_keys=True))
                telemetry.reset()
                last_telemetry = time.monotonic()
            current = datetime.now(UTC)
            cutoff = datetime.combine(current.astimezone(ZoneInfo(NY)).date(), clock(config.cutoff), ZoneInfo(NY))
            if current >= cutoff.astimezone(UTC):
                if not engine.positions:
                    break
                if current >= (cutoff + timedelta(seconds=60)).astimezone(UTC):
                    journal.event(session, "UNRESOLVED_SHADOW_EXPOSURE", {"symbols": sorted(engine.positions)})
                    raise RuntimeError("Shadow cutoff has no fresh quote; journal remains OPEN, not falsely closed.")
            await asyncio.sleep(1)
        recap = engine.recap()
        journal.event(session, "RUNTIME_TELEMETRY", telemetry.snapshot(datetime.now(UTC)))
        journal.event(session, "SESSION_RECAP", {"text": recap})
        journal.enqueue(session, "recap", recap)
        print(recap, flush=True)
        await asyncio.sleep(6)
        try:
            report = session_report(journal.engine, session, datetime.now(UTC), engine.code_sha)
            journal.event(session, "SESSION_ACCEPTANCE", report)
            LOG.info("Shadow acceptance %s", json.dumps(report, sort_keys=True))
        except Exception as exc:
            LOG.error("Session acceptance report unavailable: %s", type(exc).__name__)
    except Exception as exc:
        engine.stopped = True
        try:
            journal.event(session, "SESSION_FAILED", {"error_type": type(exc).__name__, "open_shadow_positions": sorted(engine.positions)})
            journal.enqueue(session, f"failed:{owner}",
                f"Shadow session failed | {session}\nError type: {type(exc).__name__}\n"
                f"Unresolved hypothetical positions: {', '.join(sorted(engine.positions)) or 'none'}\n"
                "No broker orders were placed. Inspect the journal and host logs.")
            await asyncio.sleep(6)
        except Exception:
            LOG.error("Could not persist session failure notification.")
        raise
    finally:
        engine.stopped = True
        source_stop.set()
        if stream is not None:
            await stream.stop_ws()
            await stream.close()
        if stream_task:
            stream_task.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, *([stream_task] if stream_task else []), return_exceptions=True)
        journal.release(owner)
        HEALTH["phase"] = "WAITING"


async def serve(config, args):
    from scripts.realtime_runner import next_runner_start, should_exit_before_wait

    journal = Journal(database_url())
    shutdown = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, shutdown.set)
    server = health_server()
    try:
        while not shutdown.is_set():
            now = datetime.now(UTC)
            start = next_runner_start(now, clock(config.preparation_start), clock(config.cutoff))
            wait = max(0, (start - now).total_seconds())
            LOG.info("Next session phase: %s; mode=SHADOW", start.isoformat())
            HEALTH.update(phase="WAITING", next_session=start.isoformat(), heartbeat=time.monotonic())
            if should_exit_before_wait(int(wait), args.max_wait_minutes):
                return
            while datetime.now(UTC) < start and not shutdown.is_set():
                HEALTH["heartbeat"] = time.monotonic()
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=min(30, max(0.1, (start - datetime.now(UTC)).total_seconds())))
                except TimeoutError:
                    pass
            if shutdown.is_set():
                return
            await run_session(config, journal, datetime.now(UTC), shutdown)
            if args.once:
                return
            await asyncio.sleep(2)
    finally:
        server.shutdown()
        server.server_close()
        journal.engine.dispose()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Multi-symbol streaming research runtime. Broker submission is disabled.")
    parser.add_argument("--check", action="store_true", help="Print sanitized configuration without network access.")
    parser.add_argument("--smoke-test", action="store_true", help="Read-only subscription check; no Slack or orders.")
    parser.add_argument("--bootstrap-test", action="store_true", help="Read-only historical bootstrap and preparation check; no Slack or orders.")
    parser.add_argument("--smoke-seconds", type=int, default=15)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--continuous", action="store_true", help="Explicitly opt into an always-running supervisor.")
    parser.add_argument("--max-wait-minutes", type=int, default=120)
    args = parser.parse_args()
    if args.once and args.continuous:
        parser.error("Choose --once or --continuous, not both.")
    args.once = not args.continuous
    if args.continuous:
        args.max_wait_minutes = None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = RuntimeConfig.from_env()
        if args.check:
            print(json.dumps(configuration_report(config), sort_keys=True, indent=2))
        elif args.bootstrap_test:
            history, daily = fetch_history(config, datetime.now(UTC))
            dossiers = [build_dossier(symbol, history, daily, datetime.now(UTC)) for symbol in config.watchlist]
            coverage = {symbol: int((history["ticker"] == symbol).sum()) for symbol in config.subscriptions}
            print(json.dumps({"mode": "READ_ONLY_BOOTSTRAP_TEST", "feed": config.feed,
                "minute_observations": coverage, "daily_observations": len(daily),
                "dossiers": len(dossiers), "orders_sent": 0, "messages_sent": 0}), flush=True)
            if any(count == 0 for count in coverage.values()):
                raise ValueError("Historical bootstrap did not cover every subscribed symbol.")
        elif args.smoke_test:
            if not 1 <= args.smoke_seconds <= 120:
                raise ValueError("Smoke duration must be between 1 and 120 seconds.")
            asyncio.run(stream_smoke(config, args.smoke_seconds))
        else:
            asyncio.run(serve(config, args))
    except Exception as exc:
        HEALTH.update(faulted=True, phase="FAILED")
        LOG.error("Runtime stopped: %s. Check configuration and structured journal events.", type(exc).__name__)
        raise SystemExit(1) from None
