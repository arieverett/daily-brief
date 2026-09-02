import asyncio
import base64
import json
from datetime import UTC, datetime

import httpx

from briefing.collect import (
    decode_legacy_google_news_id,
    deduplicate,
    extract_article_image,
    extract_image_url,
    google_news_article_id,
    normalized_title,
    parse_google_batch_response,
    resolve_google_news_url,
    source_allowed,
    split_google_title,
)
from briefing.models import Candidate


def candidate(title: str, source: str = "Reuters") -> Candidate:
    return Candidate(
        country="Sweden",
        title=title,
        url=f"https://example.com/{len(title)}",
        source=source,
        published_at=datetime.now(UTC),
    )


def test_title_normalization_removes_noise():
    assert normalized_title("The Economy: A View of Sweden") == "economy view sweden"


def test_google_title_extracts_publisher():
    assert split_google_title("Sweden raises forecast - Reuters", "Google News — Sweden") == (
        "Sweden raises forecast",
        "Reuters",
    )


def test_source_allowlist_is_case_insensitive_and_supports_suffixes():
    assert source_allowed("Reuters", ["reuters"])
    assert source_allowed("SVT Nyheter Stockholm", ["SVT Nyheter"])
    assert not source_allowed("Random Blog", ["Reuters", "SVT Nyheter"])


def test_deduplicate_near_identical_titles():
    items = [
        candidate("Sweden raises its economic growth forecast"),
        candidate("Sweden raises economic growth forecast", "AP"),
        candidate("Stockholm opens a new metro station"),
    ]
    result = deduplicate(items)
    assert len(result) == 2


def test_extract_image_from_summary_markup():
    entry = {"summary": '<p>Story</p><img src="https://example.com/news.jpg">'}
    assert extract_image_url(entry) == "https://example.com/news.jpg"


def test_extract_publisher_main_image():
    page = '<meta property="og:image" content="https://example.com/main.jpg">'
    assert extract_article_image(page) == "https://example.com/main.jpg"


def test_extract_image_survives_meta_charset_and_prefers_og():
    page = (
        '<meta charset="utf-8">'
        '<meta name="twitter:image" content="https://example.com/twitter.jpg">'
        '<meta property="og:image" content="https://example.com/hero.jpg?w=1200&amp;h=630">'
    )
    assert extract_article_image(page) == "https://example.com/hero.jpg?w=1200&h=630"


def test_extract_image_rejects_google_placeholder():
    page = '<meta property="og:image" content="https://lh3.googleusercontent.com/logo.png">'
    assert extract_article_image(page) == ""


def test_google_news_article_id():
    assert google_news_article_id("https://news.google.com/rss/articles/CBMiAbc?oc=5") == "CBMiAbc"
    assert google_news_article_id("https://news.google.com/read/CBMiAbc") == "CBMiAbc"
    assert google_news_article_id("https://example.com/story") == ""


def test_decode_legacy_google_news_id():
    payload = b"\x08\x13\x22.https://example.com/2026/09/story.html\xd2\x01\x00"
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    assert decode_legacy_google_news_id(encoded) == "https://example.com/2026/09/story.html"
    assert decode_legacy_google_news_id("AU_yqNotAUrl") == ""


def test_parse_google_batch_response_ignores_prefix_lines():
    inner = json.dumps(["garturlres", "https://publisher.example/story"])
    batch = json.dumps([["wrb.fr", "Fbv4je", inner]])
    assert parse_google_batch_response(")]}'\n123\n" + batch) == "https://publisher.example/story"


def test_resolve_google_news_url_round_trip():
    article_id = "AU_yqExample"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text='data-n-a-ts="123" data-n-a-sg="signature"')
        inner = json.dumps(["garturlres", "https://publisher.example/story"])
        batch = json.dumps([["wrb.fr", "Fbv4je", inner]])
        return httpx.Response(200, text=")]}'\n456\n" + batch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await resolve_google_news_url(
                client, f"https://news.google.com/rss/articles/{article_id}"
            )

    assert asyncio.run(run()) == "https://publisher.example/story"
