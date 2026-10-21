"""Two-Mile High: a config-driven English edition built from city/state regions."""

from datetime import date
from pathlib import Path

import yaml

from briefing import editor, regional
from briefing.editorial import SYSTEM_PROMPT
from briefing.main import edition_countries
from briefing.models import Candidate, RegionalEdition
from briefing.render import render_html, render_regional_text
from briefing.style_check import score_edition
from briefing.weather import WEATHER_PROMPT_EN, load_weather_config

US_SOURCES = Path("config/us_sources.yml")
DAY = date(2026, 10, 21)


def profile():
    loaded = regional.load_profile(US_SOURCES)
    assert loaded is not None
    return loaded


def test_profile_comes_from_the_sources_file():
    prof = profile()
    assert prof.edition == "us"
    assert prof.title == "Two-Mile High"
    assert [(r.key, r.heading) for r in prof.regions] == [
        ("phoenix", "Phoenix, Arizona"),
        ("denver", "Denver, Colorado"),
    ]
    assert edition_countries("us", US_SOURCES) == ("Phoenix", "Denver")
    # The fixed-layout editions have no edition block.
    assert regional.load_profile(Path("config/sources.yml")) is None


def test_every_feed_fills_a_configured_region():
    feeds = yaml.safe_load(US_SOURCES.read_text(encoding="utf-8"))["feeds"]
    names = set(profile().region_names)
    assert {feed["country"] for feed in feeds} == names
    for name in names:
        assert sum(f["country"] == name and f.get("tier") != "backup" for f in feeds) >= 5


def test_weather_features_both_cities_in_fahrenheit():
    config = load_weather_config(US_SOURCES)
    assert config is not None
    assert [place.name for place in config.featured] == ["Phoenix", "Denver"]
    assert config.unit == "F"
    assert config.about == "Phoenix and Denver"
    # Sweden/Indonesia-specific topics stay out.
    assert "aurora" not in config.topics and "el_nino" not in config.topics
    assert "newsletter about\nSweden and Indonesia." in WEATHER_PROMPT_EN


def test_prompts_are_retargeted_at_the_regions():
    prof = profile()
    instructions = f"{regional.system_prompt(prof)}\n\n{regional.voice_guidance(prof)}"
    # Every swap actually matched (a silent no-op would leave the old text behind).
    assert "Sweden" not in instructions and "Indonesia" not in instructions
    assert "Volvo" not in instructions and "SVT" not in instructions
    assert "Riksbank" not in instructions
    assert "Two-Mile High, a concise personal morning briefing about Phoenix and Denver." in (
        instructions
    )
    assert "- phoenix: Phoenix, Arizona." in instructions
    assert "Each region section gets" in instructions
    # The shared editorial rules carry over unchanged.
    assert SYSTEM_PROMPT.split("REPORTING RULES", 1)[1].split("Never say")[0] in instructions


def test_schema_has_one_section_per_region():
    schema = regional.edition_schema(profile())
    assert schema["required"][-2:] == ["phoenix", "denver"]
    assert schema["properties"]["phoenix"] == schema["properties"]["denver"]


def _story(city: str, n: int) -> dict:
    return {
        "headline": f"{city} story {n} about light rail and water budgets",
        "summary": f"**{city} first.** Something happened in {city}.\n\nThen more happened.",
        "highlights": [],
        "url": f"https://example.com/{city.lower()}/{n}",
        "source": f"{city} Paper",
        "label": "LOCAL LOOP",
        "source_links": [{"source": f"{city} Paper", "url": f"https://example.com/{city.lower()}/{n}"}],
        "topic_key": f"{city.lower()}-topic-{n}",
    }


def _payload() -> dict:
    def section(city: str) -> dict:
        return {
            "lead": _story(city, 0),
            "stories": [_story(city, 1), _story(city, 2)],
            "quick_hits": [_story(city, 3), _story(city, 4), _story(city, 5)],
        }

    return {
        "edition_date": "whenever",
        "date_label": "someday",
        "subject": "Heat breaks, snow arrives",
        "preview_text": "Phoenix cools off and Denver gets its first snow.",
        "setup": "**Two cities, two seasons.** Phoenix finally cools off while Denver digs out.",
        "phoenix": section("Phoenix"),
        "denver": section("Denver"),
    }


def _candidates() -> list[Candidate]:
    out = []
    for city in ("Phoenix", "Denver"):
        for n in range(8):
            out.append(
                Candidate(
                    country=city,
                    title=f"{city} story {n} about light rail and water budgets",
                    url=f"https://example.com/{city.lower()}/{n}",
                    source=f"{city} Paper",
                )
            )
    out.append(Candidate(country="Sweden", title="Stray", url="https://example.com/x", source="X"))
    return out


def _edition(monkeypatch) -> RegionalEdition:
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return _payload()

    monkeypatch.setattr(editor, "_generate_structured_output", fake)
    edition = regional.create_regional_edition(_candidates(), profile(), "k", "gpt-5-mini", DAY)
    assert "Stray" not in seen["prompt"]  # other editions' candidates never reach the editor
    assert seen["schema_name"] == "regional_brief"
    return edition


def test_editor_builds_a_dated_branded_edition(monkeypatch):
    edition = _edition(monkeypatch)
    assert edition.edition_date == "2026-10-21"
    assert edition.date_label == "Wednesday, October 21"
    assert edition.subject == "Two-Mile High: Heat breaks, snow arrives"
    assert [region.key for region, _ in edition.named_sections()] == ["phoenix", "denver"]
    assert edition.sections["denver"].lead.url == "https://example.com/denver/0"


def test_editor_refuses_a_thin_region(monkeypatch):
    candidates = [c for c in _candidates() if c.country != "Denver"]
    monkeypatch.setattr(editor, "_generate_structured_output", lambda **_: _payload())
    try:
        regional.create_regional_edition(candidates, profile(), "k", "m", DAY)
    except RuntimeError as exc:
        assert "Denver" in str(exc)
    else:
        raise AssertionError("expected a thin-pool error")


def test_html_and_text_use_the_regional_branding(monkeypatch):
    edition = _edition(monkeypatch)
    html = render_html(edition)
    assert "TWO-MILE HIGH" in html
    assert "Your daily Phoenix + Denver brief" in html
    assert "🌵 Phoenix, Arizona" in html and "🏔️ Denver, Colorado" in html
    assert html.index("Phoenix, Arizona") < html.index("Denver, Colorado")
    assert "#c2410c" in html
    assert "Sweden" not in html and "NUSANTARA" not in html and "Baca" not in html
    assert "Good morning." in html and "Speed read" in html
    text = render_regional_text(edition)
    assert text.startswith("TWO-MILE HIGH\n\nWednesday, October 21")
    assert "PHOENIX, ARIZONA" in text and "DENVER, COLORADO" in text
    assert score_edition(edition).score >= 0
