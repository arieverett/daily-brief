"""Scoreboard: ESPN scores/standings for the home teams, plus the occasional big play."""

import argparse
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from briefing import sports
from briefing.main import _add_sports
from briefing.models import BigPlay, Candidate
from briefing.render import render_html, render_regional_text
from briefing.sports import (
    GroupConfig,
    SportsConfig,
    TeamConfig,
    build_scoreboard,
    check_big_plays,
    previous_edition_start,
)

DAY = date(2026, 11, 9)  # a Monday
NOW = datetime(2026, 11, 9, 10, 45, tzinfo=UTC)  # 05:45 ET
PHX = ZoneInfo("America/Phoenix")


def competitor(team_id, name, home, score=None, winner=None, rank=99, short=None, location=None):
    value = {
        "id": team_id,
        "homeAway": "home" if home else "away",
        "curatedRank": {"current": rank},
        "team": {
            "id": team_id,
            "displayName": name,
            "shortDisplayName": short or name.split()[-1],
            "location": location or " ".join(name.split()[:-1]),
        },
    }
    if score is not None:
        value["score"] = {"value": float(score), "displayValue": str(score)}
    if winner is not None:
        value["winner"] = winner
    return value


def event(event_id, when, competitors, completed, detail=""):
    return {
        "id": event_id,
        "date": when,
        "competitions": [
            {
                "date": when,
                "competitors": competitors,
                "status": {"type": {"completed": completed, "shortDetail": detail}},
            }
        ],
    }


CARDS = {
    "id": "22",
    "displayName": "Arizona Cardinals",
    "standingSummary": "2nd in NFC West",
    "record": {"items": [{"type": "total", "summary": "5-4"}]},
    "links": [{"href": "https://www.espn.com/nfl/team/_/name/ari/arizona-cardinals"}],
    "nextEvent": [
        event(
            "3",
            "2026-11-15T21:05Z",
            [competitor("22", "Arizona Cardinals", True), competitor("25", "San Francisco 49ers", False)],
            False,
            "11/15 - 4:05 PM EST",
        )
    ],
}
CARDS_SCHEDULE = {
    "events": [
        event(
            "1",
            "2026-11-01T21:05Z",
            [competitor("22", "Arizona Cardinals", False, 10, False), competitor("26", "Seattle Seahawks", True, 20, True)],
            True,
            "Final",
        ),
        event(
            "2",
            "2026-11-08T21:25Z",
            [competitor("22", "Arizona Cardinals", True, 27, True), competitor("14", "Los Angeles Rams", False, 24, False)],
            True,
            "Final/OT",
        ),
    ]
}
ASU = {
    "id": "9",
    "displayName": "Arizona State Sun Devils",
    "standingSummary": "3rd in Big 12",
    "record": {"items": [{"type": "total", "summary": "7-2"}]},
}
ASU_SCHEDULE = {
    "events": [
        event(
            "10",
            "2026-11-07T03:30Z",  # Fri 8:30 PM Phoenix: before Saturday's edition
            [competitor("9", "Arizona State Sun Devils", True, 31, True, rank=18), competitor("2", "Utah Utes", False, 14, False)],
            True,
            "Final",
        ),
        event(
            "11",
            "2026-11-14T19:00Z",
            [competitor("9", "Arizona State Sun Devils", False, rank=16), competitor("12", "Arizona Wildcats", True, location="Arizona")],
            False,
            "TBD",
        ),
    ]
}
DBACKS = {"id": "29", "displayName": "Arizona Diamondbacks", "record": {"items": [{"summary": "84-78"}]}}
DBACKS_SCHEDULE = {
    "events": [
        event(
            "20",
            "2026-09-27T20:10Z",
            [competitor("29", "Arizona Diamondbacks", True, 3, False), competitor("19", "Los Angeles Dodgers", False, 5, True)],
            True,
            "Final",
        )
    ]
}

