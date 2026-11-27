from datetime import date
from pathlib import Path

import pytest
import yaml

from briefing import editor, main, sunday
from briefing.models import Candidate, Edition, IndonesiaEdition, RegionalEdition
from briefing.regional import load_profile
from briefing.render import render_html, render_indonesia_text, render_regional_text, render_text
from briefing.style_check import score_edition

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 11, 29)  # a Sunday


def _candidates(country: str, count: int = 8) -> list[Candidate]:
    slug = country.lower()
    # Every title shares the theme's words, as a real Sunday pool would.
    return [
        Candidate(
            country=country,
            title=f"{country} gaming boom: esports arena story {n}",
            url=f"https://example.com/{slug}/{n}",
            source=f"{country} Paper",
        )
        for n in range(count)
    ]


def _story(country: str, n: int) -> dict:
    slug = country.lower()
    return {
        "headline": f"{country} gaming boom fills a new esports arena {n}",
        "summary": f"**Game on.** {country} story {n} happened.\n\nThen more happened.",
        "highlights": [],
        "url": f"https://example.com/{slug}/{n}",
        "source": f"{country} Paper",
        "label": "PLAYER ONE",
        "source_links": [{"source": f"{country} Paper", "url": f"https://example.com/{slug}/{n}"}],
        "topic_key": f"{slug}-gaming-angle-{n}",
    }


def _payload(keys_countries: list[tuple[str, str]], stories: int = 4) -> dict:
    payload = {
        "theme": "Gaming's  Big Moment",
        "subject": "Gaming is having a moment",
        "preview_text": "How gaming got this big, from arenas to studios.",
        "setup": "**Press start.** Gaming is everywhere this week, and here's how it plays out.",
    }
    for key, country in keys_countries:
        payload[key] = {"stories": [_story(country, n) for n in range(stories)]}
    return payload


def _fake(monkeypatch, payloads):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return payloads[min(len(calls) - 1, len(payloads) - 1)]

    monkeypatch.setattr(editor, "_generate_structured_output", fake)
    return calls


def test_standard_sunday_edition_has_one_theme_and_no_speed_reads(monkeypatch):
    calls = _fake(monkeypatch, [_payload([("sweden", "Sweden"), ("indonesia", "Indonesia")])])
    edition = sunday.create_sunday_edition(
        _candidates("Sweden") + _candidates("Indonesia"),
        edition_name="standard",
        api_key="k",
        model="m",
        day=DAY,
    )
    assert isinstance(edition, Edition)
    assert edition.theme == "Gaming's Big Moment"
    assert edition.edition_date == "2026-11-29"
    assert edition.date_label == "Sunday, November 29"
    # Stories that share the theme's words aren't treated as duplicates.
    for section in (edition.sweden, edition.indonesia):
        assert 1 + len(section.stories) == 4
        assert section.quick_hits == []
    assert calls[0]["schema_name"] == "sunday_brief"
    assert "SUNDAY EDITION SHAPE" in calls[0]["instructions"]
    # The daily edition's shape (Speed reads etc.) is not in the Sunday instructions.
    assert "Each country gets one lead" not in calls[0]["instructions"]
    assert "the week GTA VI came out" in calls[0]["instructions"]

    html = render_html(edition)
    assert (
        "This Sunday: Gaming&#39;s Big Moment" in html or "This Sunday: Gaming's Big Moment" in html
    )
    assert "The Sunday edition" in html and "going deep on one topic" in html
    assert "Speed read" not in html and "The big one" not in html
    text = render_text(edition)
    assert "THIS SUNDAY: GAMING'S BIG MOMENT" in text and "SPEED READ" not in text
    assert score_edition(edition).score >= 0


