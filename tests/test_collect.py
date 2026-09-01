from datetime import UTC, datetime

from briefing.collect import (
    deduplicate,
    extract_article_image,
    extract_image_url,
    normalized_title,
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
