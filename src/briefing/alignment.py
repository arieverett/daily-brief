"""Keep the standard edition's Indonesia section on the same stories as Nusantara Daily.

Ari's mom reads Nusantara Daily; Ari reads the Indonesia section of the standard edition.
Sharing stories gives them the same news to talk about. Nusantara Daily is generated first
and saves its picks (``write_picks``); the standard edition loads them (``load_picks``),
adds their candidates to its own pool, and is asked to cover the same topics in the same
roles. Everything here is best-effort: without picks the standard edition is written
exactly as before.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .editorial import canonical_url_key
from .models import Candidate, CountrySection, IndonesiaEdition, Story

ROLES = ("lead", "story", "quick_hit")
ROLE_NAMES = {"lead": "lead", "story": "secondary story", "quick_hit": "Speed read"}


@dataclass(frozen=True)
class Pick:
    role: str  # "lead", "story" or "quick_hit"
    headline: str  # Nusantara Daily's (Bahasa Indonesia) headline, for reference only
    topic_key: str
    urls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Picks:
    edition_date: str
    stories: list[Pick]
    candidates: list[Candidate]

    def by_role(self, role: str) -> list[Pick]:
        return [pick for pick in self.stories if pick.role == role]


def _story_urls(story: Story) -> list[str]:
    urls = [story.url, *(link.url for link in story.source_links)]
    return list(dict.fromkeys(url for url in urls if url))


def _section_stories(section: CountrySection) -> list[tuple[str, Story]]:
    return [
        ("lead", section.lead),
        *(("story", story) for story in section.stories),
        *(("quick_hit", story) for story in section.quick_hits),
    ]


def _candidate_to_dict(candidate: Candidate) -> dict[str, Any]:
    data = asdict(candidate)
    data["published_at"] = candidate.published_at.isoformat() if candidate.published_at else None
    return data


def _candidate_from_dict(value: dict[str, Any]) -> Candidate:
    data = dict(value)
    published = data.get("published_at")
    data["published_at"] = datetime.fromisoformat(published) if published else None
    return Candidate(**data)


def picks_from_edition(edition: IndonesiaEdition, candidates: list[Candidate]) -> Picks:
    by_key = {canonical_url_key(candidate.url): candidate for candidate in candidates}
    stories: list[Pick] = []
    used: dict[str, Candidate] = {}
    for role, story in _section_stories(edition.indonesia):
        urls = _story_urls(story)
        stories.append(
            Pick(role=role, headline=story.headline, topic_key=story.topic_key, urls=urls)
        )
        for url in urls:
            candidate = by_key.get(canonical_url_key(url))
            if candidate is not None:
                used.setdefault(candidate.url, candidate)
    return Picks(edition_date=edition.edition_date, stories=stories, candidates=list(used.values()))


def write_picks(edition: IndonesiaEdition, candidates: list[Candidate], out_dir: Path) -> Path:
    picks = picks_from_edition(edition, candidates)
    path = out_dir / "picks" / "indonesia.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "edition_date": picks.edition_date,
        "stories": [asdict(pick) for pick in picks.stories],
        "candidates": [_candidate_to_dict(candidate) for candidate in picks.candidates],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_picks(path: Path, day: date) -> Picks | None:
    """Today's Nusantara Daily picks, or None when missing, stale, or unreadable."""
    if not path.is_file():
        print(
            "  No Nusantara Daily picks found; Indonesia stories chosen independently", flush=True
        )
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        picks = Picks(
            edition_date=payload["edition_date"],
            stories=[Pick(**item) for item in payload["stories"]],
            candidates=[_candidate_from_dict(item) for item in payload["candidates"]],
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"  WARNING: unreadable Nusantara Daily picks ({exc!r}); ignoring them", flush=True)
        return None
    if picks.edition_date != day.isoformat():
        print(
            f"  Nusantara Daily picks are for {picks.edition_date}, not {day.isoformat()}; "
            "ignoring them",
            flush=True,
        )
        return None
    if not picks.by_role("lead"):
        return None
    return picks


def merge_candidates(candidates: list[Candidate], picks: Picks | None) -> list[Candidate]:
    """Add the articles Nusantara Daily used, so the standard editor can cite them too."""
    if picks is None:
        return candidates
    seen = {canonical_url_key(candidate.url) for candidate in candidates}
    merged = list(candidates)
    for candidate in picks.candidates:
        key = canonical_url_key(candidate.url)
        if key in seen:
            continue
        seen.add(key)
        merged.append(candidate)
    return merged


def alignment_prompt(picks: Picks | None) -> str:
    """Extra instructions for the standard editor; empty when there are no picks."""
    if picks is None:
        return ""
    listing = [
        {
            "role": ROLE_NAMES[pick.role],
            "topic_key": pick.topic_key,
            "her_headline_in_bahasa": pick.headline,
            "candidate_urls": pick.urls,
        }
        for pick in picks.stories
    ]
    return (
        "\n\nINDONESIA SECTION: MATCH NUSANTARA DAILY\n"
        "The reader's mother gets Nusantara Daily, a Bahasa Indonesia edition, and he wants the "
        "same Indonesian news so they can talk about it. Her stories today are listed below in "
        "her edition's order. Build your Indonesia section from them:\n"
        "- Her lead's topic is your Indonesia lead.\n"
        "- Her secondary stories' topics are your secondary stories.\n"
        "- Her Speed reads are your Speed reads, as many as fit.\n"
        "- Use the same candidate URLs she used as your primary sources (they are in the "
        "candidate list). You may add other candidates about the same event.\n"
        "- Skip one of her stories only when its candidates don't give enough facts for a "
        "story in English; fill that slot with your next-best Indonesia story.\n"
        "- Write everything fresh for this reader in English and our voice. Her headlines are "
        "only there to identify the stories; never translate them word for word.\n"
        "- The Sweden section is unaffected.\n\n" + json.dumps(listing, ensure_ascii=False)
    )


def _shares_topic(story: Story, pick: Pick) -> bool:
    if pick.topic_key and story.topic_key and story.topic_key == pick.topic_key:
        return True
    pick_keys = {canonical_url_key(url) for url in pick.urls}
    return any(canonical_url_key(url) in pick_keys for url in _story_urls(story))


@dataclass(frozen=True)
class AlignmentReport:
    lead_matches: bool
    shared: int
    total: int
    missing: list[str]

    def text(self) -> str:
        lead = "same lead" if self.lead_matches else "different lead"
        lines = [f"{lead}; {self.shared} of {self.total} Nusantara Daily stories also covered"]
        if self.missing:
            lines.append("Not in the English edition: " + "; ".join(self.missing))
        return "\n".join(lines)


def alignment_report(section: CountrySection, picks: Picks) -> AlignmentReport:
    ours = [story for _, story in _section_stories(section)]
    lead_pick = picks.by_role("lead")[0]
    shared = 0
    missing: list[str] = []
    for pick in picks.stories:
        if any(_shares_topic(story, pick) for story in ours):
            shared += 1
        else:
            missing.append(pick.headline)
    return AlignmentReport(
        lead_matches=_shares_topic(section.lead, lead_pick),
        shared=shared,
        total=len(picks.stories),
        missing=missing,
    )
