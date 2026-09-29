"""Direct publisher feeds back up Google News, and feed failures are visible."""

import asyncio
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from briefing import collect, feedcheck
from briefing.collect import (
    FeedResult,
    _fetch_one,
    _top_up_from_backup,
    parse_feed,
    pool_from_results,
)
from briefing.models import Candidate

NOW = datetime.now(UTC)


def item(title: str, source: str, country: str = "Sweden", hours_ago: float = 1) -> Candidate:
    return Candidate(
        country=country,
        title=title,
        url=f"https://example.com/{source}/{abs(hash(title))}",
        source=source,
        published_at=NOW - timedelta(hours=hours_ago),
    )


WORDS = ["harbour", "budget", "election", "rail", "school", "energy", "court", "housing",
         "forest", "bank", "hospital", "tariff", "museum", "airport", "wolf", "bridge", "police",
         "festival", "mine", "farm", "strike", "army", "island", "vaccine", "startup", "river"]


def distinct(prefix: str, count: int, source: str, country: str = "Sweden") -> list[Candidate]:
    """Headlines different enough that deduplication keeps all of them."""
    rng = random.Random(prefix)
    return [
        item(" ".join(rng.sample(WORDS, 6)), source, country, hours_ago=i)
        for i in range(count)
    ]


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>SVT</title>
<item><title>Riksbanken sänker räntan</title><link>https://www.svt.se/a</link>
<pubDate>{recent}</pubDate></item>
<item><title>Gammal nyhet</title><link>https://www.svt.se/b</link>
<pubDate>Mon, 01 Jan 2024 08:00:00 +0000</pubDate></item>
</channel></rss>"""


def test_direct_feed_uses_its_source_label():
    feed = {"name": "SVT Nyheter", "source": "SVT Nyheter", "country": "Sweden"}
    payload = RSS.format(recent=NOW.strftime("%a, %d %b %Y %H:%M:%S +0000")).encode()
    parsed = parse_feed(payload, feed, NOW - timedelta(hours=36))
    assert [c.title for c in parsed] == ["Riksbanken sänker räntan"]
    assert parsed[0].source == "SVT Nyheter"


def test_backup_is_ignored_when_primary_pool_is_healthy():
    primary = distinct("Primary", 15, "Reuters")
    backup = distinct("Backup", 10, "SVT Nyheter")
    assert _top_up_from_backup(primary, backup, 40) == primary


def test_backup_tops_a_thin_country_up_to_the_target_only():
    primary = distinct("Primary", 3, "Reuters") + distinct("Indo", 15, "ANTARA", "Indonesia")
    backup = distinct("Backup", 20, "SVT Nyheter") + distinct("Backup2", 20, "Dagens Nyheter")
    pool = _top_up_from_backup(primary, backup, 40)
    sweden = [c for c in pool if c.country == "Sweden"]
    assert len(sweden) == collect.BACKUP_FILL_TARGET
    assert sum(c.country == "Indonesia" for c in pool) == 15  # untouched
    # The per-source cap keeps one publisher from filling the whole top-up.
    assert sum(c.source == "SVT Nyheter" for c in sweden) <= collect.SOURCE_STORY_CAP


def test_backup_skips_stories_primary_already_has():
    primary = [item("Sweden raises its defence budget to record high", "Reuters")]
    backup = [
        item("Sweden raises its defence budget to a record high", "The Local Sweden"),
        item("Stockholm opens new metro line", "The Local Sweden"),
    ]
    pool = _top_up_from_backup(primary, backup, 40)
    assert [c.title for c in pool] == [
        "Sweden raises its defence budget to record high",
        "Stockholm opens new metro line",
    ]


def test_google_outage_is_covered_by_backup_feeds():
    results = [
        FeedResult("Google News — Sweden", "Sweden", "primary", error="ConnectError: blocked"),
        FeedResult("SVT Nyheter", "Sweden", "backup", entries=20,
                   candidates=distinct("Backup", 20, "SVT Nyheter")),
        FeedResult("Dagens Nyheter", "Sweden", "backup", entries=20,
                   candidates=distinct("DN", 20, "Dagens Nyheter")),
    ]
    pool = pool_from_results(results, 40)
    assert len(pool) == collect.BACKUP_FILL_TARGET


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_failed_feed_is_reported_not_silently_dropped():
    async def run():
        async with _client(lambda request: httpx.Response(404, request=request)) as client:
            return await _fetch_one(
                client,
                {"name": "ANTARA — Latest", "country": "Indonesia", "url": "https://x.test/a.xml"},
                NOW - timedelta(hours=36),
                36,
            )

    result = asyncio.run(run())
    assert not result.ok
    assert "404" in result.error and "\n" not in result.error
    assert result.tier == "primary"


def test_working_feed_reports_entries_and_newest_item():
    body = RSS.format(recent=NOW.strftime("%a, %d %b %Y %H:%M:%S +0000"))

    async def run():
        async with _client(lambda request: httpx.Response(200, text=body)) as client:
            return await _fetch_one(
                client,
                {"name": "SVT Nyheter", "country": "Sweden", "tier": "backup",
                 "url": "https://x.test/rss.xml"},
                NOW - timedelta(hours=36),
                36,
            )

    result = asyncio.run(run())
    assert result.ok and result.tier == "backup"
    assert result.entries == 2 and len(result.candidates) == 1
    assert result.newest is not None and result.newest.year == NOW.year


def test_feedcheck_flags_a_country_that_cannot_publish():
    results = [
        FeedResult("Google News — Sweden", "Sweden", "primary", entries=5,
                   candidates=distinct("P", 3, "Reuters")),
        FeedResult("SVT Nyheter", "Sweden", "backup", error="HTTPStatusError: 403"),
    ]
    lines, warnings, errors = feedcheck.build_report(Path("config/sources.yml"), results, 36, 40)
    assert any("Sweden: 3 fresh from primary, 0 from backup, 3 in the editor's pool" in line
               for line in lines)
    assert warnings == ["config/sources.yml: SVT Nyheter - HTTPStatusError: 403"]
    assert errors and "only 3 Sweden candidates" in errors[0]


def test_every_configured_feed_is_well_formed():
    import yaml

    for path in ("config/sources.yml", "config/indonesia_sources.yml"):
        feeds = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["feeds"]
        for feed in feeds:
            assert {"name", "country", "url"} <= feed.keys(), feed
            assert feed.get("tier", "primary") in {"primary", "backup"}, feed
            if feed.get("tier") == "backup":
                assert feed.get("source"), f"backup feed needs a source label: {feed['name']}"
        assert any(feed.get("tier") == "backup" for feed in feeds), path
