from dataclasses import replace

from briefing.main import load_sample
from briefing.models import IndonesiaEdition
from briefing.render import render_html, render_indonesia_html, render_indonesia_text, render_text


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


def indonesia_sample() -> IndonesiaEdition:
    standard = load_sample()
    return IndonesiaEdition(
        edition_date=standard.edition_date,
        date_label="Rabu, 2 September",
        subject="Kabar Indonesia hari ini",
        preview_text="Ringkasan berita Indonesia.",
        front_page=[standard.indonesia.lead, *standard.indonesia.stories],
        indonesia=standard.indonesia,
        bottom_line="Kebijakan ekonomi dan budaya menjadi sorotan hari ini.",
    )


def test_indonesia_html_has_approved_branding_and_no_sweden_section():
    html = render_indonesia_html(indonesia_sample())
    assert "NUSANTARA DAILY" in html
    assert "Dalam edisi hari ini" in html
    assert "Berita utama" in html
    assert "Baca kilat" in html
    assert "Curated for Mom by Ari &lt;3" in html
    assert "#991b1b" in html
    assert "🇸🇪" not in html


def test_indonesia_plain_text_is_localized():
    text = render_indonesia_text(indonesia_sample())
    assert "DALAM EDISI HARI INI" in text
    assert "BERITA UTAMA" in text
    assert "Mengapa penting:" in text
    assert "BACA KILAT" in text


def test_indonesia_subject_has_stable_forwarding_prefix():
    from briefing.editor import prefix_indonesia_subject

    assert prefix_indonesia_subject("Kabar pagi") == "Nusantara Daily: Kabar pagi"
    assert prefix_indonesia_subject("Nusantara Daily: Kabar pagi").count("Nusantara Daily:") == 1
    assert len(prefix_indonesia_subject("x" * 100)) == 70


def test_repair_links_snaps_urls_and_drops_hallucinations():
    from briefing.editor import validate_links
    from briefing.models import Candidate, CountrySection, Edition, Story

    candidates = [
        Candidate("Sweden", "Riksbank holds rates", "https://a.se/x?oc=5", "Reuters"),
        Candidate("Sweden", "Metro line opens", "https://b.se/metro/", "SVT"),
        Candidate("Indonesia", "Jakarta floods", "https://c.id/floods", "AP"),
        Candidate("Indonesia", "Fuel subsidy trimmed", "https://d.id/fuel", "AP"),
    ]

    def story(headline, url):
        return Story(headline, "s", "w", url, "X", "NEWS")

    edition = Edition(
        "2026-09-02",
        "d",
        "s",
        "p",
        [story("Riksbank holds rates", "https://a.se/x"), story("Invented", "https://x")],
        CountrySection(story("Metro line opens", "https://b.se/metro")),
        CountrySection(
            story("Jakarta floods", "https://c.id/floods"),
            [story("Fuel subsidy trimmed", "https://d.id/fuel")],
        ),
        "b",
    )

    fixed = validate_links(edition, candidates)
    allowed = {candidate.url for candidate in candidates}
    assert len(fixed.front_page) == 1
    assert fixed.front_page[0].url == "https://a.se/x?oc=5"
    assert fixed.sweden.lead.url == "https://b.se/metro/"
    every = [*fixed.front_page, fixed.sweden.lead, fixed.indonesia.lead]
    assert all(story.url in allowed for story in every)
