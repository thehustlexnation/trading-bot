from scripts.premarket_youtube_context import (
    TickerContext,
    VideoRef,
    extract_ticker_context,
    format_context_message,
    parse_video_id,
    parse_watchlist,
)


def test_parse_video_id_accepts_common_youtube_urls():
    assert parse_video_id("https://www.youtube.com/watch?v=abcdefghijk") == "abcdefghijk"
    assert parse_video_id("https://youtu.be/abcdefghijk") == "abcdefghijk"
    assert parse_video_id("abcdefghijk") == "abcdefghijk"
    assert parse_video_id("https://www.youtube.com/live/abcdefghijk") == "abcdefghijk"


def test_parse_watchlist_normalizes_and_deduplicates():
    assert parse_watchlist("aapl, NVDA, aapl, spy") == ["AAPL", "NVDA", "SPY"]


def test_extract_ticker_context_finds_bias_and_levels():
    transcript = (
        "Today I am watching NVDA. NVDA looks strong above 231 and could breakout. "
        "AAPL is weak below 330 with resistance near 334. "
        "Random words should not matter."
    )

    contexts = extract_ticker_context(transcript, ["AAPL", "NVDA", "TSLA"])
    by_symbol = {context.ticker: context for context in contexts}

    assert by_symbol["NVDA"].bias == "bullish context"
    assert "231" in by_symbol["NVDA"].levels
    assert by_symbol["AAPL"].bias == "bearish/caution context"
    assert "330" in by_symbol["AAPL"].levels
    assert "TSLA" not in by_symbol


def test_format_context_message_handles_missing_transcript():
    message = format_context_message(
        video=VideoRef(
            video_id="abcdefghijk",
            title="Premarket prep",
            url="https://www.youtube.com/watch?v=abcdefghijk",
        ),
        contexts=[],
        transcript_available=False,
    )

    assert "Pre-market video context" in message
    assert "Transcript/captions were not available" in message
    assert "context only" in message


def test_format_context_message_lists_detected_context():
    message = format_context_message(
        video=VideoRef(
            video_id="abcdefghijk",
            title="Premarket prep",
            url="https://www.youtube.com/watch?v=abcdefghijk",
        ),
        contexts=[
            TickerContext(
                ticker="NVDA",
                mentions=3,
                bias="bullish context",
                levels=("231", "228"),
            )
        ],
        transcript_available=True,
        transcript_source="yt_dlp_captions",
    )

    assert "NVDA: bullish context, 3 mention(s)" in message
    assert "levels heard: 231, 228" in message
    assert "Transcript source: yt_dlp_captions" in message