ROUTES = {
    "/football/nfl/teams/ari": {"team": CARDS},
    "/football/nfl/teams/ari/schedule": CARDS_SCHEDULE,
    "/football/college-football/teams/9": {"team": ASU},
    "/football/college-football/teams/9/schedule": ASU_SCHEDULE,
    "/baseball/mlb/teams/ari": {"team": DBACKS},
    "/baseball/mlb/teams/ari/schedule": DBACKS_SCHEDULE,
    "/basketball/nba/teams/phx": {"team": {"id": "21", "displayName": "Atlanta Hawks"}},
    "/basketball/nba/teams": {"sports": [{"leagues": [{"teams": [{"team": {"id": "1", "displayName": "Atlanta Hawks"}}]}]}]},
    # College baseball: the football id is another team; the right one is found by name.
    "/baseball/college-baseball/teams/9": {"team": {"id": "9", "displayName": "Australia"}},
    "/baseball/college-baseball/teams": {
        "sports": [{"leagues": [{"teams": [
            {"team": {"id": "9", "displayName": "Australia"}},
            {"team": {"id": "58", "displayName": "Arizona State Sun Devils"}},
        ]}]}]
    },
    "/baseball/college-baseball/teams/58": {"team": {"id": "58", "displayName": "Arizona State Sun Devils"}},
    "/baseball/college-baseball/teams/58/schedule": {"events": []},
}

CONFIG = SportsConfig(
    groups=[
        GroupConfig(
            name="Arizona",
            emoji="🌵",
            timezone="America/Phoenix",
            teams=[
                TeamConfig("Cardinals", "football", "nfl", "ari", "Arizona Cardinals"),
                TeamConfig("Suns", "basketball", "nba", "phx", "Phoenix Suns"),
                TeamConfig("Diamondbacks", "baseball", "mlb", "ari", "Diamondbacks"),
                TeamConfig("ASU football", "football", "college-football", "9", "Arizona State"),
                TeamConfig("ASU baseball", "baseball", "college-baseball", "9", "Arizona State"),
            ],
        )
    ]
)


def client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/apis/site/v2/sports")
        if path in ROUTES:
            return httpx.Response(200, json=ROUTES[path])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def board():
    return build_scoreboard(CONFIG, DAY, now=NOW, client=client())


def test_previous_edition_covers_the_weekend_on_monday():
    assert previous_edition_start(DAY).date() == date(2026, 11, 7)
    assert previous_edition_start(date(2026, 11, 10)).date() == date(2026, 11, 9)


def test_pro_line_has_record_standing_new_result_and_next_game():
    cards = board().groups[0].teams[0]
    assert cards.team == "Cardinals"
    assert (cards.record, cards.standing) == ("5-4", "2nd in NFC West")
    assert cards.last == "W 27-24 (OT) vs Rams (Sun)"
    # Phoenix time: 21:05 UTC is 2:05 PM MST, six days out.
    assert cards.next == "Sun vs 49ers, 2:05 PM"
    assert cards.url.startswith("https://www.espn.com/nfl/team")


def test_college_line_shows_ranking_and_hides_results_already_reported():
    asu = board().groups[0].teams[1]
    assert asu.rank == "#16" and asu.standing == "3rd in Big 12"
    assert asu.last == ""  # Friday night's game was in Saturday's edition
    assert asu.next == "Sat at Arizona"  # TBD kickoff: no clock


def test_offseason_teams_and_wrong_ids_are_kept_out_of_the_lines():
    result = board()
    assert [line.team for line in result.groups[0].teams] == ["Cardinals", "ASU football"]
    # ASU baseball's football id was wrong; found by name, then offseason.
    assert result.offseason == ["Diamondbacks", "ASU baseball"]
    assert len(result.errors) == 1 and "Phoenix Suns" in result.errors[0]  # match guard


