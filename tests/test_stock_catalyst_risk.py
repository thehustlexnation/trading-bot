from datetime import date

from scripts.stock_catalyst_risk import (
    CompanyNewsItem,
    EarningsEvent,
    format_stock_catalyst_message,
    parse_company_news_payload,
    parse_earnings_payload,
    parse_symbols,
)


def test_parse_symbols_normalizes_and_deduplicates():
    assert parse_symbols("aapl, NVDA, aapl, qqq") == ["AAPL", "NVDA", "QQQ"]


def test_parse_earnings_payload():
    payload = {
        "earningsCalendar": [
            {
                "symbol": "NVDA",
                "date": "2026-10-06",
                "hour": "amc",
                "epsEstimate": 1.23,
                "revenueEstimate": 45000000000,
            }
        ]
    }

    events = parse_earnings_payload(payload, "NVDA")

    assert events == [
        EarningsEvent(
            symbol="NVDA",
            report_date="2026-10-06",
            hour="amc",
            eps_estimate="1.23",
            revenue_estimate="45000000000",
        )
    ]


def test_parse_company_news_payload_limits_items():
    payload = [
        {"headline": "Nvidia announces new AI chip", "source": "Reuters", "url": "https://example.com/1"},
        {"headline": "Analyst raises target", "source": "Bloomberg", "url": "https://example.com/2"},
    ]

    news = parse_company_news_payload(payload, "NVDA", max_items=1)

    assert news == [
        CompanyNewsItem(
            symbol="NVDA",
            headline="Nvidia announces new AI chip",
            source="Reuters",
            url="https://example.com/1",
        )
    ]


def test_format_stock_catalyst_message_missing_key():
    message = format_stock_catalyst_message(
        symbols=["AAPL", "NVDA"],
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 9),
        earnings=[],
        news=[],
        api_key_available=False,
    )

    assert "FINNHUB_API_KEY" in message
    assert "skipped" in message


def test_format_stock_catalyst_message_with_earnings_and_news():
    message = format_stock_catalyst_message(
        symbols=["NVDA"],
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 9),
        earnings=[
            EarningsEvent(
                symbol="NVDA",
                report_date="2026-10-06",
                hour="amc",
                eps_estimate="1.23",
            )
        ],
        news=[
            CompanyNewsItem(
                symbol="NVDA",
                headline="Nvidia announces new AI chip",
                source="Reuters",
            )
        ],
        api_key_available=True,
    )

    assert "Earnings / event risk" in message
    assert "NVDA: 2026-10-06" in message
    assert "Recent company headlines" in message
    assert "Symbol | Count | Latest headlines" in message
    assert "NVDA" in message
    assert "Reuters: Nvidia announces new AI chip" in message


def test_format_stock_catalyst_message_groups_headlines_by_symbol():
    message = format_stock_catalyst_message(
        symbols=["AAPL", "NVDA", "QQQ"],
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 9),
        earnings=[],
        news=[
            CompanyNewsItem(symbol="AAPL", headline="Apple headline one", source="Yahoo"),
            CompanyNewsItem(symbol="AAPL", headline="Apple headline two", source="Reuters"),
            CompanyNewsItem(symbol="AAPL", headline="Apple headline three", source="CNBC"),
            CompanyNewsItem(symbol="NVDA", headline="Nvidia headline", source="Yahoo"),
        ],
        api_key_available=True,
    )

    assert "Symbols with headlines: 2/3" in message
    assert "AAPL" in message
    assert "Apple headline one" in message
    assert "(+1 more)" in message
    assert "QQQ    | 0" in message
