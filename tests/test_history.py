"""Today in history: Wikipedia's events, local ones first, short and light, credited."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx

from briefing import history
from briefing.history import HistoryConfig, LocalTag, pick
from briefing.main import _add_history
from briefing.render import render_html, render_regional_text

DAY = date(2026, 10, 24)
CONFIG = HistoryConfig(
    count=4,
    max_local=2,
    local=[
        LocalTag("🌵", ["Arizona", "Grand Canyon"]),
        LocalTag("🏔️", ["Colorado", "Denver"]),
    ],
)


def page(title: str) -> dict:
    return {
        "titles": {"normalized": title},
        "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}},
    }


PAYLOAD = {
    "events": [
        {"year": 1861, "text": "The first transcontinental telegraph line is completed in the United States", "pages": [page("First Transcontinental Telegraph")]},
        {"year": 1901, "text": "Annie Edson Taylor becomes the first person to go over Niagara Falls in a barrel.", "pages": [page("Annie Edson Taylor")]},
        {"year": 1945, "text": "The United Nations is founded.", "pages": [page("United Nations")]},
        {"year": 1919, "text": "A national park is established.", "pages": [page("Grand Canyon National Park")]},
        {"year": 1876, "text": "Denver hosts the first session of the new state legislature.", "pages": [page("Colorado General Assembly")]},
        {"year": 1917, "text": "Thousands are killed in a battle on the Italian front.", "pages": [page("Battle of Caporetto")]},
        {"year": 2003, "text": "Concorde makes its last commercial flight.", "pages": [page("Concorde")]},
        {"year": 1960, "text": " ".join(["A very long entry"] * 20), "pages": [page("Long")]},
        {"year": 1999, "text": "No link at all.", "pages": []},
        {"year": -44, "text": "A Roman event happens.", "pages": [page("Rome")]},
    ]
}


def events():
    return history.parse_events(PAYLOAD)


def test_parse_skips_unlinked_events_and_adds_full_stops():
    parsed = events()
    assert "No link at all." not in [event.text for event in parsed]
    assert parsed[0].text.endswith("States.")
    assert parsed[3].titles == "Grand Canyon National Park"


def test_local_events_lead_and_are_flagged_and_matched_by_linked_page():
    items = pick(events(), CONFIG, DAY)
    assert len(items) == 4
    local = {item.year: item.emoji for item in items if item.emoji}
    # Grand Canyon matches through the linked page title, not the event text.
    assert local == {1919: "🌵", 1876: "🏔️"}
    assert [item.year for item in items] == sorted(item.year for item in items)


def test_picks_are_short_light_and_stable_per_day():
    items = pick(events(), CONFIG, DAY)
    texts = " ".join(item.text for item in items)
    assert "killed" not in texts
    assert "very long entry" not in texts
    assert pick(events(), CONFIG, DAY) == items


def test_bc_years_read_naturally():
    item = next(i for i in pick(events(), replace(CONFIG, count=10, local=[]), DAY) if i.year < 0)
    assert item.year_label == "44 BC"


def test_fetch_falls_back_to_the_second_endpoint():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        assert request.url.path.endswith("/10/24")
        if request.url.host == "api.wikimedia.org":
            return httpx.Response(503)
        return httpx.Response(200, json=PAYLOAD)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert history.fetch_events(DAY, client)
    assert seen == ["api.wikimedia.org", "en.wikipedia.org"]


def test_us_config_has_a_history_block():
    config = history.load_history_config(Path("config/us_sources.yml"))
    assert config is not None and config.count == 4
    assert [tag.emoji for tag in config.local] == ["🌵", "🏔️"]
    assert history.load_history_config(Path("config/sources.yml")) is None


def test_section_renders_last_with_credit(monkeypatch):
    import test_regional as tr

    edition = tr._edition(monkeypatch)
    edition = replace(edition, history=pick(events(), CONFIG, DAY))
    html = render_html(edition)
    assert "📜 Today in history" in html
    assert html.index("Denver, Colorado") < html.index("Today in history") < html.index("Coded by Ari")
    assert "🌵 <strong>1919:</strong>" in html
    assert "CC BY-SA 4.0" in html and html.count("Wikipedia →") == 4
    text = render_regional_text(edition)
    assert "TODAY IN HISTORY" in text and "1919: A national park is established." in text
    # Empty history leaves no empty section behind.
    assert "Today in history" not in render_html(replace(edition, history=[]))


def test_wikipedia_outage_never_blocks_the_send(monkeypatch):
    import argparse

    import test_regional as tr

    edition = tr._edition(monkeypatch)

    def down(*args, **kwargs):
        raise RuntimeError("wikipedia down")

    monkeypatch.setattr("briefing.main.build_history", down)
    args = argparse.Namespace(sources=Path("config/us_sources.yml"))
    assert _add_history(edition, args, DAY) is edition