def test_us_config_has_a_sports_block():
    config = sports.load_sports_config(Path("config/us_sources.yml"))
    assert config is not None
    assert [group.name for group in config.groups] == ["Arizona", "Colorado"]
    labels = [team.label for group in config.groups for team in group.teams]
    for label in ("Cardinals", "Suns", "Diamondbacks", "ASU football", "Arizona hoops", "Broncos", "Nuggets", "Avalanche", "Rockies", "CU football"):
        assert label in labels
    assert config.big_plays is not None and len(config.big_plays.feeds) == 4
    assert all(feed["country"] in {"Phoenix", "Denver"} for feed in config.big_plays.feeds)
    assert sports.load_sports_config(Path("config/sources.yml")) is None


CANDIDATES = [
    Candidate(
        country="Phoenix",
        title="Chandler Hamilton wins 6A state title, 45-14, for 3rd straight crown",
        url="https://example.com/hamilton",
        source="azcentral",
    ),
    Candidate(country="Denver", title="Cherry Creek rolls in playoff opener", url="https://example.com/cc", source="Denver Post"),
]


def test_big_plays_keep_only_linked_items_with_sourced_numbers():
    items = [
        {"emoji": "🏈", "label": "Chandler Hamilton", "text": "Won the 6A title 45-14 — a 3rd straight.", "url": "https://example.com/hamilton"},
        {"emoji": "🏈", "label": "Cherry Creek", "text": "Won by 50 points.", "url": "https://example.com/cc"},
        {"emoji": "⛳", "label": "Made up", "text": "Something.", "url": "https://example.com/nope"},
    ]
    kept, dropped = check_big_plays(items, CANDIDATES, 3)
    assert [play.label for play in kept] == ["Chandler Hamilton"]
    assert "—" not in kept[0].text
    assert len(dropped) == 2


def _report():
    result = board()
    play = BigPlay("🏈", "Chandler Hamilton", "Won the 6A state title.", "https://example.com/hamilton")
    return sports.build_sports_report(result, [play])


def test_section_renders_before_history_with_credit(monkeypatch):
    import test_regional as tr

    edition = replace(tr._edition(monkeypatch), sports=_report())
    html = render_html(edition)
    assert "🏆 Scoreboard" in html
    assert html.index("Denver, Colorado") < html.index("Scoreboard") < html.index("Coded by Ari")
    assert "Cardinals</a></strong>" in html and "#16 ASU football" in html
    assert "W 27-24 (OT) vs Rams (Sun)" in html and "🔥 Big plays" in html
    assert "Offseason: Diamondbacks, ASU baseball." in html and "from ESPN" in html
    text = render_regional_text(edition)
    assert "SCOREBOARD" in text and "Cardinals 5-4, 2nd in NFC West | Last: W 27-24 (OT) vs Rams (Sun)" in text
    assert "Scoreboard" not in render_html(replace(edition, sports=None))


def test_espn_outage_never_blocks_the_send(monkeypatch):
    import test_regional as tr

    edition = tr._edition(monkeypatch)

    def down(*args, **kwargs):
        raise RuntimeError("espn down")

    monkeypatch.setattr("briefing.main.build_scoreboard", down)
    args = argparse.Namespace(sources=Path("config/us_sources.yml"))
    assert _add_sports(edition, args, None, DAY) is edition


def test_big_plays_failure_still_sends_the_scoreboard(monkeypatch):
    import test_regional as tr

    edition = tr._edition(monkeypatch)
    monkeypatch.setattr("briefing.main.build_scoreboard", lambda config, day: board())

    def down(*args, **kwargs):
        raise RuntimeError("news down")

    monkeypatch.setattr("briefing.main.collect_big_play_candidates", down)
    args = argparse.Namespace(sources=Path("config/us_sources.yml"))
    result = _add_sports(edition, args, None, DAY)
    assert result.sports is not None and result.sports.big_plays == []
    assert result.sports.groups[0].teams[0].team == "Cardinals"
