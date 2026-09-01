from __future__ import annotations

import asyncio
import html
import re
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx
import yaml
from dateutil import parser as date_parser

from .models import Candidate

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^a-z0-9 ]+")
PUBLISHER_SUFFIX_RE = re.compile(r"\s+-\s+([^-]{2,80})$")


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
    timeout = httpx.Timeout(20.0, connect=10.0)
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
