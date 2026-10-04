import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from paper_trading.notifications import notify_slack


NY_TZ = ZoneInfo("America/New_York")
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    report_date: str
    hour: str = ""
    eps_estimate: str = ""
    revenue_estimate: str = ""


@dataclass(frozen=True)
class CompanyNewsItem:
    symbol: str
    headline: str
    source: str = ""
    url: str = ""


def _group_by_symbol(items) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(item.symbol.upper(), []).append(item)
    return grouped


def _shorten(text: str, max_length: int = 96) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def parse_symbols(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    return list(dict.fromkeys(symbols))


def fetch_json(url: str, timeout: int = 15):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ai-trading-system"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset, errors="replace"))


def finnhub_url(path: str, params: dict[str, str]) -> str:
    return f"{FINNHUB_BASE_URL}{path}?{urllib.parse.urlencode(params)}"


def parse_earnings_payload(payload, requested_symbol: str) -> list[EarningsEvent]:
    rows = payload.get("earningsCalendar", []) if isinstance(payload, dict) else []
    events = []
    for row in rows:
        symbol = str(row.get("symbol") or requested_symbol).upper()
        date = str(row.get("date") or "").strip()
        if not symbol or not date:
            continue
        events.append(
            EarningsEvent(
                symbol=symbol,
                report_date=date,
                hour=str(row.get("hour") or "").strip(),
                eps_estimate=str(row.get("epsEstimate") or "").strip(),
                revenue_estimate=str(row.get("revenueEstimate") or "").strip(),
            )
        )
    return events


def parse_company_news_payload(payload, symbol: str, max_items: int = 3) -> list[CompanyNewsItem]:
    if not isinstance(payload, list):
        return []
    news = []
    for row in payload:
        headline = str(row.get("headline") or "").strip()
        if not headline:
            continue
        news.append(
            CompanyNewsItem(
                symbol=symbol,
                headline=headline,
                source=str(row.get("source") or "").strip(),
                url=str(row.get("url") or "").strip(),
            )
        )
        if len(news) >= max_items:
            break
    return news


def fetch_symbol_catalysts(
    symbol: str,
    api_key: str,
    start_date,
    end_date,
    include_news: bool = True,
) -> tuple[list[EarningsEvent], list[CompanyNewsItem]]:
    start = start_date.isoformat()
    end = end_date.isoformat()

    earnings_payload = fetch_json(
        finnhub_url(
            "/calendar/earnings",
            {
                "symbol": symbol,
                "from": start,
                "to": end,
                "token": api_key,
            },
        )
    )
    earnings = parse_earnings_payload(earnings_payload, symbol)

    news = []
    if include_news:
        news_payload = fetch_json(
            finnhub_url(
                "/company-news",
                {
                    "symbol": symbol,
                    "from": start,
                    "to": end,
                    "token": api_key,
                },
            )
        )
        news = parse_company_news_payload(news_payload, symbol)

    return earnings, news


def format_stock_catalyst_message(
    symbols: list[str],
    start_date,
    end_date,
    earnings: list[EarningsEvent],
    news: list[CompanyNewsItem],
    api_key_available: bool = True,
) -> str:
    lines = [
        "Stock catalyst risk check",
        "",
        f"Window: {start_date} to {end_date}",
        f"Watchlist: {', '.join(symbols)}",
        "",
    ]

    if not api_key_available:
        lines.extend(
            [
                "Finnhub API key is not configured, so stock-specific catalyst checks were skipped.",
                "Add GitHub secret FINNHUB_API_KEY to enable earnings/news context.",
            ]
        )
        return "\n".join(lines)

    if not earnings and not news:
        lines.extend(
            [
                "No earnings events or recent company headlines found for the watchlist.",
                "Normal strategy rules still apply.",
            ]
        )
        return "\n".join(lines)

    if earnings:
        lines.append("Earnings / event risk:")
        earnings_by_symbol = _group_by_symbol(earnings)
        for symbol in symbols:
            symbol_events = earnings_by_symbol.get(symbol, [])
            if not symbol_events:
                continue
            event_texts = []
            for event in symbol_events[:2]:
                detail = []
                if event.hour:
                    detail.append(event.hour)
                if event.eps_estimate:
                    detail.append(f"EPS {event.eps_estimate}")
                if event.revenue_estimate:
                    detail.append(f"Rev {event.revenue_estimate}")
                detail_text = f" ({', '.join(detail)})" if detail else ""
                event_texts.append(f"{event.report_date}{detail_text}")
            hidden_count = len(symbol_events) - len(event_texts)
            hidden_text = f"; +{hidden_count} more" if hidden_count > 0 else ""
            lines.append(f"- {symbol}: {'; '.join(event_texts)}{hidden_text}")
        lines.append("")

    if news:
        news_by_symbol = _group_by_symbol(news)
        symbols_with_news = [
            symbol
            for symbol in symbols
            if news_by_symbol.get(symbol)
        ]
        lines.extend(
            [
                "Recent company headlines:",
                f"Symbols with headlines: {len(symbols_with_news)}/{len(symbols)}",
                "",
                "```",
                "Symbol | Count | Latest headlines",
                "-------|-------|-----------------",
            ]
        )
        for symbol in symbols:
            symbol_news = news_by_symbol.get(symbol, [])
            if not symbol_news:
                lines.append(f"{symbol:<6} | 0     | none found")
                continue

            shown = []
            for item in symbol_news[:2]:
                source = f"{item.source}: " if item.source else ""
                shown.append(_shorten(f"{source}{item.headline}"))
            hidden_count = len(symbol_news) - len(shown)
            hidden_text = f" (+{hidden_count} more)" if hidden_count > 0 else ""
            lines.append(
                f"{symbol:<6} | {len(symbol_news):<5} | "
                f"{' / '.join(shown)}{hidden_text}"
            )
        lines.append("```")
        lines.append("")

    lines.extend(
        [
            "Use this as context, not a trade signal.",
            "If a strategy alert fires on a stock with fresh catalyst risk, check the headline before entering.",
        ]
    )
    return "\n".join(lines).strip()


def parse_args():
    parser = argparse.ArgumentParser(description="Check stock-specific earnings/news catalyst risk.")
    parser.add_argument("--symbols", default="AAPL,NVDA,AMD,SNDK,INTC,GOOGL,QQQ")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today in New York.")
    parser.add_argument("--lookback-days", type=int, default=1)
    parser.add_argument("--lookahead-days", type=int, default=7)
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--slack", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    api_key = os.getenv("FINNHUB_API_KEY")
    symbols = parse_symbols(args.symbols)
    trading_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(tz=NY_TZ).date()
    )
    start_date = trading_date - timedelta(days=args.lookback_days)
    end_date = trading_date + timedelta(days=args.lookahead_days)

    all_earnings = []
    all_news = []
    if api_key:
        try:
            for symbol in symbols:
                earnings, news = fetch_symbol_catalysts(
                    symbol=symbol,
                    api_key=api_key,
                    start_date=start_date,
                    end_date=end_date,
                    include_news=not args.skip_news,
                )
                all_earnings.extend(earnings)
                all_news.extend(news)
        except Exception as exc:
            message = (
                "Stock catalyst risk check\n\n"
                f"Could not load Finnhub catalyst data: {type(exc).__name__}: {exc}\n"
                "Trading bot will still run, but there is no stock-specific catalyst context."
            )
        else:
            message = format_stock_catalyst_message(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                earnings=sorted(all_earnings, key=lambda item: (item.report_date, item.symbol)),
                news=all_news,
                api_key_available=True,
            )
    else:
        message = format_stock_catalyst_message(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            earnings=[],
            news=[],
            api_key_available=False,
        )

    print(message)
    notify_slack(
        webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
        message=message,
        enabled=args.slack,
    )


if __name__ == "__main__":
    main()
