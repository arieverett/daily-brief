from briefing.main import load_sample
from briefing.render import render_html, render_text


def test_html_has_core_sections_and_links():
    html = render_html(load_sample())
    assert "NORDIC + NUSANTARA" in html
    assert "The front page" in html
    assert "Sweden" in html
    assert "Indonesia" in html
    assert "https://www.reuters.com" in html


def test_plain_text_fallback_has_core_sections():
    text = render_text(load_sample())
    assert "THE FRONT PAGE" in text
    assert "SWEDEN" in text
    assert "INDONESIA" in text
    assert "THE BOTTOM LINE" in text
