"""The Scoreboard in a regional edition (Two-Mile High): scores, standings and big plays.

Two parts:

- Teams: records, standings, new results since the last edition and the next game, from
  ESPN's public site API (no key). Every number comes straight from ESPN's data. Teams
  whose season is over show up only in a one-line "Offseason" list.
- Big plays: high school and other sports (golf, soccer, hockey, smaller schools) only
  when something big happens: a state title, a record, a huge upset. Headlines come from
  the `sports.big_plays.feeds` Google News queries; the model picks zero to a few and
  writes one line each, linked to the story. Lines citing numbers that aren't in the
  headline are dropped. An empty list is the normal day.

Best-effort: any failure leaves (part of) the section out; the edition still sends.

    python -m briefing.sports config/us_sources.yml [YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import yaml

from .models import BigPlay, Candidate, SportsReport, TeamGroup, TeamLine

API = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{team}"
HEADERS = {
    "User-Agent": "DailyBriefNewsletter/1.0 (https://github.com/arieverett/daily-brief)",
    "Accept": "application/json",
}
TIMEOUT = httpx.Timeout(15.0, connect=10.0)
NEW_YORK = ZoneInfo("America/New_York")
SPORT_EMOJI = {"football": "🏈", "basketball": "🏀", "baseball": "⚾", "hockey": "🏒"}
COLLEGE_LEAGUES = {"college-football", "mens-college-basketball", "college-baseball"}
# A team is "in season" with a game in this window either side of today.
ACTIVE_PAST_DAYS = 10
ACTIVE_NEXT_DAYS = 14
UNRANKED = 99


# ---------------------------------------------------------------------------
# Config


@dataclass(frozen=True)
class TeamConfig:
    label: str
    sport: str
    league: str
    team: str  # ESPN id or abbreviation
    match: str  # must appear in ESPN's team name: guards against a wrong id

    @property
    def emoji(self) -> str:
        return SPORT_EMOJI.get(self.sport, "🏅")

    @property
    def college(self) -> bool:
        return self.league in COLLEGE_LEAGUES


@dataclass(frozen=True)
class GroupConfig:
    name: str
    emoji: str
    timezone: str
    teams: list[TeamConfig]


@dataclass(frozen=True)
class BigPlaysConfig:
    max_items: int = 3
    about: str = ""
    feeds: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class SportsConfig:
    groups: list[GroupConfig]
    big_plays: BigPlaysConfig | None = None


def load_sports_config(sources_path: Path) -> SportsConfig | None:
    """The optional `sports:` block of an edition's sources file."""
    config = yaml.safe_load(Path(sources_path).read_text(encoding="utf-8")) or {}
    raw = config.get("sports")
    if not raw:
        return None
    groups = [
        GroupConfig(
            name=str(group["name"]),
            emoji=str(group.get("emoji", "")),
            timezone=str(group.get("timezone", "America/New_York")),
            teams=[
                TeamConfig(
                    label=str(team["label"]),
                    sport=str(team["sport"]),
                    league=str(team["league"]),
                    team=str(team["team"]),
                    match=str(team.get("match") or team["label"]),
                )
                for team in group.get("teams", [])
            ],
        )
        for group in raw.get("groups", [])
    ]
    plays = raw.get("big_plays")
    big_plays = (
        BigPlaysConfig(
            max_items=int(plays.get("max_items", 3)),
            about=str(plays.get("about", "")),
            feeds=list(plays.get("feeds", [])),
        )
        if plays
        else None
    )
    return SportsConfig(groups=groups, big_plays=big_plays)


# ---------------------------------------------------------------------------
# ESPN


@dataclass(frozen=True)
class Game:
    start: datetime
    completed: bool
    home: bool
    opponent: str
    detail: str = ""  # "Final/OT", "Sun, November 15th at 4:05 PM EST"
    ours: str = ""  # score
    theirs: str = ""
    won: bool | None = None
    rank: int = UNRANKED  # our ranking going into the game (college)
    time_valid: bool = True


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _score(competitor: dict[str, Any]) -> str:
    score = competitor.get("score")
    if isinstance(score, dict):
        score = score.get("displayValue", score.get("value"))
    if score is None or score == "":
        return ""
    try:
        return str(int(float(score)))
    except (TypeError, ValueError):
        return str(score)


def _opponent_name(team: dict[str, Any], college: bool) -> str:
    keys = ("location", "shortDisplayName", "displayName") if college else (
        "shortDisplayName", "name", "displayName"
    )
    return next((str(team[key]) for key in keys if team.get(key)), "TBD")


