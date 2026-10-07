"""Feed collection, candidate deduplication, URL resolution, and image enrichment."""

from __future__ import annotations

import asyncio
import base64
import binascii
import html
import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx
import yaml
from dateutil import parser as date_parser

from .models import Candidate, CountrySection, Edition, IndonesiaEdition, Story

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^a-z0-9 ]+")
PUBLISHER_SUFFIX_RE = re.compile(r"\s+-\s+([^-]{2,80})$")
IMAGE_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
GOOGLE_SIGNATURE_RE = re.compile(r'data-n-a-sg="([^"]+)"')
GOOGLE_TIMESTAMP_RE = re.compile(r'data-n-a-ts="([^"]+)"')
GOOGLE_WHEN_RE = re.compile(r"when(?::|%3A)(\d+)d", re.IGNORECASE)
DECODED_URL_RE = re.compile(rb'https?://[^\x00-\x20"\\]+')

COUNTRY_ORDER = ("Sweden", "Indonesia")
SOURCE_STORY_CAP = 8
IMAGE_CONCURRENCY = 4
# Feeds marked `tier: backup` (direct publisher RSS) only top a country up to this many
# candidates when the primary Google News searches come back thin or fail.
BACKUP_FILL_TARGET = 12
# Article text gives the editor real detail to write from beyond the RSS headline and blurb.
EXCERPT_MAX_CHARS = 1500
EXCERPT_CONCURRENCY = 8
ARTICLE_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "nav", "header", "footer", "aside", "figure", "form"}
)

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

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class SocialImageParser(HTMLParser):
    """Extract a publisher's Open Graph image, falling back to Twitter metadata."""

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


class ArticleTextParser(HTMLParser):
    """Pull the meta description and body paragraphs out of a publisher article page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.description = ""
        self.paragraphs: list[str] = []
        self._skip_depth = 0
        self._in_paragraph = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "meta" and not self.description:
            values = {key.casefold(): value or "" for key, value in attrs}
            key = (values.get("property") or values.get("name") or "").casefold()
            if key in {"og:description", "description", "twitter:description"}:
                self.description = clean_text(values.get("content", ""))
        elif tag in ARTICLE_SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "p" and not self._skip_depth:
            self._in_paragraph = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in ARTICLE_SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "p" and self._in_paragraph:
            self._in_paragraph = False
            text = clean_text(" ".join(self._buffer))
            # Real article prose: a sentence or more, not a byline, caption or button.
            if len(text) >= 80 and text not in self.paragraphs:
                self.paragraphs.append(text)

    def handle_data(self, data: str) -> None:
        if self._in_paragraph and not self._skip_depth:
            self._buffer.append(data)


def extract_article_excerpt(page_html: str, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """Return the article's opening paragraphs (or its meta description) as plain text."""
    parser = ArticleTextParser()
    try:
        parser.feed(page_html)
    except (AssertionError, ValueError):
        pass

    excerpt = ""
    for paragraph in parser.paragraphs:
        if len(excerpt) + len(paragraph) + 1 > max_chars:
            break
        excerpt = f"{excerpt} {paragraph}".strip()
    if len(excerpt) < 200 and parser.description and parser.description not in excerpt:
        excerpt = f"{parser.description} {excerpt}".strip()
    return excerpt[:max_chars]


def clean_text(value: str) -> str:
    value = TAG_RE.sub(" ", html.unescape(value or ""))
    return SPACE_RE.sub(" ", value).strip()


def normalized_title(value: str) -> str:
    value = NON_WORD_RE.sub(" ", value.lower())
    stopwords = {"a", "an", "and", "in", "of", "on", "the", "to", "for", "with"}
    return " ".join(word for word in value.split() if word not in stopwords)


def split_google_title(title: str, feed_name: str) -> tuple[str, str]:
    """Strip Google News' trailing publisher name and return it as the source.

    Direct publisher feeds keep their title and use the feed's source label.
    """
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
    """Read an RSS-provided image before making any publisher-page request."""
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


def is_placeholder_image(url: str) -> bool:
    if not url:
        return True
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return host in PLACEHOLDER_IMAGE_HOSTS or parsed.path.casefold().endswith(".svg")


def extract_article_image(page_html: str) -> str:
    parser = SocialImageParser()
    try:
        parser.feed(page_html)
    except Exception:  # noqa: BLE001 - publisher HTML is untrusted and best-effort.
        return ""
    return "" if is_placeholder_image(parser.image_url) else parser.image_url


