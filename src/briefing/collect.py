from __future__ import annotations

import asyncio
import base64
import binascii
import html
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx
import yaml
from dateutil import parser as date_parser

from .models import Candidate, Edition

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^a-z0-9 ]+")
PUBLISHER_SUFFIX_RE = re.compile(r"\s+-\s+([^-]{2,80})$")
IMAGE_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


class SocialImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.og_image = ""
        self.twitter_image = ""

    @property
    def image_url(self) -> str:
        return self.og_image or self.twitter_image

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta" or self.og_image:
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        image_key = (values.get("property") or values.get("name") or "").casefold()
        if image_key not in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
            return
        url = html.unescape(values.get("content", "").strip())
        if urlparse(url).scheme not in {"http", "https"}:
            return
        if image_key.startswith("og:"):
            self.og_image = url
        elif not self.twitter_image:
            self.twitter_image = url


def clean_text(value: str) -> str:
    value = TAG_RE.sub(" ", html.unescape(value or ""))
    return SPACE_RE.sub(" ", value).strip()


def normalized_title(value: str) -> str:
    value = NON_WORD_RE.sub(" ", value.lower())
    stop = {"a", "an", "and", "in", "of", "on", "the", "to", "for", "with"}
    return " ".join(word for word in value.split() if word not in stop)


def split_google_title(title: str, feed_name: str) -> tuple[str, str]:
    if not feed_name.startswith("Google News"):
        return title, feed_name
    match = PUBLISHER_SUFFIX_RE.search(title)
    if not match:
        return title, feed_name
    return title[: match.start()].strip(), match.group(1).strip()


def source_allowed(source: str, allowed_sources: list[str] | None) -> bool:
    if not allowed_sources:
        return True
    source_key = source.casefold()
    return any(allowed.casefold() in source_key for allowed in allowed_sources)


def parse_date(entry: dict) -> datetime | None:
    value = entry.get("published") or entry.get("updated")
    if not value:
        return None
    try:
        parsed = date_parser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def extract_image_url(entry: dict) -> str:
    for key in ("media_content", "media_thumbnail"):
        for item in entry.get(key, []):
            url = str(item.get("url", "")).strip()
            if urlparse(url).scheme in {"http", "https"}:
                return url
    for item in entry.get("enclosures", []):
        url = str(item.get("href") or item.get("url") or "").strip()
        if str(item.get("type", "")).startswith("image/") and urlparse(url).scheme in {
            "http",
            "https",
        }:
            return url
    raw_summary = entry.get("summary", "") or entry.get("description", "")
    match = IMAGE_RE.search(raw_summary)
    if match and urlparse(match.group(1)).scheme in {"http", "https"}:
        return html.unescape(match.group(1))
    return ""


def extract_article_image(page_html: str) -> str:
    parser = SocialImageParser()
    try:
        parser.feed(page_html)
    except Exception:  # noqa: BLE001 - publisher HTML is untrusted and best-effort
        return ""
    return "" if is_placeholder_image(parser.image_url) else parser.image_url


PLACEHOLDER_IMAGE_HOSTS = {
    "lh3.googleusercontent.com",
    "lh4.googleusercontent.com",
    "lh5.googleusercontent.com",
    "lh6.googleusercontent.com",
    "news.google.com",
    "ssl.gstatic.com",
    "www.google.com",
    "www.gstatic.com",
}
GOOGLE_SIGNATURE_RE = re.compile(r'data-n-a-sg="([^"]+)"')
GOOGLE_TIMESTAMP_RE = re.compile(r'data-n-a-ts="([^"]+)"')
DECODED_URL_RE = re.compile(rb'https?://[^\x00-\x20"\\]+')
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_placeholder_image(url: str) -> bool:
    if not url:
        return True
    host = (urlparse(url).hostname or "").casefold()
    return host in PLACEHOLDER_IMAGE_HOSTS or urlparse(url).path.casefold().endswith(".svg")


def google_news_article_id(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.hostname or "").casefold() != "news.google.com":
        return ""
    segments = [part for part in parsed.path.split("/") if part]
    if len(segments) >= 2 and segments[-2] in {"articles", "read"}:
        return segments[-1]
    return ""


def decode_legacy_google_news_id(article_id: str) -> str:
    try:
        padded = article_id + "=" * (-len(article_id) % 4)
        decoded = base64.urlsafe_b64decode(padded)
    except (ValueError, binascii.Error):
        return ""
    match = DECODED_URL_RE.search(decoded)
    if not match:
        return ""
    url = match.group(0).decode("utf-8", errors="ignore")
    return url if urlparse(url).scheme in {"http", "https"} else ""


def parse_google_batch_response(payload: str) -> str:
    """Extract a publisher URL without depending on Google's response line numbers."""
    for line in payload.splitlines():
        line = line.strip()
        if not line.startswith("[["):
            continue
        try:
            batches = json.loads(line)
            decoded = json.loads(batches[0][2])
            resolved = decoded[1]
        except (IndexError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(resolved, str) and urlparse(resolved).scheme in {"http", "https"}:
            return resolved
    return ""


async def resolve_google_news_url(client: httpx.AsyncClient, url: str) -> str:
    article_id = google_news_article_id(url)
    if not article_id:
        return url
    legacy = decode_legacy_google_news_id(article_id)
    if legacy:
        return legacy
    try:
        page = await client.get(f"https://news.google.com/rss/articles/{article_id}")
        page.raise_for_status()
        signature = GOOGLE_SIGNATURE_RE.search(page.text)
        timestamp = GOOGLE_TIMESTAMP_RE.search(page.text)
        if not signature or not timestamp:
            return url
        request_payload = (
            '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
            'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
            f'"{article_id}",{timestamp.group(1)},"{signature.group(1)}"]'
        )
        request_data = [
            [
                [
                    "Fbv4je",
                    request_payload,
                    None,
                    "generic",
                ]
            ]
        ]
        response = await client.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            data={"f.req": json.dumps(request_data)},
        )
        response.raise_for_status()
        return parse_google_batch_response(response.text) or url
    except (httpx.HTTPError, TypeError, ValueError):
        return url


