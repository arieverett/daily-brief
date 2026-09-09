import json
from importlib.resources import files

from briefing.editorial import (
    EDITION_SCHEMA,
    INDONESIA_EDITION_SCHEMA,
    STORY_SCHEMA,
    clean_highlights,
)
from briefing.models import Story


def test_output_schema_has_no_unrendered_legacy_fields():
    assert "front_page" not in EDITION_SCHEMA["properties"]
    assert "front_page" not in INDONESIA_EDITION_SCHEMA["properties"]
    assert "why_it_matters" not in STORY_SCHEMA["properties"]
    assert "setup" in EDITION_SCHEMA["required"]
    assert "setup" in INDONESIA_EDITION_SCHEMA["required"]


def test_sample_fixture_uses_only_current_schema():
    sample_path = files("briefing").joinpath("data", "sample_edition.json")
    payload = json.loads(sample_path.read_text(encoding="utf-8"))

    assert "front_page" not in payload
    assert "bottom_line" not in payload
    assert "setup" in payload

    for country in ("sweden", "indonesia"):
        section = payload[country]
        stories = [section["lead"], *section.get("stories", []), *section.get("quick_hits", [])]
        assert all("why_it_matters" not in story for story in stories)


def test_highlights_are_optional_and_capped_at_three():
    highlights = STORY_SCHEMA["properties"]["highlights"]
    assert highlights["minItems"] == 0
    assert highlights["maxItems"] == 3


def test_cleanup_deduplicates_repeated_bullets():
    story = Story(
        headline="Bank changes policy",
        summary="The bank changed its lending policy after a board vote.",
        url="https://example.com/story",
        source="Example",
        label="MONEY",
        highlights=[
            "Applications will reopen on October 1 for small businesses.",
            "Applications will reopen on October 1 for small businesses.",
        ],
    )
    assert clean_highlights(story) == [
        "Applications will reopen on October 1 for small businesses."
    ]