def parse_game(event: dict[str, Any], team_id: str, college: bool) -> Game | None:
    """One schedule event from our team's side, or None if it can't be read."""
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    ours = next(
        (c for c in competitors if str((c.get("team") or {}).get("id", c.get("id"))) == team_id),
        None,
    )
    theirs = next((c for c in competitors if c is not ours), None)
    start = _parse_time(competition.get("date") or event.get("date", ""))
    if ours is None or theirs is None or start is None:
        return None
    status = (competition.get("status") or event.get("status") or {}).get("type") or {}
    rank = (ours.get("curatedRank") or {}).get("current", UNRANKED)
    won = ours.get("winner")
    our_score, their_score = _score(ours), _score(theirs)
    completed = bool(status.get("completed"))
    if completed and won is None and our_score and their_score:
        try:
            if float(our_score) != float(their_score):
                won = float(our_score) > float(their_score)
        except ValueError:
            pass
    return Game(
        start=start,
        completed=completed,
        home=ours.get("homeAway") == "home",
        opponent=_opponent_name(theirs.get("team") or {}, college),
        detail=str(status.get("shortDetail") or status.get("detail") or ""),
        ours=our_score,
        theirs=their_score,
        won=won if isinstance(won, bool) else None,
        rank=int(rank) if isinstance(rank, int | float) else UNRANKED,
        time_valid=bool(event.get("timeValid", competition.get("timeValid", True))),
    )


def _record(team: dict[str, Any]) -> str:
    record = team.get("record") or {}
    for item in record.get("items") or []:
        if item.get("type", "total") == "total" and item.get("summary"):
            return str(item["summary"])
    total = record.get("total") or {}
    return str(total.get("summary") or team.get("recordSummary") or "")


@dataclass(frozen=True)
class TeamData:
    name: str
    record: str
    standing: str
    url: str
    games: list[Game]


def fetch_team(config: TeamConfig, client: httpx.Client) -> TeamData:
    base = API.format(sport=config.sport, league=config.league, team=config.team)
    info = client.get(base, headers=HEADERS)
    info.raise_for_status()
    team = info.json().get("team") or {}
    name = str(team.get("displayName", ""))
    if config.match.lower() not in name.lower():
        raise ValueError(f"ESPN team {config.team!r} is {name!r}, expected {config.match!r}")
    team_id = str(team.get("id", config.team))

    schedule = client.get(base + "/schedule", headers=HEADERS)
    schedule.raise_for_status()
    payload = schedule.json()
    events = list(payload.get("events") or []) + list(team.get("nextEvent") or [])
    games: dict[str, Game] = {}
    for event in events:
        game = parse_game(event, team_id, config.college)
        if game is None:
            continue
        key = str(event.get("id") or game.start.isoformat())
        # The schedule wins over nextEvent for the same game (it carries final scores).
        games.setdefault(key, game)

    links = [link.get("href", "") for link in team.get("links") or [] if link.get("href")]
    standing = str(team.get("standingSummary") or (payload.get("team") or {}).get("standingSummary") or "")
    return TeamData(
        name=name,
        record=_record(team) or str((payload.get("team") or {}).get("recordSummary") or ""),
        standing=standing,
        url=links[0] if links else "",
        games=sorted(games.values(), key=lambda game: game.start),
    )


# ---------------------------------------------------------------------------
# Lines


def previous_edition_start(day: date) -> datetime:
    """When the previous edition was put together (editions go out Mon-Sat at 6 AM ET)."""
    back = 2 if day.weekday() == 0 else 1
    return datetime.combine(day - timedelta(days=back), time(5, 0), NEW_YORK)


def _day_label(when: datetime, now: datetime) -> str:
    days = (when.date() - now.date()).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    if -6 <= days < 0 or 1 < days <= 6:
        return f"{when:%a}"
    return f"{when:%b} {when.day}"


def _clock(when: datetime) -> str:
    return f"{when:%I:%M %p}".lstrip("0").replace(":00 ", " ")


def describe_last(game: Game, zone: ZoneInfo, now: datetime) -> str:
    if game.won is True:
        result = "W"
    elif game.won is False:
        result = "L"
    else:
        result = "T"
    where = "vs" if game.home else "at"
    extra = ""
    match = re.search(r"/(\d*OT|SO|F/\d+)", game.detail)
    if match:
        extra = f" ({match.group(1)})"
    when = _day_label(game.start.astimezone(zone), now)
    return f"{result} {game.ours}-{game.theirs}{extra} {where} {game.opponent} ({when})"


def describe_next(game: Game, zone: ZoneInfo, now: datetime) -> str:
    local = game.start.astimezone(zone)
    where = "vs" if game.home else "at"
    when = _day_label(local, now)
    clock = f", {_clock(local)}" if game.time_valid and "TBD" not in game.detail else ""
    return f"{when} {where} {game.opponent}{clock}"