def parse_feed(payload: bytes, feed: dict, cutoff: datetime) -> list[Candidate]:
    parsed = feedparser.parse(payload)
    candidates: list[Candidate] = []
    for entry in parsed.entries:
        published_at = parse_date(entry)
        if published_at and published_at < cutoff:
            continue
        raw_title = clean_text(entry.get("title", ""))
        title, source = split_google_title(raw_title, feed["name"])
        if not source_allowed(source, feed.get("allowed_sources")):
            continue
        url = entry.get("link", "").strip()
        if not title or not url or urlparse(url).scheme not in {"http", "https"}:
            continue
        candidates.append(
            Candidate(
                country=feed["country"],
                title=title,
                url=url,
                source=source,
                published_at=published_at,
                summary=clean_text(entry.get("summary", "") or entry.get("description", "")),
                image_url=extract_image_url(entry),
            )
        )
    return candidates


def deduplicate(candidates: list[Candidate]) -> list[Candidate]:
    kept: list[Candidate] = []
    fingerprints: list[str] = []
    for candidate in sorted(
        candidates, key=lambda x: x.published_at or datetime.min.replace(tzinfo=UTC), reverse=True
    ):
        fingerprint = normalized_title(candidate.title)
        duplicate = any(
            fingerprint == existing or SequenceMatcher(None, fingerprint, existing).ratio() >= 0.86
            for existing in fingerprints
        )
        if not duplicate:
            kept.append(candidate)
            fingerprints.append(fingerprint)
    return kept


async def _fetch_one(client: httpx.AsyncClient, feed: dict, cutoff: datetime) -> list[Candidate]:
    try:
        response = await client.get(feed["url"])
        response.raise_for_status()
        return parse_feed(response.content, feed, cutoff)
    except (httpx.HTTPError, UnicodeError):
        return []


async def collect_candidates(
    sources_path: Path, lookback_hours: int, max_candidates: int
) -> list[Candidate]:
    config = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    feeds = config.get("feeds", [])
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    headers = {"User-Agent": "DailyBrief/0.1 (+personal-newsletter)"}
    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        batches = await asyncio.gather(*(_fetch_one(client, feed, cutoff) for feed in feeds))
    unique = deduplicate([item for batch in batches for item in batch])
    by_country: list[Candidate] = []
    per_country = max(12, max_candidates // 2)
    for country in ("Sweden", "Indonesia"):
        source_counts: dict[str, int] = {}
        selected: list[Candidate] = []
        for candidate in (x for x in unique if x.country == country):
            key = candidate.source.casefold()
            if source_counts.get(key, 0) >= 8:
                continue
            selected.append(candidate)
            source_counts[key] = source_counts.get(key, 0) + 1
            if len(selected) >= per_country:
                break
        by_country.extend(selected)
    return by_country[:max_candidates]


async def add_article_images(
    edition: Edition, candidates: list[Candidate], limit: int = 4
) -> Edition:
    fallback_by_url = {
        item.url: item.image_url
        for item in candidates
        if item.image_url and not is_placeholder_image(item.image_url)
    }
    per_country = max(1, limit // 2)
    sweden_targets = [edition.sweden.lead, *edition.sweden.stories]
    indonesia_targets = [edition.indonesia.lead, *edition.indonesia.stories]
    targets = [*sweden_targets, *indonesia_targets]
    timeout = httpx.Timeout(10.0, connect=5.0)
    semaphore = asyncio.Semaphore(4)

    async def fetch_image(story) -> str:
        fallback = fallback_by_url.get(story.url, "")
        try:
            async with semaphore:
                article_url = await resolve_google_news_url(client, story.url)
                if google_news_article_id(article_url):
                    return fallback
                response = await client.get(article_url)
                response.raise_for_status()
            return extract_article_image(response.text) or fallback
        except (httpx.HTTPError, UnicodeError, ValueError):
            return fallback

    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS, timeout=timeout, follow_redirects=True
    ) as client:
        images = await asyncio.gather(*(fetch_image(story) for story in targets))
    discovered = dict(zip((story.url for story in targets), images, strict=True))
    image_by_story_url: dict[str, str] = {}
    for stories, allowance in (
        (sweden_targets, per_country),
        (indonesia_targets, limit - per_country),
    ):
        for story in stories:
            image_url = discovered.get(story.url, "")
            if image_url and allowance:
                image_by_story_url[story.url] = image_url
                allowance -= 1

    def decorate(story):
        return replace(story, image_url=image_by_story_url.get(story.url, ""))

    return replace(
        edition,
        sweden=replace(
            edition.sweden,
            lead=decorate(edition.sweden.lead),
            stories=[decorate(story) for story in edition.sweden.stories],
        ),
        indonesia=replace(
            edition.indonesia,
            lead=decorate(edition.indonesia.lead),
            stories=[decorate(story) for story in edition.indonesia.stories],
        ),
    )
