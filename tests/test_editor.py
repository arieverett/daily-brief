"""The AI editor's output is repaired into a usable, correctly dated edition."""

import json
from datetime import date
from importlib.resources import files

import pytest

from briefing import editor
from briefing.models import Candidate


def _sample_payload() -> dict:
    sample_path = files("briefing").joinpath("data", "sample_edition.json")
    return json.loads(sample_path.read_text(encoding="utf-8"))


def _candidates_for(payload: dict) -> list[Candidate]:
    candidates: list[Candidate] = []
    for country_key, country in (("sweden", "Sweden"), ("indonesia", "Indonesia")):
        section = payload[country_key]
        stories = [section["lead"], *section["stories"], *section["quick_hits"]]
        for story in stories:
            candidates.append(
                Candidate(country=country, title=story["headline"], url=story["url"],
                          source=story["source"])
            )
        # Pad each country to the minimum pool size with distinct filler topics.
        for index in range(editor.MIN_CANDIDATES_PER_COUNTRY):
            candidates.append(
                Candidate(
                    country=country,
                    title=f"{country} filler topic number {index} about harbour cranes",
                    url=f"https://example.com/{country_key}/{index}",
                    source="Example",
                )
            )
    return candidates


def test_edition_date_comes_from_the_gate_not_the_model(monkeypatch):
    payload = _sample_payload()
    payload["edition_date"] = "September 29th"  # the model can't be trusted with this
    payload["date_label"] = "Someday"
    monkeypatch.setattr(editor, "_generate_structured_output", lambda **kwargs: payload)

    edition = editor.create_edition(_candidates_for(payload), "key", "gpt-5-mini",
                                    date(2026, 9, 29))
    assert edition.edition_date == "2026-09-29"
    assert edition.date_label == "Tuesday, September 29"


def test_indonesia_date_label_is_localized():
    assert editor.indonesia_date_label(date(2026, 9, 29)) == "Selasa, 29 September"
    assert editor.indonesia_date_label(date(2026, 10, 3)) == "Sabtu, 3 Oktober"


def test_unusable_editor_output_is_regenerated_once(monkeypatch):
    payload = _sample_payload()
    calls = []

    def flaky(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise json.JSONDecodeError("truncated", "", 0)
        return payload

    monkeypatch.setattr(editor, "_generate_structured_output", flaky)
    edition = editor.create_edition(_candidates_for(payload), "key", "gpt-5-mini",
                                    date(2026, 9, 29))
    assert len(calls) == 2
    assert edition.subject == payload["subject"]


def test_regeneration_gives_up_after_the_retry(monkeypatch):
    calls = []

    def broken(**kwargs):
        calls.append(1)
        raise json.JSONDecodeError("truncated", "", 0)

    monkeypatch.setattr(editor, "_generate_structured_output", broken)
    with pytest.raises(ValueError):
        editor.create_edition(_candidates_for(_sample_payload()), "key", "gpt-5-mini",
                              date(2026, 9, 29))
    assert len(calls) == editor.GENERATION_ATTEMPTS
