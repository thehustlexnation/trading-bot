import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from paper_trading.notifications import notify_discord


NY_TZ = ZoneInfo("America/New_York")
DEFAULT_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


@dataclass(frozen=True)
class EconomicEvent:
    title: str
    country: str
    event_time: datetime
    impact: str
    forecast: str = ""
    previous: str = ""


def fetch_text(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ai-trading-system"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_ny_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def parse_csv_items(text: str) -> list[dict]:
    import csv
    from io import StringIO

    reader = csv.DictReader(StringIO(text))
    return [dict(row) for row in reader]


def parse_calendar_items(text: str) -> list[dict]:
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        payload = json.loads(text)
        if isinstance(payload, dict):
            for key in ("events", "calendar", "data"):
                if isinstance(payload.get(key), list):
                    return payload[key]
            return []
        if isinstance(payload, list):
            return payload
        return []
    return parse_csv_items(text)


def parse_event(item: dict) -> EconomicEvent | None:
    title = str(item.get("title") or item.get("event") or item.get("name") or "").strip()
    country = str(item.get("country") or item.get("currency") or "").strip().upper()
    impact = str(item.get("impact") or "").strip().title()
    raw_date = str(item.get("date") or item.get("datetime") or "").strip()
    if not title or not country or not raw_date:
        return None

    try:
        event_time = datetime.fromisoformat(raw_date)
    except ValueError:
        return None
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=NY_TZ)
    event_time = event_time.astimezone(NY_TZ)

    return EconomicEvent(
        title=title,
        country=country,
        event_time=event_time,
        impact=impact,
        forecast=str(item.get("forecast") or "").strip(),
        previous=str(item.get("previous") or "").strip(),
    )


def parse_calendar_events(text: str) -> list[EconomicEvent]:
    return [
        event
        for event in (parse_event(item) for item in parse_calendar_items(text))
        if event is not None
    ]


def filter_news_risk_events(
    events: list[EconomicEvent],
    trading_date,
    countries: set[str],
    impacts: set[str],
    start_time: time,
    end_time: time,
) -> list[EconomicEvent]:
    return sorted(
        [
            event
            for event in events
            if event.event_time.date() == trading_date
            and event.country in countries
            and event.impact.upper() in impacts
            and start_time <= event.event_time.time() <= end_time
        ],
        key=lambda event: event.event_time,
    )


def format_news_risk_message(
    events: list[EconomicEvent],
    trading_date,
    start_time: time,
    end_time: time,
    countries: set[str],
    impacts: set[str],
) -> str:
    country_text = ", ".join(sorted(countries))
    impact_text = ", ".join(sorted(impacts))
    lines = [
        "Pre-market news risk check",
        "",
        f"Date: {trading_date}",
        f"Window: {start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')} New York",
        f"Filter: {country_text} / {impact_text} impact",
        "",
    ]

    if not events:
        lines.extend(
            [
                "No matching high-risk economic events found in the watch window.",
                "Normal strategy rules still apply.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "News risk detected:",
            "",
        ]
    )
    for event in events[:10]:
        details = []
        if event.forecast:
            details.append(f"forecast {event.forecast}")
        if event.previous:
            details.append(f"previous {event.previous}")
        detail_text = f" ({', '.join(details)})" if details else ""
        lines.append(
            f"- {event.event_time.strftime('%H:%M')} NY | {event.country} | "
            f"{event.impact}: {event.title}{detail_text}"
        )

    if len(events) > 10:
        lines.append(f"- +{len(events) - 10} more")

    lines.extend(
        [
            "",
            "Use this as a risk warning, not a trade signal.",
            "If a bot trade fires near major USD news, consider waiting for volatility to settle.",
        ]
    )
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="Check ForexFactory economic calendar news risk.")
    parser.add_argument("--calendar-url", default=DEFAULT_CALENDAR_URL)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today in New York.")
    parser.add_argument("--countries", default="USD")
    parser.add_argument("--impacts", default="High")
    parser.add_argument("--start", default="08:00", help="New York HH:MM")
    parser.add_argument("--end", default="10:30", help="New York HH:MM")
    parser.add_argument("--discord", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    trading_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(tz=NY_TZ).date()
    )
    countries = {item.strip().upper() for item in args.countries.split(",") if item.strip()}
    impacts = {item.strip().upper() for item in args.impacts.split(",") if item.strip()}
    start_time = parse_ny_clock(args.start)
    end_time = parse_ny_clock(args.end)

    try:
        text = fetch_text(args.calendar_url)
        events = parse_calendar_events(text)
        risk_events = filter_news_risk_events(
            events=events,
            trading_date=trading_date,
            countries=countries,
            impacts=impacts,
            start_time=start_time,
            end_time=end_time,
        )
        message = format_news_risk_message(
            events=risk_events,
            trading_date=trading_date,
            start_time=start_time,
            end_time=end_time,
            countries=countries,
            impacts=impacts,
        )
    except Exception as exc:
        message = (
            "Pre-market news risk check\n\n"
            f"Could not load ForexFactory calendar export: {type(exc).__name__}: {exc}\n"
            "Trading bot will still run, but there is no economic-calendar risk context."
        )

    print(message)
    notify_discord(
        webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        message=message,
        enabled=args.discord,
    )


if __name__ == "__main__":
    main()
