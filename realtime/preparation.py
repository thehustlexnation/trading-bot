from dataclasses import asdict
from datetime import timedelta
import math
import os

import pandas as pd

from realtime.config import GROUPS
from realtime.journal import serializable
from realtime.market import NY, aware_timestamp
from scripts.stock_catalyst_risk import fetch_symbol_catalysts
from strategy.market_calendar import get_previous_exchange_session


def prepare_history(frame):
    if frame.empty:
        result = pd.DataFrame(columns=["ticker", "open", "high", "low", "close", "volume"])
        result["timestamp"] = pd.Series([], dtype="datetime64[ns, UTC]")
        result["timestamp_ny"] = pd.Series([], dtype="datetime64[ns, America/New_York]")
        return result
    result = frame.reset_index().rename(columns={"symbol": "ticker"})
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    result["timestamp_ny"] = result["timestamp"].dt.tz_convert(NY)
    return result.sort_values("timestamp")


def activity_summary(history, symbol, now):
    history = history.copy()
    if "ticker" in history:
        history = history[history["ticker"] == symbol]
    if history.empty:
        return {"opening_rvol": None, "premarket_rvol": None, "historical_samples": 0}
    day = aware_timestamp(now).tz_convert(NY).date()
    history = history[history["timestamp"] + pd.Timedelta(minutes=1) <= aware_timestamp(now)]
    history["day"] = history["timestamp_ny"].dt.date
    minute = history["timestamp_ny"].dt.hour * 60 + history["timestamp_ny"].dt.minute
    opening = history[(minute >= 570) & (minute < 575)]
    counts = opening.groupby("day")["volume"].count()
    prior = opening[opening["day"] < day].groupby("day")["volume"].sum()
    prior = prior[counts.reindex(prior.index) == 5].tail(14)
    current = opening[opening["day"] == day]
    opening_rvol = None
    if len(prior) == 14 and len(current) == 5 and prior.mean() > 0:
        opening_rvol = float(current["volume"].sum() / prior.mean())
    time_limit = min(570, aware_timestamp(now).tz_convert(NY).hour * 60 + aware_timestamp(now).tz_convert(NY).minute)
    pre = history[(minute >= 240) & (minute < time_limit)]
    pre_prior = pre[pre["day"] < day].groupby("day")["volume"].sum().tail(14)
    pre_current = pre[pre["day"] == day]
    pre_rvol = None
    if len(pre_prior) == 14 and not pre_current.empty and pre_prior.mean() > 0:
        pre_rvol = float(pre_current["volume"].sum() / pre_prior.mean())
    return {"opening_rvol": opening_rvol, "premarket_rvol": pre_rvol,
            "historical_samples": len(prior), "feed_coverage": "observed-feed-only"}


def build_dossier(symbol, history, daily, now, catalysts=None):
    day = aware_timestamp(now).tz_convert(NY).date()
    rows = history[history["ticker"] == symbol].copy() if "ticker" in history else history.iloc[:0]
    rows = rows[rows["timestamp"] + pd.Timedelta(minutes=1) <= aware_timestamp(now)]
    before = daily[daily["ticker"] == symbol].copy() if "ticker" in daily else daily.iloc[:0]
    if not before.empty:
        before = before[before["timestamp_ny"].dt.date < day].sort_values("timestamp")
    current = rows[rows["timestamp_ny"].dt.date == day]
    pre = current[(current["timestamp_ny"].dt.hour * 60 + current["timestamp_ny"].dt.minute >= 240)
                  & (current["timestamp_ny"].dt.hour * 60 + current["timestamp_ny"].dt.minute < 570)]
    prior_close = float(before.iloc[-1]["close"]) if not before.empty else None
    latest = float(current.iloc[-1]["close"]) if not current.empty else None
    prior_day = get_previous_exchange_session(day)
    minute = rows["timestamp_ny"].dt.hour * 60 + rows["timestamp_ny"].dt.minute
    prior_regular = rows[(rows["timestamp_ny"].dt.date == prior_day) & (minute >= 570) & (minute < 960)]
    return {
        "symbol": symbol, "asof": aware_timestamp(now).isoformat(),
        "group": GROUPS.get(symbol, "unclassified"),
        "prior_close": prior_close,
        "pdh": float(prior_regular["high"].max()) if not prior_regular.empty else None,
        "pdl": float(prior_regular["low"].min()) if not prior_regular.empty else None,
        "daily_level_scope": "previous exchange session, regular-hours observed feed",
        "premarket_high": float(pre["high"].max()) if not pre.empty else None,
        "premarket_low": float(pre["low"].min()) if not pre.empty else None,
        "gap_fraction": latest / prior_close - 1 if latest and prior_close else None,
        "corporate_action_state": "UNKNOWN",
        "activity": activity_summary(rows, symbol, now),
        "catalysts": catalysts or {"status": "UNKNOWN", "reason": "not fetched"},
        "scenarios": {"bull": "Require a new confirmed long setup and final risk approval",
                      "bear": "Require a new confirmed short setup and final risk approval",
                      "stand_aside": "Missing required inputs, stale quotes, event risk or exhausted risk budget"},
    }