def team_line(config: TeamConfig, data: TeamData, zone: ZoneInfo, day: date, now: datetime) -> TeamLine | None:
    """The team's Scoreboard line, or None when its season is over (no games near today)."""
    since = previous_edition_start(day)
    done = [game for game in data.games if game.completed]
    upcoming = [game for game in data.games if not game.completed and game.start >= now - timedelta(hours=4)]
    recent = done[-1] if done else None
    following = upcoming[0] if upcoming else None
    active = (recent and recent.start >= now - timedelta(days=ACTIVE_PAST_DAYS)) or (
        following and following.start <= now + timedelta(days=ACTIVE_NEXT_DAYS)
    )
    if not active:
        return None
    local_now = now.astimezone(zone)
    ranked = [game.rank for game in (recent, following) if game and game.rank < UNRANKED]
    return TeamLine(
        emoji=config.emoji,
        team=config.label,
        record=data.record,
        standing=data.standing,
        rank=f"#{ranked[-1]}" if config.college and ranked else "",
        last=describe_last(recent, zone, local_now) if recent and recent.start >= since else "",
        next=describe_next(following, zone, local_now) if following else "",
        url=data.url,
    )


@dataclass
class ScoreboardResult:
    groups: list[TeamGroup]
    offseason: list[str]
    errors: list[str]


def build_scoreboard(
    config: SportsConfig, day: date, now: datetime | None = None, client: httpx.Client | None = None
) -> ScoreboardResult:
    now = now or datetime.now(UTC)
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    groups: list[TeamGroup] = []
    offseason: list[str] = []
    errors: list[str] = []
    try:
        for group in config.groups:
            zone = ZoneInfo(group.timezone)
            lines: list[TeamLine] = []
            for team in group.teams:
                try:
                    data = fetch_team(team, client)
                except Exception as exc:  # noqa: BLE001 - one team never sinks the section
                    errors.append(f"{team.label} ({team.league}): {type(exc).__name__}: {exc}"[:240])
                    continue
                line = team_line(team, data, zone, day, now)
                if line is None:
                    if team.label not in offseason:
                        offseason.append(team.label)
                else:
                    lines.append(line)
            groups.append(TeamGroup(name=group.name, emoji=group.emoji, teams=lines))
    finally:
        if own:
            client.close()
    return ScoreboardResult(groups=groups, offseason=offseason, errors=errors)


# ---------------------------------------------------------------------------
# Big plays

BIG_PLAYS_PROMPT = """You pick the "Big plays" for the sports corner of a morning newsletter
about {about}. The reader follows the big pro and college teams in a separate scoreboard. Here
you flag only the remarkable: things a sports fan in these states would be sorry to miss.

Pick an item only if it clears a high bar, for example:
- a high school state championship, a long winning streak or undefeated season, a national
  ranking, a record broken, a huge upset (name the school AND its city, so the reader learns
  which towns have strong programs);
- golf, soccer, hockey, or another sport: a title, a record, a historic first, a pro signing
  or result that made national news;
- a smaller college (e.g. Northern Arizona, Grand Canyon, Colorado State, Air Force) only for
  something extraordinary such as beating a ranked team or a first-ever title.

Skip: routine games and previews, recruiting chatter, injuries, coaching rumors, betting,
anything about the scoreboard teams ({teams}) unless it's off the field and historic, and
anything outside {about}. Returning zero items is normal and better than a weak pick.

Rules: at most {max_items} items, most remarkable first. Use only facts in the headline and
summary you were given; never add numbers, names or details that aren't there. Each item:
emoji (one sport emoji), label (the school, team or athlete, 2 to 5 words, with the city for
high school, e.g. "Chandler Hamilton"), text (one sentence, at most 28 words, plain and
specific, no em dashes, no hype words like "insane"), url (the candidate's url, exactly).
"""

BIG_PLAYS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["emoji", "label", "text", "url"],
                "properties": {
                    "emoji": {"type": "string"},
                    "label": {"type": "string"},
                    "text": {"type": "string"},
                    "url": {"type": "string"},
                },
            },
        }
    },
}

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {value.replace(",", "") for value in NUMBER_RE.findall(text)}


def check_big_plays(
    items: list[dict[str, Any]], candidates: list[Candidate], max_items: int
) -> tuple[list[BigPlay], list[str]]:
    """Keep items that link to a candidate and cite only numbers found in its headline/summary."""
    from .editorial import strip_em_dashes

    by_url = {candidate.url: candidate for candidate in candidates}
    kept: list[BigPlay] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in items:
        url = str(item.get("url", "")).strip()
        candidate = by_url.get(url)
        text = strip_em_dashes(" ".join(str(item.get("text", "")).split()))
        label = strip_em_dashes(" ".join(str(item.get("label", "")).split()))
        if candidate is None:
            dropped.append(f"{label}: link not among the headlines")
            continue
        if url in seen:
            continue
        source = f"{candidate.title} {candidate.summary}"
        unknown = (_numbers(text) | _numbers(label)) - _numbers(source)
        if unknown:
            dropped.append(f"{label}: numbers not in the headline {sorted(unknown)}")
            continue
        if not text or not label:
            continue
        seen.add(url)
        emoji = str(item.get("emoji", "")).strip()[:4] or "🏅"
        kept.append(BigPlay(emoji=emoji, label=label, text=text, url=url))
        if len(kept) >= max_items:
            break
    return kept, dropped


