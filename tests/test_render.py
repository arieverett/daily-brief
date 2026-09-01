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
