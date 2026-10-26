"""The standard edition's Indonesia section follows Nusantara Daily's stories."""

import json
from datetime import UTC, date, datetime
from importlib.resources import files

from briefing import editor
from briefing.alignment import (
    alignment_prompt,
    alignment_report,
    load_picks,
    merge_candidates,
    write_picks,
)
from briefing.models import (
    Candidate,
    CountrySection,
    IndonesiaEdition,
    SourceLink,
    Story,
    edition_from_dict,
)

DAY = date(2026, 10, 27)


def _story(headline: str, url: str, topic: str) -> Story:
    return Story(
        headline=headline,
        summary="Ringkasan.",
        url=url,
        source="ANTARA",
        topic_key=topic,
        source_links=[SourceLink(source="ANTARA", url=url)],
    )


def _nusantara() -> tuple[IndonesiaEdition, list[Candidate]]:
    stories = [
        ("BI tahan suku bunga", "https://antaranews.com/bi?utm_source=x", "bi-rate"),
        ("Banjir di Bandung", "https://detik.com/banjir", "bandung-flood"),
        ("MRT fase dua", "https://kompas.com/mrt", "mrt-phase-two"),
        ("Festival film", "https://tempo.co/film", "film-festival"),
    ]
    edition = IndonesiaEdition(
        edition_date=DAY.isoformat(),
        date_label="Selasa, 27 Oktober",
        subject="Nusantara Daily: BI tahan bunga",
        preview_text="",
        setup="",
        indonesia=CountrySection(
            lead=_story(*stories[0]),
            stories=[_story(*stories[1]), _story(*stories[2])],
            quick_hits=[_story(*stories[3])],
        ),
    )
    candidates = [
        Candidate(
            country="Indonesia",
            title=headline,
            url=url,
            source="ANTARA",
            published_at=datetime(2026, 10, 27, 1, tzinfo=UTC),
            excerpt="Paragraf pembuka.",
        )
        for headline, url, _ in stories
    ]
    candidates.append(
        Candidate(country="Indonesia", title="Unused", url="https://x.id/unused", source="X")
    )
    return edition, candidates


def test_picks_round_trip_with_only_the_candidates_used(tmp_path):
    edition, candidates = _nusantara()
    path = write_picks(edition, candidates, tmp_path)
    picks = load_picks(path, DAY)
    assert picks is not None
    assert [pick.role for pick in picks.stories] == ["lead", "story", "story", "quick_hit"]
    assert len(picks.candidates) == 4  # the unused candidate isn't shared
    assert picks.candidates[0].excerpt == "Paragraf pembuka."
    assert picks.candidates[0].published_at == datetime(2026, 10, 27, 1, tzinfo=UTC)


def test_stale_missing_or_broken_picks_are_ignored(tmp_path):
    edition, candidates = _nusantara()
    path = write_picks(edition, candidates, tmp_path)
    assert load_picks(path, date(2026, 10, 28)) is None
    assert load_picks(tmp_path / "missing.json", DAY) is None
    path.write_text("{not json", encoding="utf-8")
    assert load_picks(path, DAY) is None


def test_merge_adds_pick_articles_once(tmp_path):
    edition, candidates = _nusantara()
    picks = load_picks(write_picks(edition, candidates, tmp_path), DAY)
    own = [Candidate(country="Indonesia", title="BI", url="https://antaranews.com/bi", source="A")]
    merged = merge_candidates(own, picks)
    # The tracking-parameter variant of the same article isn't added twice.
    assert len(merged) == 4
    assert merge_candidates(own, None) == own


def test_prompt_lists_her_stories_in_order(tmp_path):
    edition, candidates = _nusantara()
    picks = load_picks(write_picks(edition, candidates, tmp_path), DAY)
    prompt = alignment_prompt(picks)
    assert "MATCH NUSANTARA DAILY" in prompt
    listing = json.loads(prompt[prompt.index("[") :])
    assert [item["role"] for item in listing] == [
        "lead", "secondary story", "secondary story", "Speed read",
    ]
    assert alignment_prompt(None) == ""


def test_report_counts_shared_stories_by_url_or_topic(tmp_path):
    edition, candidates = _nusantara()
    picks = load_picks(write_picks(edition, candidates, tmp_path), DAY)
    ours = CountrySection(
        lead=_story("BI holds rates", "https://antaranews.com/bi", "bank-indonesia-hold"),
        stories=[_story("Bandung floods", "https://other.com/flood", "bandung-flood")],
        quick_hits=[_story("Coffee exports", "https://x.id/coffee", "coffee")],
    )
    report = alignment_report(ours, picks)
    assert report.lead_matches
    assert (report.shared, report.total) == (2, 4)
    assert report.missing == ["MRT fase dua", "Festival film"]
    assert "same lead; 2 of 4" in report.text()


def _sample_payload() -> dict:
    return json.loads(
        files("briefing").joinpath("data", "sample_edition.json").read_text(encoding="utf-8")
    )


def _sample_candidates(payload: dict) -> list[Candidate]:
    candidates = []
    for key, country in (("sweden", "Sweden"), ("indonesia", "Indonesia")):
        section = payload[key]
        for story in [section["lead"], *section["stories"], *section["quick_hits"]]:
            candidates.append(
                Candidate(country=country, title=story["headline"], url=story["url"],
                          source=story["source"])
            )
        for index in range(editor.MIN_CANDIDATES_PER_COUNTRY):
            candidates.append(
                Candidate(country=country, title=f"{country} filler {index} about cranes",
                          url=f"https://example.com/{key}/{index}", source="Example")
            )
    return candidates


def _picks_for_lead(tmp_path, payload: dict, lead_index: int):
    """Picks whose lead is the sample's Indonesia story at `lead_index`."""
    sample = edition_from_dict(payload).indonesia
    ordered = [sample.lead, *sample.stories, *sample.quick_hits]
    lead = ordered[lead_index]
    rest = [story for story in ordered if story is not lead]
    edition = IndonesiaEdition(
        edition_date=DAY.isoformat(), date_label="", subject="", preview_text="", setup="",
        indonesia=CountrySection(lead=lead, stories=rest[:2], quick_hits=rest[2:]),
    )
    return load_picks(write_picks(edition, _sample_candidates(payload), tmp_path), DAY)


def test_editor_prompt_carries_the_picks_and_a_matching_lead_is_kept(tmp_path, monkeypatch):
    payload = _sample_payload()
    picks = _picks_for_lead(tmp_path, payload, 0)
    prompts = []

    def fake(**kwargs):
        prompts.append(kwargs["prompt"])
        return payload

    monkeypatch.setattr(editor, "_generate_structured_output", fake)
    editor.create_edition(_sample_candidates(payload), "k", "m", DAY, picks=picks)
    assert len(prompts) == 1
    assert "MATCH NUSANTARA DAILY" in prompts[0]


def test_a_different_lead_is_regenerated_once_then_accepted(tmp_path, monkeypatch):
    payload = _sample_payload()
    picks = _picks_for_lead(tmp_path, payload, 1)  # her lead is our secondary story
    calls = []

    def fake(**kwargs):
        calls.append(1)
        return payload

    monkeypatch.setattr(editor, "_generate_structured_output", fake)
    edition = editor.create_edition(_sample_candidates(payload), "k", "m", DAY, picks=picks)
    assert len(calls) == editor.GENERATION_ATTEMPTS
    assert edition.indonesia.lead.headline == payload["indonesia"]["lead"]["headline"]