def test_nusantara_sunday_edition_is_indonesian(monkeypatch):
    calls = _fake(monkeypatch, [_payload([("indonesia", "Indonesia")])])
    edition = sunday.create_sunday_edition(
        _candidates("Indonesia"), edition_name="indonesia", api_key="k", model="m", day=DAY
    )
    assert isinstance(edition, IndonesiaEdition)
    assert edition.date_label == "Minggu, 29 November"
    assert edition.subject.startswith("Nusantara Daily")
    assert "BENTUK EDISI MINGGU" in calls[0]["instructions"]
    assert "Baca kilat dari topik berbeda" not in calls[0]["instructions"]
    html = render_html(edition)
    assert "Edisi Minggu" in html and "Minggu ini: Gaming" in html and "Baca kilat" not in html
    assert "MINGGU INI: GAMING" in render_indonesia_text(edition)


def test_two_mile_high_sunday_edition_covers_arizona_and_colorado(monkeypatch):
    profile = load_profile(ROOT / "config" / "us_sources.yml")
    calls = _fake(monkeypatch, [_payload([("phoenix", "Phoenix"), ("denver", "Denver")], 3)])
    edition = sunday.create_sunday_edition(
        _candidates("Phoenix") + _candidates("Denver"),
        edition_name="us",
        api_key="k",
        model="m",
        day=DAY,
        profile=profile,
    )
    assert isinstance(edition, RegionalEdition)
    assert edition.subject.startswith("Two-Mile High: ")
    assert [region.key for region, _ in edition.named_sections()] == ["phoenix", "denver"]
    instructions = calls[0]["instructions"]
    assert "stories about Arizona (the Phoenix area first" in instructions
    assert "stories about Colorado (the Denver area first" in instructions
    html = render_html(edition)
    assert "The Sunday edition" in html and "Speed read" not in html
    assert "THIS SUNDAY:" in render_regional_text(edition)


def test_a_section_that_loses_its_stories_is_regenerated(monkeypatch):
    bad = _payload([("sweden", "Sweden"), ("indonesia", "Indonesia")], 3)
    for n, story in enumerate(bad["sweden"]["stories"][1:]):
        story["url"] = story["source_links"][0]["url"] = f"https://invented.example/{n}"
        story["headline"] = f"Unrelated invented headline number {n} about llamas"
    good = _payload([("sweden", "Sweden"), ("indonesia", "Indonesia")])
    calls = _fake(monkeypatch, [bad, good])
    edition = sunday.create_sunday_edition(
        _candidates("Sweden") + _candidates("Indonesia"),
        edition_name="standard",
        api_key="k",
        model="m",
        day=DAY,
    )
    assert len(calls) == 2
    assert 1 + len(edition.sweden.stories) == 4


def test_thin_pool_refuses():
    with pytest.raises(RuntimeError, match="Sunday"):
        sunday.create_sunday_edition(
            _candidates("Sweden") + _candidates("Indonesia", 2),
            edition_name="standard",
            api_key="k",
            model="m",
            day=DAY,
        )


def test_schema_asks_for_three_to_five_stories_per_section():
    schema = sunday.sunday_schema(sunday.sections_for("standard"))
    assert schema["required"] == [
        "theme",
        "subject",
        "preview_text",
        "setup",
        "sweden",
        "indonesia",
    ]
    stories = schema["properties"]["sweden"]["properties"]["stories"]
    assert (stories["minItems"], stories["maxItems"]) == (3, 5)


def test_sunday_format_follows_the_day_unless_overridden(monkeypatch):
    monkeypatch.delenv("BRIEF_FORMAT", raising=False)
    assert main.sunday_format(DAY)
    assert not main.sunday_format(date(2026, 11, 27))
    assert main.sunday_format(date(2026, 11, 27), "sunday")
    assert not main.sunday_format(DAY, "daily")
    assert main.sunday_format(DAY, "auto")


@pytest.mark.parametrize(
    ("name", "countries"),
    [
        ("sources.yml", {"Sweden", "Indonesia"}),
        ("indonesia_sources.yml", {"Indonesia"}),
        ("us_sources.yml", {"Phoenix", "Denver"}),
    ],
)
def test_every_edition_has_sunday_feeds_for_each_section(name, countries):
    config = yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))
    feeds = config["sunday_feeds"]
    assert {feed["country"] for feed in feeds} == countries
    for feed in feeds:
        assert feed["allowed_sources"], feed["name"]
        assert "when%3A7d" in feed["url"], feed["name"]
