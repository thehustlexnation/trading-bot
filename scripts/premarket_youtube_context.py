import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from paper_trading.notifications import notify_discord


DEFAULT_CHANNEL_URL = "https://www.youtube.com/@JdubTrades/streams"
DEFAULT_WATCHLIST = (
    "AAPL,NVDA,SNDK,INTC,GOOGL,MSFT,AMZN,META,TSLA,AMD,AVGO,"
    "JPM,V,MA,NFLX,COST,ORCL,CRM,ADBE,SPY,QQQ,IWM"
)


@dataclass(frozen=True)
class VideoRef:
    video_id: str
    title: str
    url: str
    published: str | None = None


@dataclass(frozen=True)
class TickerContext:
    ticker: str
    mentions: int
    bias: str
    levels: tuple[str, ...]


def fetch_text(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ai-trading-system"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_video_id(value: str) -> str | None:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query)
    if "v" in query and query["v"]:
        return query["v"][0]
    if parsed.netloc.endswith("youtu.be"):
        candidate = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate
    match = re.search(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})", parsed.path)
    if match:
        return match.group(1)
    return None


def extract_channel_id(page_html: str) -> str | None:
    patterns = [
        r'"channelId":"(UC[A-Za-z0-9_-]+)"',
        r'"browseId":"(UC[A-Za-z0-9_-]+)"',
        r'<meta itemprop="channelId" content="(UC[A-Za-z0-9_-]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html)
        if match:
            return match.group(1)
    return None


def latest_video_from_rss(channel_id: str) -> VideoRef | None:
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = fetch_text(feed_url)
    root = ET.fromstring(feed)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    entry = root.find("atom:entry", ns)
    if entry is None:
        return None

    video_id = entry.findtext("yt:videoId", namespaces=ns)
    title = entry.findtext("atom:title", default="Untitled", namespaces=ns)
    published = entry.findtext("atom:published", default=None, namespaces=ns)
    if not video_id:
        return None
    return VideoRef(
        video_id=video_id,
        title=title,
        url=f"https://www.youtube.com/watch?v={video_id}",
        published=published,
    )


def latest_video_from_channel(channel_url: str) -> VideoRef | None:
    page = fetch_text(channel_url)
    channel_id = extract_channel_id(page)
    if not channel_id:
        return None
    return latest_video_from_rss(channel_id)


def get_video_title(video_id: str) -> str:
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    try:
        payload = json.loads(fetch_text(url))
    except (OSError, json.JSONDecodeError):
        return "YouTube video"
    return str(payload.get("title") or "YouTube video")


def transcript_languages(video_id: str) -> list[str]:
    url = f"https://video.google.com/timedtext?type=list&v={video_id}"
    try:
        text = fetch_text(url)
    except OSError:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    languages = []
    for track in root.findall("track"):
        lang = track.attrib.get("lang_code")
        if lang:
            languages.append(lang)
    return languages


def fetch_transcript(video_id: str, preferred_languages: tuple[str, ...] = ("en", "en-US")) -> str | None:
    languages = transcript_languages(video_id)
    if not languages:
        return None

    selected = next((lang for lang in preferred_languages if lang in languages), languages[0])
    query = urllib.parse.urlencode({"v": video_id, "lang": selected})
    url = f"https://video.google.com/timedtext?{query}"
    try:
        text = fetch_text(url)
    except OSError:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    parts = []
    for node in root.findall("text"):
        if node.text:
            parts.append(html.unescape(node.text).replace("\n", " "))
    transcript = " ".join(parts).strip()
    return transcript or None


def parse_watchlist(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    return list(dict.fromkeys(symbols))


def extract_ticker_context(transcript: str, watchlist: list[str]) -> list[TickerContext]:
    contexts = []
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", transcript)
        if sentence.strip()
    ]
    bullish_words = {"BULLISH", "LONG", "BREAKOUT", "ABOVE", "SUPPORT", "BOUNCE", "STRONG"}
    bearish_words = {"BEARISH", "SHORT", "BREAKDOWN", "BELOW", "RESISTANCE", "REJECT", "WEAK"}

    for ticker in watchlist:
        ticker_pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])")
        ticker_sentences = [
            sentence
            for sentence in sentences
            if ticker_pattern.search(sentence.upper())
        ]
        mentions = sum(
            len(list(ticker_pattern.finditer(sentence.upper())))
            for sentence in ticker_sentences
        )
        if not mentions:
            continue

        joined = " ".join(ticker_sentences[:8])
        joined_upper = joined.upper()

        bullish_score = sum(joined_upper.count(word) for word in bullish_words)
        bearish_score = sum(joined_upper.count(word) for word in bearish_words)
        if bullish_score > bearish_score:
            bias = "bullish context"
        elif bearish_score > bullish_score:
            bias = "bearish/caution context"
        else:
            bias = "mentioned, unclear bias"

        levels = tuple(
            dict.fromkeys(
                re.findall(
                    rf"(?:{re.escape(ticker)}\D{{0,80}})?(?:above|below|over|under|support|resistance|level|watch)\D{{0,30}}(\d{{2,4}}(?:\.\d{{1,2}})?)",
                    joined,
                    flags=re.IGNORECASE,
                )
            )
        )
        contexts.append(
            TickerContext(
                ticker=ticker,
                mentions=mentions,
                bias=bias,
                levels=levels[:5],
            )
        )

    return sorted(contexts, key=lambda item: (-item.mentions, item.ticker))


