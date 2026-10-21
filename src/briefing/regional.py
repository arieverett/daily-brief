"""Config-driven English editions built from city/state regions (Two-Mile High).

The edition's branding and sections live in the `edition:` block of its sources file, so
adding a city is a config change: a new region entry plus feeds whose `country` is that
region's name. Everything else (collection, link repair, images, weather, delivery) is the
same pipeline the other editions use.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .editorial import COUNTRY_SCHEMA, SYSTEM_PROMPT, validate_edition
from .models import (
    Candidate,
    Region,
    RegionalEdition,
    RegionalProfile,
    country_from_dict,
)

SUBJECT_MAX = 70


def load_profile(sources_path: Path) -> RegionalProfile | None:
    """Read the optional `edition:` block; None for the fixed-layout editions."""
    config = yaml.safe_load(Path(sources_path).read_text(encoding="utf-8")) or {}
    raw = config.get("edition")
    if not raw:
        return None
    regions = [
        Region(
            key=str(item["key"]),
            name=str(item["name"]),
            state=str(item.get("state", "")),
            emoji=str(item.get("emoji", "")),
            focus=" ".join(str(item.get("focus", "")).split()),
        )
        for item in raw.get("regions", [])
    ]
    if not regions:
        raise ValueError(f"{sources_path}: the edition block needs at least one region")
    keys = [region.key for region in regions]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{sources_path}: region keys must be unique: {keys}")
    return RegionalProfile(
        edition=str(raw.get("name", "us")),
        title=str(raw["title"]),
        kicker=str(raw.get("kicker", "")),
        intro=str(raw.get("intro", "")),
        footer=str(raw.get("footer", "")),
        accent=str(raw.get("accent", "#075dcc")),
        regions=regions,
    )


def edition_schema(profile: RegionalProfile) -> dict[str, Any]:
    keys = [region.key for region in profile.regions]
    fixed = ["edition_date", "date_label", "subject", "preview_text", "setup"]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **{name: {"type": "string"} for name in fixed},
            **{key: COUNTRY_SCHEMA for key in keys},
        },
        "required": [*fixed, *keys],
    }


def _places(profile: RegionalProfile) -> str:
    names = [region.name for region in profile.regions]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def system_prompt(profile: RegionalProfile) -> str:
    """The standard edition's editorial rules, retargeted at this edition's regions."""
    base = SYSTEM_PROMPT.replace(
        "a concise personal morning briefing about Sweden and Indonesia.",
        f"{profile.title}, a concise personal morning briefing about {_places(profile)}.",
    ).replace("Each country gets", "Each region section gets").replace(
        'Never say "according to Reuters", "per SVT"', 'Never say "according to Reuters", "per 9NEWS"'
    )
    sections = "\n".join(
        f"- {region.key}: {region.heading}. {region.focus}".rstrip()
        for region in profile.regions
    )
    return (
        f"{base}\n"
        "SECTIONS\n"
        "Each candidate's `country` field names the section it belongs to. Fill each section only "
        "from its own candidates; a story never appears in two sections.\n"
        f"{sections}\n"
        "- Favor what changes daily life in the region (local government and ballot measures, "
        "housing, water, transit, schools, jobs, big local employers), then strong local business, "
        "sports, food, and culture. National stories belong only when there is a clear local angle "
        "in the metadata.\n"
        "- Sports gets at most one secondary story and one Speed read per section.\n"
        "- The subject names the lead's city when it isn't obvious from the story.\n"
    )


def voice_guidance(profile: RegionalProfile) -> str:
    """The standard edition's voice with region-appropriate wording and examples."""
    from .editor import ENGLISH_VOICE_GUIDANCE  # local import: editor imports this module

    head, _, _ = ENGLISH_VOICE_GUIDANCE.partition("STYLE EXAMPLES")
    head = head.replace("knows Sweden and Indonesia cold", f"knows {_places(profile)} cold").replace(
        'write "Volvo plans to build an energy park in Mariestad," not "Reporting says Volvo\n'
        '  plans to build an energy park in Mariestad."',
        'write "The city plans to add 40 miles of bike lanes," not "Reporting says the city\n'
        '  plans to add 40 miles of bike lanes."',
    )
    return head + """STYLE EXAMPLES (invented facts, for tone only; never reuse their wording, jokes, or facts)
Flat: "The Phoenix City Council approved a $40 million plan to plant shade trees along bus routes on Wednesday."
Our voice: "Waiting for the bus in July is about to get a little less like standing in a pizza oven. The
Phoenix City Council approved a $40 million plan on Wednesday to plant shade trees along bus routes.

**The catch:** The first trees go in next spring, and they'll take years to cast real shade, so keep the
water bottle handy."
Flat speed read: "Denver home prices fell 2% year over year in September."
Our voice: "Denver buyers finally caught a (small) break: home prices slipped 2% from a year earlier in September."
Flat setup: "Today's newsletter covers Arizona water rules, a Denver housing vote, and the Nuggets."
Our voice: "**Water, walls, and a win.** Arizona is rewriting who gets to build where the water runs thin, and
Denver voters get the final say on a housing plan. Plus, the Nuggets opened their season like they never left."
"""


def date_label(day: date) -> str:
    return f"{day:%A, %B} {day.day}"


def prefix_subject(subject: str, title: str) -> str:
    if not subject.casefold().startswith(f"{title.casefold()}:"):
        subject = f"{title}: {subject}"
    return subject[:SUBJECT_MAX]


def prompt(candidates: list[Candidate], day: date) -> str:
    payload = [candidate.prompt_dict() for candidate in candidates]
    return (
        f"Create the edition for {day.isoformat()}. "
        f"Use date_label '{date_label(day)}'. "
        "Candidate stories follow as JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def edition_from_payload(payload: dict[str, Any], profile: RegionalProfile) -> RegionalEdition:
    return RegionalEdition(
        edition_date=payload["edition_date"],
        date_label=payload["date_label"],
        subject=payload["subject"],
        preview_text=payload["preview_text"],
        setup=payload["setup"],
        profile=profile,
        sections={
            region.key: country_from_dict(payload[region.key]) for region in profile.regions
        },
    )


def create_regional_edition(
    candidates: list[Candidate],
    profile: RegionalProfile,
    api_key: str,
    model: str,
    day: date,
) -> RegionalEdition:
    from .editor import (  # local import: editor imports this module
        MIN_CANDIDATES_PER_COUNTRY,
        OPENAI_MAX_RETRIES,
        _generate_structured_output,
        _with_regeneration,
    )

    names = set(profile.region_names)
    candidates = [candidate for candidate in candidates if candidate.country in names]
    counts = {name: sum(c.country == name for c in candidates) for name in profile.region_names}
    if min(counts.values()) < MIN_CANDIDATES_PER_COUNTRY:
        raise RuntimeError(f"Not enough candidate stories to publish safely: {counts}")

    def build() -> RegionalEdition:
        payload = _generate_structured_output(
            api_key=api_key,
            model=model,
            instructions=f"{system_prompt(profile)}\n\n{voice_guidance(profile)}",
            prompt=prompt(candidates, day),
            schema=edition_schema(profile),
            schema_name="regional_brief",
            retries=OPENAI_MAX_RETRIES,
        )
        edition = edition_from_payload(payload, profile)
        # The date is ours, not the model's: it keys duplicate protection.
        edition = replace(
            edition,
            subject=prefix_subject(edition.subject, profile.title),
            edition_date=day.isoformat(),
            date_label=date_label(day),
        )
        validated = validate_edition(edition, candidates)
        assert isinstance(validated, RegionalEdition)
        return validated

    return _with_regeneration(build)
