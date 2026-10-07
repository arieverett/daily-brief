import asyncio
import base64
import json
from datetime import UTC, datetime

import httpx

from briefing.collect import (
    _select_candidates,
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
    widen_google_news_lookback,
)
from briefing.models import Candidate


def candidate(title: str, source: str = "Reuters", country: str = "Sweden") -> Candidate:
    return Candidate(
        country=country,
        title=title,
        url=f"https://example.com/{country}/{len(title)}/{source}",
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


def test_deduplicate_does_not_drop_same_headline_from_other_country():
    title = "Regional airline announces new route"
    items = [
        candidate(title, country="Sweden"),
        candidate(title, country="Indonesia"),
    ]
    assert len(deduplicate(items)) == 2


def test_candidate_selection_splits_limit_fairly_across_countries():
    items = [
        *(candidate(f"Sweden story {index}", country="Sweden") for index in range(5)),
        *(candidate(f"Indonesia story {index}", country="Indonesia") for index in range(5)),
    ]
    selected = _select_candidates(items, 4)
    assert sum(item.country == "Sweden" for item in selected) == 2
    assert sum(item.country == "Indonesia" for item in selected) == 2


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


def test_google_news_fallback_widens_feed_window():
    url = (
        "https://news.google.com/rss/search?"
        "q=Sweden%20when%3A2d&hl=en-US&gl=US&ceid=US%3Aen"
    )
    assert widen_google_news_lookback(url, 36) == url
    assert "when%3A7d" in widen_google_news_lookback(url, 168)
    assert "when%3A30d" in widen_google_news_lookback(url, 720)

    direct_feed = "https://en.antaranews.com/rss/latest-news.xml"
    assert widen_google_news_lookback(direct_feed, 720) == direct_feed


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


def test_article_excerpt_keeps_body_paragraphs_and_skips_chrome():
    from briefing.collect import extract_article_excerpt

    page = """
    <html><head><meta property="og:description" content="Short teaser."></head><body>
    <nav><p>Home News Sport Weather and a long navigation line that should never be kept at all.</p></nav>
    <article>
      <p>By Staff</p>
      <p>The Royal Swedish Academy of Sciences awarded the prize to two chemists for work on
      molecular chirality, the property that makes some molecules mirror images of each other.</p>
      <p>The laureates will share the 11 million Swedish crown award, the academy said on Wednesday,
      and will receive it at a ceremony in Stockholm on December 10.</p>
    </article>
    <footer><p>Copyright notice that is long enough to pass the length filter if not skipped.</p></footer>
    </body></html>
    """
    excerpt = extract_article_excerpt(page)
    assert excerpt.startswith("The Royal Swedish Academy")
    assert "December 10" in excerpt
    assert "navigation" not in excerpt and "Copyright" not in excerpt and "By Staff" not in excerpt


def test_article_excerpt_falls_back_to_meta_description():
    from briefing.collect import extract_article_excerpt

    page = '<meta name="description" content="Police closed the E4 near Uppsala after a crash.">'
    assert extract_article_excerpt(page) == "Police closed the E4 near Uppsala after a crash."


def test_prompt_dict_includes_excerpt_only_when_present():
    from briefing.models import Candidate

    plain = Candidate(country="Sweden", title="T", url="https://e.com", source="S")
    assert "article_excerpt" not in plain.prompt_dict()
    rich = Candidate(country="Sweden", title="T", url="https://e.com", source="S", excerpt="Body.")
    assert rich.prompt_dict()["article_excerpt"] == "Body."