def collect_catalysts(symbols, now, on_symbol=None, stop_event=None):
    """Serial quota-aware discovery; incomplete sources never imply no catalyst."""
    import time

    key = os.getenv("FINNHUB_API_KEY")
    sec_agent = os.getenv("SEC_USER_AGENT")
    today = aware_timestamp(now).tz_convert(NY).date()
    cik_map = {}
    sec_status = "not configured"
    if sec_agent:
        try:
            cik_map = fetch_sec_json("https://www.sec.gov/files/company_tickers.json", sec_agent)
            cik_map = {row["ticker"]: str(row["cik_str"]).zfill(10) for row in cik_map.values()}
            sec_status = "available"
        except Exception as exc:
            sec_status = type(exc).__name__
    result = {}
    for symbol in symbols:
        if stop_event is not None and stop_event.is_set():
            break
        info = {"status": "UNKNOWN", "news": [], "earnings": [], "filings": [],
                "first_seen_at": aware_timestamp(now).isoformat(), "sec_status": sec_status}
        if symbol in {"QQQ", "TQQQ"}:
            info.update(status="ETF_CONTEXT_ONLY", reason="No issuer corporate earnings event")
        elif key:
            try:
                earnings, news = fetch_symbol_catalysts(symbol, key, today - timedelta(days=1), today + timedelta(days=7))
                info.update(status="AVAILABLE_UNVERIFIED", earnings=[asdict(item) for item in earnings],
                    news=[asdict(item) for item in news])
            except Exception as exc:
                info["reason"] = type(exc).__name__
            time.sleep(2.1)
        else:
            info["reason"] = "FINNHUB_API_KEY not configured"
        if symbol in cik_map:
            try:
                payload = fetch_sec_json(f"https://data.sec.gov/submissions/CIK{cik_map[symbol]}.json", sec_agent)
                info["filings"] = parse_filings(payload, cik_map[symbol], today, pd.Timestamp.now(tz="UTC"))
                info["sec_status"] = "AVAILABLE"
            except Exception as exc:
                info["sec_status"] = type(exc).__name__
            time.sleep(0.2)
        info["first_seen_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        result[symbol] = serializable(info)
        if on_symbol:
            on_symbol(symbol, result[symbol])
    return result


def fetch_sec_json(url, agent):
    import json
    from urllib.request import Request, urlopen

    with urlopen(Request(url, headers={"User-Agent": agent, "Accept": "application/json"}), timeout=15) as response:
        return json.load(response)


def parse_filings(payload, cik, today, now):
    rows = payload.get("filings", {}).get("recent", {})
    result = []
    for i, day in enumerate(rows.get("filingDate", [])):
        if not ((today - timedelta(days=7)).isoformat() <= day <= today.isoformat()):
            continue
        if rows["form"][i] not in {"8-K", "10-Q", "10-K", "6-K"}:
            continue
        accession = rows["accessionNumber"][i].replace("-", "")
        filename = rows["primaryDocument"][i]
        result.append({"form": rows["form"][i], "filing_date": day,
            "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{filename}",
            "first_seen_at": aware_timestamp(now).isoformat(), "verification": "issuer filing metadata only; contents not analyzed"})
    return result


def format_preparation(dossiers, feed, phase="initial"):
    title = {"initial": "Initial preparation", "watch": "Watch preparation", "opening": "Opening preparation"}[phase]
    lines = [f"{title} | SHADOW ONLY", f"Feed: {feed}; all {len(dossiers)} configured instruments",
             f"As of: {max((row['asof'] for row in dossiers), default='unknown')}", "```",
             "Symbol | Gap     | Pre RVOL | News / filings"]
    for row in dossiers:
        gap = f"{row['gap_fraction']:+.2%}" if row["gap_fraction"] is not None else "unknown"
        rvol = row["activity"]["premarket_rvol"]
        activity = f"{rvol:.2f}x" if rvol is not None and math.isfinite(rvol) else "unknown"
        context = row["catalysts"]
        lines.append(f"{row['symbol']:<6} | {gap:>7} | {activity:>8} | {len(context.get('news', []))} / {len(context.get('filings', []))} ({context['status']})")
    lines.extend(["```", "Conditional scenarios and complete source evidence are retained in the journal. No orders are placed."])
    return "\n".join(lines)
