from briefing.editorial import clean_indonesia_highlights
from briefing.models import Story


def _story(summary: str, highlights: list[str]) -> Story:
    return Story(
        headline="Indonesia test story",
        summary=summary,
        url="https://example.com/story",
        source="Example",
        label="NEWS",
        highlights=highlights,
    )


def test_indonesia_wildfire_recap_bullets_are_removed():
    story = _story(
        "Recent data show Indonesia has the world's highest wildfire emissions right now, "
        "largely because of fires and peatland burning. Malaysia has said it is waiting for "
        "Indonesia's approval to provide firefighting assistance.",
        [
            "Indonesia's current wildfire emissions lead global totals for the reporting period.",
            "Peatland burning is a major contributor to the emissions surge.",
            "Malaysia has said it is waiting for Indonesia's approval to assist firefighting efforts.",
        ],
    )
    assert clean_indonesia_highlights(story) == []


def test_indonesia_keeps_new_number_but_drops_repeated_bonus_and_resilience():
    story = _story(
        "Prabowo released Indonesia's Asian Games delegation and urged athletes to show resilience. "
        "The government announced a Rp3 billion bonus for gold medalists, and the send-off involved "
        "hundreds of athletes.",
        [
            "Prabowo led the send-off for Indonesia's Asian Games contingent of over 400 athletes.",
            "The government announced a Rp3 billion bonus for gold medal winners.",
            "Officials framed the delegation as a symbol of national resilience.",
        ],
    )
    assert clean_indonesia_highlights(story) == [
        "Prabowo led the send-off for Indonesia's Asian Games contingent of over 400 athletes."
    ]


def test_indonesia_prefers_new_quantitative_bullet():
    story = _story(
        "The government launched a new electric motorcycle incentive program nationwide.",
        [
            "Officials said the plan is intended to accelerate transport electrification.",
            "The program targets 100,000 electric motorcycles per year.",
        ],
    )
    assert clean_indonesia_highlights(story)[0] == (
        "The program targets 100,000 electric motorcycles per year."
    )