# Google News RSS links frequently point to an opaque Google URL rather than the publisher.
def google_news_article_id(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.hostname or "").casefold() != "news.google.com":
        return ""
    segments = [part for part in parsed.path.split("/") if part]
    if len(segments) >= 2 and segments[-2] in {"articles", "read"}:
        return segments[-1]
    return ""


def widen_google_news_lookback(url: str, lookback_hours: int) -> str:
    """Widen Google News when-filters during fallback collection passes."""
    if (urlparse(url).hostname or "").casefold() != "news.google.com":
        return url

    requested_days = max(1, (lookback_hours + 23) // 24)

    def replace_window(match: re.Match[str]) -> str:
        current_days = int(match.group(1))
        if requested_days <= current_days:
            return match.group(0)
        separator = "%3A" if "%3A" in match.group(0).upper() else ":"
        return f"when{separator}{requested_days}d"

    return GOOGLE_WHEN_RE.sub(replace_window, url)


def decode_legacy_google_news_id(article_id: str) -> str:
    """Resolve older Google News IDs that still embed the publisher URL."""
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
    """Best-effort conversion of an opaque Google News RSS URL to the publisher URL."""
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
        request_data = [[["Fbv4je", request_payload, None, "generic"]]]
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
        title, source = split_google_title(raw_title, feed.get("source") or feed["name"])
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
    """Collapse near-identical headlines within a country, newest coverage first."""
    kept: list[Candidate] = []
    fingerprints_by_country: dict[str, list[str]] = {}
    newest_first = sorted(
        candidates,
        key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )

    for candidate in newest_first:
        fingerprint = normalized_title(candidate.title)
        fingerprints = fingerprints_by_country.setdefault(candidate.country, [])
        duplicate = any(
            fingerprint == existing or SequenceMatcher(None, fingerprint, existing).ratio() >= 0.86
            for existing in fingerprints
        )
        if duplicate:
            continue
        kept.append(candidate)
        fingerprints.append(fingerprint)
    return kept


@dataclass
class FeedResult:
    """What one feed returned, kept so failures are visible instead of silent."""

    name: str
    country: str
    tier: str
    entries: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    error: str = ""
    newest: datetime | None = None

    @property
    def ok(self) -> bool:
        return not self.error


def feed_tier(feed: dict) -> str:
    return "backup" if feed.get("tier") == "backup" else "primary"


async def _fetch_one(
    client: httpx.AsyncClient,
    feed: dict,
    cutoff: datetime,
    lookback_hours: int,
) -> FeedResult:
    result = FeedResult(name=feed["name"], country=feed["country"], tier=feed_tier(feed))
    try:
        response = await client.get(widen_google_news_lookback(feed["url"], lookback_hours))
        response.raise_for_status()
        entries = feedparser.parse(response.content).entries
        result.entries = len(entries)
        dates = [date for date in (parse_date(entry) for entry in entries) if date]
        result.newest = max(dates, default=None)
        result.candidates = parse_feed(response.content, feed, cutoff)
        if result.entries == 0:
            result.error = "feed returned no entries"
    except (httpx.HTTPError, UnicodeError) as exc:
        # One bad publisher/feed should never sink the daily edition, but it should
        # show up in the run log.
        first_line = (str(exc).splitlines() or [""])[0]
        result.error = f"{type(exc).__name__}: {first_line}"[:160]
    return result


async def fetch_feeds(feeds: list[dict], lookback_hours: int) -> list[FeedResult]:
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    timeout = httpx.Timeout(15.0, connect=5.0)
    headers = {"User-Agent": "DailyBrief/1.0 (+personal-newsletter)"}
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        return list(
            await asyncio.gather(
                *(_fetch_one(client, feed, cutoff, lookback_hours) for feed in feeds)
            )
        )


def report_feed_health(results: list[FeedResult], lookback_hours: int) -> None:
    """Print which feeds failed and how many stories each tier contributed."""
    working = sum(result.ok for result in results)
    print(f"  Feeds working: {working}/{len(results)} (lookback {lookback_hours}h)", flush=True)
    for result in results:
        if not result.ok:
            print(f"  ! {result.name}: {result.error}", flush=True)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [
        f"### Feeds ({lookback_hours}h lookback): {working}/{len(results)} working",
        "",
        "| Feed | Tier | Entries | Usable | Status |",
        "|---|---|---:|---:|---|",
    ]
    for result in results:
        status = "ok" if result.ok else result.error.replace("|", "/")
        lines.append(
            f"| {result.name} | {result.tier} | {result.entries} | "
            f"{len(result.candidates)} | {status} |"
        )
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n\n")


def _ordered_countries(candidates: list[Candidate]) -> list[str]:
    available = {candidate.country for candidate in candidates}
    ordered = [country for country in COUNTRY_ORDER if country in available]
    ordered.extend(sorted(available - set(ordered)))
    return ordered


def _select_candidates(candidates: list[Candidate], max_candidates: int) -> list[Candidate]:
    """Apply a per-source cap and split the global limit fairly across countries."""
    countries = _ordered_countries(candidates)
    if not countries or max_candidates <= 0:
        return []

    base_quota, remainder = divmod(max_candidates, len(countries))
    selected: list[Candidate] = []

    for index, country in enumerate(countries):
        quota = base_quota + (1 if index < remainder else 0)
        source_counts: dict[str, int] = {}
        country_selected = 0
        for candidate in (item for item in candidates if item.country == country):
            source_key = candidate.source.casefold()
            if source_counts.get(source_key, 0) >= SOURCE_STORY_CAP:
                continue
            selected.append(candidate)
            source_counts[source_key] = source_counts.get(source_key, 0) + 1
            country_selected += 1
            if country_selected >= quota:
                break
    return selected


def _top_up_from_backup(
    selected: list[Candidate],
    backup: list[Candidate],
    max_candidates: int,
    target: int = BACKUP_FILL_TARGET,
) -> list[Candidate]:
    """Add direct-publisher stories to countries whose primary pool is thin."""
    countries = [
        country
        for country in COUNTRY_ORDER
        if any(item.country == country for item in [*selected, *backup])
    ]
    if not countries:
        return selected
    per_country_cap = max(1, max_candidates // len(countries))
    fill_to = min(target, per_country_cap)

    result = list(selected)
    for country in countries:
        chosen = [item for item in result if item.country == country]
        if len(chosen) >= fill_to:
            continue
        fingerprints = [normalized_title(item.title) for item in chosen]
        source_counts: dict[str, int] = {}
        for item in chosen:
            key = item.source.casefold()
            source_counts[key] = source_counts.get(key, 0) + 1
        for candidate in (item for item in backup if item.country == country):
            if len(chosen) >= fill_to:
                break
            fingerprint = normalized_title(candidate.title)
            if any(
                fingerprint == existing
                or SequenceMatcher(None, fingerprint, existing).ratio() >= 0.86
                for existing in fingerprints
            ):
                continue
            key = candidate.source.casefold()
            if source_counts.get(key, 0) >= SOURCE_STORY_CAP:
                continue
            chosen.append(candidate)
            result.append(candidate)
            fingerprints.append(fingerprint)
            source_counts[key] = source_counts.get(key, 0) + 1
    return result


def pool_from_results(results: list[FeedResult], max_candidates: int) -> list[Candidate]:
    primary = [c for r in results if r.tier == "primary" for c in r.candidates]
    backup = [c for r in results if r.tier == "backup" for c in r.candidates]
    selected = _select_candidates(deduplicate(primary), max_candidates)
    return _top_up_from_backup(selected, deduplicate(backup), max_candidates)


async def collect_candidates(
    sources_path: Path,
    lookback_hours: int,
    max_candidates: int,
) -> list[Candidate]:
    config = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    results = await fetch_feeds(config.get("feeds", []), lookback_hours)
    report_feed_health(results, lookback_hours)
    pool = pool_from_results(results, max_candidates)
    primary_urls = {c.url for r in results if r.tier == "primary" for c in r.candidates}
    from_backup = sum(item.url not in primary_urls for item in pool)
    if from_backup:
        print(f"  Topped up thin pools with {from_backup} direct-publisher stories", flush=True)
    return pool


def _fallback_images(candidates: list[Candidate]) -> dict[str, str]:
    return {
        candidate.url: candidate.image_url
        for candidate in candidates
        if candidate.image_url and not is_placeholder_image(candidate.image_url)
    }


async def _discover_story_images(
    stories: list[Story],
    candidates: list[Candidate],
    image_budget_seconds: float,
) -> dict[str, str]:
    """Fetch publisher images concurrently and fall back to RSS-provided images."""
    if not stories:
        return {}

    fallback_by_url = _fallback_images(candidates)
    timeout = httpx.Timeout(10.0, connect=5.0)
    semaphore = asyncio.Semaphore(IMAGE_CONCURRENCY)

    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        timeout=timeout,
        follow_redirects=True,
    ) as client:

        async def fetch_image(story: Story) -> str:
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

        try:
            images = await asyncio.wait_for(
                asyncio.gather(*(fetch_image(story) for story in stories)),
                timeout=image_budget_seconds,
            )
        except TimeoutError:
            print(
                f"  Image lookup exceeded {image_budget_seconds:.0f}s, "
                "sending with RSS images only",
                flush=True,
            )
            images = [fallback_by_url.get(story.url, "") for story in stories]

    return dict(zip((story.url for story in stories), images, strict=True))


def _choose_images(
    stories: list[Story],
    discovered: dict[str, str],
    limit: int,
) -> dict[str, str]:
    selected: dict[str, str] = {}
    for story in stories:
        image_url = discovered.get(story.url, "")
        if image_url:
            selected[story.url] = image_url
        if len(selected) >= limit:
            break
    return selected


def _decorate_section(section: CountrySection, image_by_url: dict[str, str]) -> CountrySection:
    def decorate(story: Story) -> Story:
        return replace(story, image_url=image_by_url.get(story.url, ""))

    return replace(
        section,
        lead=decorate(section.lead),
        stories=[decorate(story) for story in section.stories],
    )


async def add_article_images(
    edition: Edition,
    candidates: list[Candidate],
    limit: int = 4,
    image_budget_seconds: float = 30.0,
) -> Edition:
    sweden_stories = [edition.sweden.lead, *edition.sweden.stories]
    indonesia_stories = [edition.indonesia.lead, *edition.indonesia.stories]
    all_stories = [*sweden_stories, *indonesia_stories]
    discovered = await _discover_story_images(all_stories, candidates, image_budget_seconds)

    sweden_limit = max(1, limit // 2)
    image_by_url = _choose_images(sweden_stories, discovered, sweden_limit)
    image_by_url.update(
        _choose_images(indonesia_stories, discovered, max(0, limit - sweden_limit))
    )

    return replace(
        edition,
        sweden=_decorate_section(edition.sweden, image_by_url),
        indonesia=_decorate_section(edition.indonesia, image_by_url),
    )


async def add_indonesia_article_images(
    edition: IndonesiaEdition,
    candidates: list[Candidate],
    limit: int = 4,
    image_budget_seconds: float = 30.0,
) -> IndonesiaEdition:
    stories = [edition.indonesia.lead, *edition.indonesia.stories]
    discovered = await _discover_story_images(stories, candidates, image_budget_seconds)
    image_by_url = _choose_images(stories, discovered, limit)
    return replace(
        edition,
        indonesia=_decorate_section(edition.indonesia, image_by_url),
    )


async def add_article_excerpts(
    candidates: list[Candidate],
    budget_seconds: float = 60.0,
) -> list[Candidate]:
    """Attach each article's opening paragraphs, best effort and within a time budget.

    Paywalled or bot-blocking publishers simply keep their RSS summary only. Whatever
    finished before the budget runs out is kept.
    """
    if not candidates:
        return candidates

    timeout = httpx.Timeout(10.0, connect=5.0)
    semaphore = asyncio.Semaphore(EXCERPT_CONCURRENCY)
    excerpts: dict[int, str] = {}

    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        timeout=timeout,
        follow_redirects=True,
    ) as client:

        async def fetch_excerpt(index: int, candidate: Candidate) -> None:
            try:
                async with semaphore:
                    article_url = await resolve_google_news_url(client, candidate.url)
                    if google_news_article_id(article_url):
                        return
                    response = await client.get(article_url)
                    response.raise_for_status()
                excerpt = extract_article_excerpt(response.text)
                if excerpt:
                    excerpts[index] = excerpt
            except (httpx.HTTPError, UnicodeError, ValueError):
                return

        tasks = [
            asyncio.create_task(fetch_excerpt(index, candidate))
            for index, candidate in enumerate(candidates)
        ]
        _, pending = await asyncio.wait(tasks, timeout=budget_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    return [
        replace(candidate, excerpt=excerpts[index]) if index in excerpts else candidate
        for index, candidate in enumerate(candidates)
    ]