def collect_big_play_candidates(config: BigPlaysConfig, lookback_hours: int) -> list[Candidate]:
    from .collect import deduplicate, fetch_feeds

    results = asyncio.run(fetch_feeds(config.feeds, lookback_hours))
    candidates = [candidate for result in results for candidate in result.candidates]
    candidates = deduplicate(candidates)
    candidates.sort(key=lambda c: c.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return candidates[:60]


def pick_big_plays(
    config: BigPlaysConfig,
    candidates: list[Candidate],
    team_labels: list[str],
    *,
    api_key: str,
    model: str,
    day: date,
) -> tuple[list[BigPlay], list[str]]:
    if not candidates:
        return [], []
    from .editor import _generate_structured_output

    instructions = BIG_PLAYS_PROMPT.format(
        about=config.about or "the reader's home states",
        teams=", ".join(team_labels),
        max_items=config.max_items,
    )
    prompt = f"Edition date: {day.isoformat()}. Candidate headlines as JSON:\n\n" + json.dumps(
        [
            {
                "region": c.country,
                "title": c.title,
                "source": c.source,
                "published_at": c.published_at.isoformat() if c.published_at else "unknown",
                "summary": c.summary[:400],
                "url": c.url,
            }
            for c in candidates
        ],
        ensure_ascii=False,
    )
    payload = _generate_structured_output(
        api_key=api_key,
        model=model,
        instructions=instructions,
        prompt=prompt,
        schema=BIG_PLAYS_SCHEMA,
        schema_name="big_plays",
        retries=2,
    )
    return check_big_plays(list(payload.get("items") or []), candidates, config.max_items)


def big_plays_lookback(day: date) -> int:
    """Hours since the previous edition, with a little overlap (Monday covers the weekend)."""
    return 60 if day.weekday() == 0 else 36


# ---------------------------------------------------------------------------
# Whole section


def scoreboard_report(result: ScoreboardResult) -> str:
    lines: list[str] = []
    for group in result.groups:
        lines.append(f"{group.emoji} {group.name}: {len(group.teams)} teams in season")
        lines += [f"  {format_line(line)}" for line in group.teams]
    if result.offseason:
        lines.append("Offseason: " + ", ".join(result.offseason))
    lines += [f"Failed: {error}" for error in result.errors]
    return "\n".join(lines)


def format_line(line: TeamLine) -> str:
    """Plain-text version of a team line (also used in run notices)."""
    head = " ".join(part for part in (line.emoji, line.rank, line.team) if part)
    parts = [part for part in (line.record, line.standing) if part]
    text = head + (f" {', '.join(parts)}" if parts else "")
    if line.last:
        text += f" | Last: {line.last}"
    if line.next:
        text += f" | Next: {line.next}"
    return text


def build_sports_report(
    scoreboard: ScoreboardResult, big_plays: list[BigPlay]
) -> SportsReport:
    return SportsReport(groups=scoreboard.groups, big_plays=big_plays, offseason=scoreboard.offseason)


def _annotate(level: str, title: str, message: str) -> None:
    print(message, flush=True)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = message[:3000].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level} title={title}::{escaped}", flush=True)


def main(argv: list[str] | None = None) -> int:
    """Preview: the scoreboard plus the Big plays headlines (no model call)."""
    parser = argparse.ArgumentParser(description="Preview the Scoreboard")
    parser.add_argument("sources", type=Path)
    today = datetime.now(NEW_YORK).date()
    parser.add_argument("date", nargs="?", default=today.isoformat())
    args = parser.parse_args(argv)
    config = load_sports_config(args.sources)
    if config is None:
        _annotate("error", "Scoreboard", f"{args.sources}: no sports block")
        return 1
    day = date.fromisoformat(args.date)
    result = build_scoreboard(config, day)
    in_season = sum(len(group.teams) for group in result.groups)
    level = "error" if result.errors and not in_season else ("warning" if result.errors else "notice")
    _annotate(level, "Scoreboard", scoreboard_report(result))
    if config.big_plays:
        candidates = collect_big_play_candidates(config.big_plays, big_plays_lookback(day))
        lines = [f"{len(candidates)} Big plays headlines:"] + [
            f"  [{c.country}] {c.title} ({c.source})" for c in candidates[:40]
        ]
        _annotate("notice", "Big plays headlines", "\n".join(lines))
    return 0 if in_season or not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
