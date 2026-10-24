"""The "Today in history" bonus at the end of a regional edition (Two-Mile High).

Events come from Wikipedia's free "On this day" feed (CC BY-SA, credited in the email).
Events from the edition's own states go first, flagged with that state's emoji; the rest
are spread across the centuries and kept short, like a speed read. The text is
Wikipedia's own one-line summary, not rewritten, so nothing can be invented. Best-effort:
any failure just leaves the section out.

    python -m briefing.history config/us_sources.yml [YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import yaml

from .models import HistoryItem

FEED_URLS = (
    "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{month:02d}/{day:02d}",
    "https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month:02d}/{day:02d}",
)
# Wikimedia's User-Agent policy: tool/version (contact URL) library/version. A UA without a
# URL is refused with 403 from cloud networks such as GitHub's runners.
HEADERS = {
    "User-Agent": (
        f"DailyBriefNewsletter/1.0 (https://github.com/arieverett/daily-brief) "
        f"python-httpx/{httpx.__version__}"
    ),
    "Api-User-Agent": "DailyBriefNewsletter/1.0 (https://github.com/arieverett/daily-brief)",
    "Accept": "application/json",
}
TIMEOUT = httpx.Timeout(15.0, connect=10.0)

# Speed-read length: Wikipedia's longer entries are skipped when there are shorter ones.
MAX_WORDS = 40
# A fun bonus: wars and disasters are only used when nothing lighter is left.
GRIM_RE = re.compile(
    r"\b(kill\w*|dead|deaths?|died|massacre\w*|bomb\w*|shot|shooting|murder\w*|execut\w*|"
    r"genocide|crash\w*|explosion|earthquake|attack\w*|invad\w*|invasion|battle|war|"
    r"terror\w*|hostage\w*|assassinat\w*|sinks?|sank|famine|riot\w*)\b",
    re.IGNORECASE,
)
US_RE = re.compile(r"\b(United States|American|U\.S\.|US)\b")


@dataclass(frozen=True)
class LocalTag:
    emoji: str
    terms: list[str]

    def matches(self, text: str) -> bool:
        return any(
            re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) for term in self.terms
        )


@dataclass(frozen=True)
class HistoryConfig:
    count: int = 4
    max_local: int = 2
    local: list[LocalTag] = field(default_factory=list)


@dataclass(frozen=True)
class Event:
    year: int
    text: str
    url: str
    titles: str  # linked page titles, for local matching

    @property
    def words(self) -> int:
        return len(self.text.split())


def load_history_config(sources_path: Path) -> HistoryConfig | None:
    """The optional `history:` block of an edition's sources file."""
    config = yaml.safe_load(Path(sources_path).read_text(encoding="utf-8")) or {}
    raw = config.get("history")
    if not raw:
        return None
    return HistoryConfig(
        count=int(raw.get("count", 4)),
        max_local=int(raw.get("max_local", 2)),
        local=[
            LocalTag(emoji=str(item.get("emoji", "")), terms=[str(t) for t in item["terms"]])
            for item in raw.get("local", [])
        ],
    )


def _clean(text: str) -> str:
    text = " ".join(str(text).split())
    if text and text[-1] not in ".!?":
        text += "."
    return text


def parse_events(payload: dict[str, Any]) -> list[Event]:
    events: list[Event] = []
    for raw in payload.get("events", []):
        try:
            year = int(raw["year"])
        except (KeyError, TypeError, ValueError):
            continue
        text = _clean(raw.get("text", ""))
        pages = raw.get("pages") or []
        url = ""
        for page in pages:
            url = (page.get("content_urls") or {}).get("desktop", {}).get("page", "")
            if url:
                break
        if not text or not url:
            continue
        titles = " ".join(
            (page.get("titles") or {}).get("normalized", "") or page.get("title", "")
            for page in pages
        )
        events.append(Event(year=year, text=text, url=url, titles=titles))
    return events


def fetch_events(day: date, client: httpx.Client | None = None) -> list[Event]:
    """Wikipedia's events for this month/day; tries the second endpoint if the first fails."""
    own = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    errors: list[str] = []
    try:
        for template in FEED_URLS:
            url = template.format(month=day.month, day=day.day)
            try:
                response = client.get(url, headers=HEADERS)
                response.raise_for_status()
                events = parse_events(response.json())
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
                continue
            if events:
                return events
            errors.append(f"{url}: no events")
    finally:
        if own:
            client.close()
    raise RuntimeError("; ".join(errors))


def _local_tag(event: Event, tags: list[LocalTag]) -> LocalTag | None:
    haystack = f"{event.text} {event.titles}"
    return next((tag for tag in tags if tag.matches(haystack)), None)


def pick(events: list[Event], config: HistoryConfig, day: date) -> list[HistoryItem]:
    """Local events first (up to max_local), then a short, light spread across the eras.

    Deterministic per date, so a re-run of the same edition picks the same events.
    """
    rng = random.Random(day.isoformat())
    short = [event for event in events if event.words <= MAX_WORDS] or list(events)

    chosen: list[tuple[Event, str]] = []
    local = [(event, tag) for event in short if (tag := _local_tag(event, config.local))]
    local.sort(key=lambda pair: (bool(GRIM_RE.search(pair[0].text)), -pair[0].year))
    chosen.extend((event, tag.emoji) for event, tag in local[: config.max_local])

    used = {event.url + event.text for event, _ in chosen}
    rest = [event for event in short if event.url + event.text not in used]
    light = [event for event in rest if not GRIM_RE.search(event.text)]
    pool = sorted(light if len(light) >= config.count else rest, key=lambda event: event.year)

    need = max(0, config.count - len(chosen))
    if need and pool:
        # One event from each slice of history, oldest to newest, US events preferred.
        size = len(pool) / need
        for index in range(need):
            bucket = pool[int(index * size): int((index + 1) * size)] or pool[-1:]
            american = [event for event in bucket if US_RE.search(event.text)]
            chosen.append((rng.choice(american or bucket), ""))

    seen: set[str] = set()
    items: list[HistoryItem] = []
    for event, emoji in sorted(chosen, key=lambda pair: pair[0].year):
        key = event.url + event.text
        if key in seen:
            continue
        seen.add(key)
        items.append(HistoryItem(year=event.year, text=event.text, url=event.url, emoji=emoji))
    return items


def build_history(
    config: HistoryConfig, day: date, client: httpx.Client | None = None
) -> list[HistoryItem]:
    return pick(fetch_events(day, client), config, day)


def _annotate(level: str, title: str, message: str) -> None:
    print(message, flush=True)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = message[:3000].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level} title={title}::{escaped}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview the Today in history picks")
    parser.add_argument("sources", type=Path)
    today = datetime.now(ZoneInfo("America/New_York")).date()
    parser.add_argument("date", nargs="?", default=today.isoformat())
    args = parser.parse_args(argv)
    config = load_history_config(args.sources)
    if config is None:
        _annotate("error", "Today in history", f"{args.sources}: no history block")
        return 1
    day = date.fromisoformat(args.date)
    try:
        events = fetch_events(day)
    except Exception as exc:  # noqa: BLE001 - report whatever went wrong on the run page
        _annotate("error", "Today in history", f"{type(exc).__name__}: {exc}")
        return 1
    items = pick(events, config, day)
    lines = [f"{len(events)} Wikipedia events for {day:%B} {day.day}; picked {len(items)}:"]
    lines += [
        f"  {item.emoji + ' ' if item.emoji else ''}{item.year_label}: {item.text} <{item.url}>"
        for item in items
    ]
    _annotate("notice" if items else "error", "Today in history", "\n".join(lines))
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
