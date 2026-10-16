from dataclasses import replace

from briefing.main import load_sample
from briefing.style_check import score_edition, words


def test_sample_edition_scorecard_flags_only_real_misses():
    card = score_edition(load_sample())
    assert card.checks > 30
    assert 0 < card.score <= 100
    assert all("em dash" not in miss for miss in card.misses)


def test_scorecard_catches_style_drift():
    edition = load_sample()
    lead = replace(
        edition.sweden.lead,
        summary="According to Reuters, it remains to be seen — honestly.",
        label="A VERY LONG KICKER LABEL",
    )
    weather = replace(edition.weather, summary="Just one paragraph, no run-in.")
    card = score_edition(replace(edition, sweden=replace(edition.sweden, lead=lead), weather=weather))
    joined = "\n".join(card.misses)
    for expected in ("Sweden lead: summary is", "kicker", "em dash", "source-as-narrator",
                     "filler phrase", "weather write-up has 1 paragraphs",
                     "weather paragraph without a bold run-in"):
        assert expected in joined
    assert card.report().startswith(f"House style: {card.score}%")


def test_word_count_ignores_markup():
    assert words("**Zoom out:** Rates rose 0.25% to 4.5%.") == 7