def format_context_message(video: VideoRef, contexts: list[TickerContext], transcript_available: bool) -> str:
    lines = [
        "Pre-market video context",
        "",
        f"Source: {video.title}",
        f"Link: {video.url}",
    ]
    if video.published:
        lines.append(f"Published: {video.published}")

    lines.extend(
        [
            "",
            "Important: this is context only. Our bot still requires its own strategy confirmation before any trade.",
        ]
    )

    if not transcript_available:
        lines.extend(
            [
                "",
                "Transcript/captions were not available yet, so I could not analyze the video automatically.",
                "Try again later or paste the video link after captions appear.",
            ]
        )
        return "\n".join(lines)

    if not contexts:
        lines.extend(["", "No watchlist tickers were clearly detected in the transcript."])
        return "\n".join(lines)

    lines.extend(["", "Stocks mentioned from our watchlist:"])
    for item in contexts[:12]:
        level_text = f" | levels heard: {', '.join(item.levels)}" if item.levels else ""
        lines.append(f"- {item.ticker}: {item.bias}, {item.mentions} mention(s){level_text}")

    lines.extend(
        [
            "",
            "How to use this:",
            "- If a bot alert matches this prep, treat it as aligned context.",
            "- If price is already far from the bot entry, do not chase.",
        ]
    )
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="Summarize JdubTrades pre-market YouTube context.")
    parser.add_argument("--channel-url", default=DEFAULT_CHANNEL_URL)
    parser.add_argument("--video-url", default=None, help="Optional direct YouTube video URL.")
    parser.add_argument("--watchlist", default=DEFAULT_WATCHLIST)
    parser.add_argument("--discord", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    video = None
    if args.video_url:
        video_id = parse_video_id(args.video_url)
        if not video_id:
            print("REJECTED: INVALID_VIDEO_URL")
            return
        video = VideoRef(
            video_id=video_id,
            title=get_video_title(video_id),
            url=f"https://www.youtube.com/watch?v={video_id}",
        )
    else:
        video = latest_video_from_channel(args.channel_url)

    if video is None:
        message = (
            "Pre-market video context\n\n"
            "Could not find the latest JdubTrades video/stream from YouTube.\n"
            "Try again later or run with --video-url."
        )
        print(message)
        notify_discord(
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
            message=message,
            enabled=args.discord,
        )
        return

    transcript = fetch_transcript(video.video_id)
    contexts = extract_ticker_context(transcript, parse_watchlist(args.watchlist)) if transcript else []
    message = format_context_message(
        video=video,
        contexts=contexts,
        transcript_available=transcript is not None,
    )
    print(message)
    notify_discord(
        webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        message=message,
        enabled=args.discord,
    )


if __name__ == "__main__":
    main()
