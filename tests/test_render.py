from dataclasses import replace

from briefing.main import load_sample
from briefing.render import render_html, render_text


def test_html_has_core_sections_and_links():
    html = render_html(load_sample())
    assert "NORDIC + NUSANTARA" in html
    assert "The front page" in html
    assert "Sweden" in html
    assert "Indonesia" in html
    assert "https://www.reuters.com" in html
    assert "5-minute read" not in html
    assert "View online" not in html
    assert "Phoenix" not in html
    assert html.index("The setup") < html.index("The front page")
    assert html.count('class="speed-row"') == 10
    assert "—" not in html


def test_story_heading_precedes_article_image():
    edition = load_sample()
    lead = replace(edition.sweden.lead, image_url="https://example.com/article-main.jpg")
    html = render_html(replace(edition, sweden=replace(edition.sweden, lead=lead)))
    assert html.index(lead.headline) < html.index("https://example.com/article-main.jpg")


def test_plain_text_fallback_has_core_sections():
    text = render_text(load_sample())
    assert "THE FRONT PAGE" in text
    assert "SWEDEN" in text
    assert "INDONESIA" in text
    assert text.index("THE SETUP") < text.index("THE FRONT PAGE")


def test_repair_links_snaps_urls_and_drops_hallucinations():
    from briefing.editor import validate_links
    from briefing.models import Candidate, CountrySection, Edition, Story

    candidates = [
        Candidate(country="Sweden", title="Riksbank holds rates", url="https://a.se/x?oc=5", source="Reuters"),
        Candidate(country="Sweden", title="Metro line opens", url="https://b.se/metro/", source="SVT"),
        Candidate(country="Indonesia", title="Jakarta floods", url="https://c.id/floods", source="AP"),
        Candidate(country="Indonesia", title="Fuel subsidy trimmed", url="https://d.id/fuel", source="AP"),
    ]

    def story(headline, url):
        return Story(headline=headline, summary="s", why_it_matters="w", url=url, source="X", label="NEWS")

    edition = Edition(
        edition_date="2026-09-02", date_label="d", subject="s", preview_text="p",
        front_page=[story("Riksbank holds rates", "https://a.se/x"), story("Invented", "https://nope.example/z")],
        sweden=CountrySection(lead=story("Metro line opens", "https://b.se/metro"), stories=[], quick_hits=[]),
        indonesia=CountrySection(
            lead=story("Jakarta floods", "https://c.id/floods"),
            stories=[story("Fuel subsidy trimmed", "https://d.id/fuel")],
            quick_hits=[],
        ),
        bottom_line="b",
    )

    fixed = validate_links(edition, candidates)
    allowed = {c.url for c in candidates}
    assert len(fixed.front_page) == 1
    assert fixed.front_page[0].url == "https://a.se/x?oc=5"
    assert fixed.sweden.lead.url == "https://b.se/metro/"
    every = [*fixed.front_page, fixed.sweden.lead, fixed.indonesia.lead, *fixed.indonesia.stories]
    assert all(s.url in allowed for s in every)
