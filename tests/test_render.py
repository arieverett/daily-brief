from dataclasses import replace

from briefing.main import load_sample
from briefing.models import IndonesiaEdition
from briefing.render import render_html, render_indonesia_text, render_text


def test_html_has_core_sections_and_links():
    html = render_html(load_sample())
    assert "NORDIC + NUSANTARA" in html
    assert "The front page" not in html
    assert "Sweden" in html
    assert "Indonesia" in html
    assert "https://www.reuters.com" in html
    assert "5-minute read" not in html
    assert "View online" not in html
    assert "Phoenix" not in html
    assert html.index("The setup") < html.index("🇸🇪 Sweden")
    assert "Every item links to its source" not in html
    assert "AI-assisted; verify important details at the source" not in html


def test_html_uses_email_safe_fonts_and_language_metadata():
    html = render_html(load_sample())
    assert '<html lang="en">' in html
    assert "fonts.googleapis.com" not in html
    assert "-webkit-text-size-adjust: 100%" in html


def test_story_heading_precedes_article_image():
    edition = load_sample()
    lead = replace(edition.sweden.lead, image_url="https://example.com/article-main.jpg")
    html = render_html(replace(edition, sweden=replace(edition.sweden, lead=lead)))
    assert html.index(lead.headline) < html.index("https://example.com/article-main.jpg")
    assert f'alt="{lead.headline}"' in html


def test_story_can_render_multiple_sources():
    from briefing.models import SourceLink

    edition = load_sample()
    lead = replace(
        edition.indonesia.lead,
        source_links=[
            SourceLink("Reuters", "https://example.com/reuters-story"),
            SourceLink("AP", "https://example.com/ap-story"),
        ],
    )
    html = render_html(replace(edition, indonesia=replace(edition.indonesia, lead=lead)))
    assert "https://example.com/reuters-story" in html
    assert "https://example.com/ap-story" in html
    assert ">Reuters</a>" in html
    assert ">AP</a>" in html


def test_plain_text_fallback_has_core_sections():
    text = render_text(load_sample())
    assert "THE FRONT PAGE" not in text
    assert "SWEDEN" in text
    assert "INDONESIA" in text
    assert text.index("THE SETUP") < text.index("SWEDEN")


def indonesia_sample() -> IndonesiaEdition:
    standard = load_sample()
    return IndonesiaEdition(
        edition_date=standard.edition_date,
        date_label="Rabu, 2 September",
        subject="Kabar Indonesia hari ini",
        preview_text="Ringkasan berita Indonesia.",
        indonesia=standard.indonesia,
        setup="Kebijakan ekonomi dan budaya menjadi sorotan hari ini.",
    )


def test_indonesia_html_has_approved_branding_and_no_sweden_section():
    html = render_html(indonesia_sample())
    assert '<html lang="id">' in html
    assert "NUSANTARA DAILY" in html
    assert "Dalam edisi hari ini" in html
    assert "Baca kilat" in html
    assert "Curated for Mom by Ari &lt;3" in html
    assert "#991b1b" in html
    assert "🇸🇪" not in html


def test_indonesia_plain_text_is_localized():
    text = render_indonesia_text(indonesia_sample())
    assert "DALAM EDISI HARI INI" in text
    assert "BACA KILAT" in text
    assert "Mengapa penting:" not in text


def test_indonesia_subject_has_stable_forwarding_prefix():
    from briefing.editorial import prefix_indonesia_subject

    assert prefix_indonesia_subject("Kabar pagi") == "Nusantara Daily: Kabar pagi"
    assert prefix_indonesia_subject("Nusantara Daily: Kabar pagi").count("Nusantara Daily:") == 1
    assert len(prefix_indonesia_subject("x" * 100)) == 70


def test_editorial_cleanup_drops_fragments_and_summary_repeats():
    from briefing.editorial import clean_highlights
    from briefing.models import Story

    story = Story(
        headline="Volcano disrupts travel",
        summary="Authorities closed 8 airports because ash spread across western Indonesia.",
        url="https://example.com/a",
        source="Example",
        label="TRANSPORT",
        highlights=[
            "8 airports closed",
            "Authorities closed 8 airports because ash spread across western Indonesia.",
            "Rail operator KAI added an extra train between Semarang and Jakarta.",
        ],
    )
    assert clean_highlights(story) == [
        "Rail operator KAI added an extra train between Semarang and Jakarta."
    ]


def test_validate_edition_snaps_urls_and_drops_hallucinations():
    from briefing.editorial import validate_edition
    from briefing.models import Candidate, CountrySection, Edition, Story

    candidates = [
        Candidate("Sweden", "Riksbank holds rates", "https://a.se/x?oc=5", "Reuters"),
        Candidate("Sweden", "Metro line opens", "https://b.se/metro/", "SVT"),
        Candidate("Sweden", "New rail timetable", "https://e.se/rail", "SVT"),
        Candidate("Indonesia", "Jakarta floods", "https://c.id/floods", "AP"),
        Candidate("Indonesia", "Fuel subsidy trimmed", "https://d.id/fuel", "AP"),
        Candidate("Indonesia", "New commuter line", "https://f.id/train", "ANTARA"),
    ]

    def story(headline: str, url: str) -> Story:
        return Story(headline, "A complete summary sentence.", url, "X", "NEWS")

    edition = Edition(
        edition_date="2026-09-02",
        date_label="Wednesday, September 2",
        subject="Daily brief",
        preview_text="Preview",
        sweden=CountrySection(
            story("Riksbank holds rates", "https://a.se/x"),
            [story("Invented", "https://x")],
            [story("Metro line opens", "https://b.se/metro")],
        ),
        indonesia=CountrySection(
            story("Jakarta floods", "https://c.id/floods"),
            [story("Fuel subsidy trimmed", "https://d.id/fuel")],
            [story("New commuter line", "https://f.id/train")],
        ),
        setup="Setup",
    )

    fixed = validate_edition(edition, candidates)
    assert isinstance(fixed, Edition)
    assert fixed.sweden.lead.url == "https://a.se/x?oc=5"
    assert fixed.sweden.stories == []
    assert fixed.sweden.quick_hits[0].url == "https://b.se/metro/"
    assert fixed.indonesia.lead.url == "https://c.id/floods"
