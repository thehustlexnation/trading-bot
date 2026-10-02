from datetime import date, time

from scripts.forexfactory_news_risk import (
    filter_news_risk_events,
    format_news_risk_message,
    parse_calendar_events,
)


def test_parse_calendar_events_from_json():
    text = """
    [
      {
        "title": "Non-Farm Employment Change",
        "country": "USD",
        "date": "2026-10-02T08:30:00-04:00",
        "impact": "High",
        "forecast": "120K",
        "previous": "90K"
      }
    ]
    """

    events = parse_calendar_events(text)

    assert len(events) == 1
    assert events[0].title == "Non-Farm Employment Change"
    assert events[0].country == "USD"
    assert events[0].event_time.hour == 8
    assert events[0].impact == "High"


def test_filter_news_risk_events_keeps_usd_high_impact_in_window():
    events = parse_calendar_events(
        """
        [
          {"title":"CPI m/m","country":"USD","date":"2026-10-02T08:30:00-04:00","impact":"High"},
          {"title":"Factory Orders","country":"USD","date":"2026-10-02T10:45:00-04:00","impact":"High"},
          {"title":"ECB President Speaks","country":"EUR","date":"2026-10-02T09:00:00-04:00","impact":"High"},
          {"title":"FOMC Member Speaks","country":"USD","date":"2026-10-02T09:30:00-04:00","impact":"Low"}
        ]
        """
    )

    filtered = filter_news_risk_events(
        events=events,
        trading_date=date(2026, 10, 2),
        countries={"USD"},
        impacts={"HIGH"},
        start_time=time(8, 0),
        end_time=time(10, 30),
    )

    assert [event.title for event in filtered] == ["CPI m/m"]


def test_format_news_risk_message_with_events():
    events = parse_calendar_events(
        """
        [
          {
            "title":"CPI m/m",
            "country":"USD",
            "date":"2026-10-02T08:30:00-04:00",
            "impact":"High",
            "forecast":"0.3%",
            "previous":"0.2%"
          }
        ]
        """
    )

    message = format_news_risk_message(
        events=events,
        trading_date=date(2026, 10, 2),
        start_time=time(8, 0),
        end_time=time(10, 30),
        countries={"USD"},
        impacts={"HIGH"},
    )

    assert "News risk detected" in message
    assert "08:30 NY | USD | High: CPI m/m" in message
    assert "Use this as a risk warning" in message


def test_format_news_risk_message_without_events():
    message = format_news_risk_message(
        events=[],
        trading_date=date(2026, 10, 2),
        start_time=time(8, 0),
        end_time=time(10, 30),
        countries={"USD"},
        impacts={"HIGH"},
    )

    assert "No matching high-risk economic events" in message
