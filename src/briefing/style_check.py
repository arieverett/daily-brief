"""A daily scorecard of how well the AI editor followed the house style.

The editor's "training" is its instructions (editorial.py, editor.py, weather.py) plus the
Morning Brew reference notes they were distilled from. This module is the test side: it
measures each generated edition against the rules those instructions set, and the result
is posted on the run page. It never blocks a send; it makes drift visible so the
instructions can be tuned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import CountrySection, Edition, NewsletterEdition, Story

RUN_IN_RE = re.compile(r"^\*\*[^*]{2,40}\*\*")
NARRATOR_RE = re.compile(
    r"\b(according to|reports? that|reported that|per (?:the )?[A-Z]\w+|coverage|"
    r"menurut|dilansir|melaporkan|pemberitaan)\b",
    re.IGNORECASE,
)
FILLER_RE = re.compile(
    r"\b(the development comes as|this underscores|it remains to be seen|amid ongoing|"
    r"remains to be seen)\b",
    re.IGNORECASE,
)


def words(text: str) -> int:
    return len(re.findall(r"[\w'’%$.-]+", text.replace("**", "")))


def paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]


@dataclass
class Scorecard:
    checks: int = 0
    misses: list[str] = field(default_factory=list)

    def check(self, ok: bool, miss: str) -> None:
        self.checks += 1
        if not ok:
            self.misses.append(miss)

    @property
    def score(self) -> int:
        return round(100 * (self.checks - len(self.misses)) / self.checks) if self.checks else 100

    def report(self) -> str:
        head = f"House style: {self.score}% of {self.checks} checks passed"
        if not self.misses:
            return head
        shown = self.misses[:12]
        more = len(self.misses) - len(shown)
        return "\n".join([head, *shown, *([f"...and {more} more"] if more else [])])


def _story(card: Scorecard, story: Story, where: str, low: int, high: int, kicker: bool) -> None:
    count = words(story.summary)
    card.check(low <= count <= high, f"{where}: summary is {count} words (aim {low}-{high})")
    headline = words(story.headline)
    card.check(4 <= headline <= 14, f"{where}: headline is {headline} words")
    if kicker:
        label = words(story.label)
        card.check(1 <= label <= 4, f"{where}: kicker '{story.label}' is {label} words")
    later = paragraphs(story.summary)[1:]
    if later:
        with_run_in = sum(bool(RUN_IN_RE.match(part)) for part in later)
        # One or two per story, like Morning Brew (~13% of its paragraphs), not every one.
        card.check(
            1 <= with_run_in <= 2,
            f"{where}: {with_run_in} of {len(later)} later paragraphs open with a run-in (aim 1-2)",
        )
    text = " ".join([story.headline, story.summary, *story.highlights])
    card.check("—" not in text, f"{where}: em dash")
    card.check(not NARRATOR_RE.search(text), f"{where}: source-as-narrator phrasing")
    card.check(not FILLER_RE.search(text), f"{where}: filler phrase")


def _section(card: Scorecard, section: CountrySection, name: str) -> None:
    _story(card, section.lead, f"{name} lead", 100, 180, kicker=True)
    for index, story in enumerate(section.stories, 1):
        _story(card, story, f"{name} story {index}", 60, 130, kicker=True)
    card.check(
        2 <= len(section.stories) <= 3, f"{name}: {len(section.stories)} secondary stories"
    )
    card.check(
        3 <= len(section.quick_hits) <= 5, f"{name}: {len(section.quick_hits)} speed reads"
    )
    for index, story in enumerate(section.quick_hits, 1):
        count = words(story.summary)
        card.check(
            12 <= count <= 50, f"{name} speed read {index}: {count} words (aim 20-45)"
        )


def score_edition(edition: NewsletterEdition) -> Scorecard:
    card = Scorecard()
    setup_words = words(edition.setup)
    card.check(30 <= setup_words <= 80, f"setup is {setup_words} words (aim 35-70)")
    card.check(bool(RUN_IN_RE.match(edition.setup)), "setup doesn't open with a bold run-in")
    card.check(len(edition.subject) <= 70, f"subject is {len(edition.subject)} characters")
    if isinstance(edition, Edition):
        _section(card, edition.sweden, "Sweden")
    _section(card, edition.indonesia, "Indonesia")
    weather = edition.weather
    if weather is not None:
        card.check(bool(weather.summary), "weather write-up missing (forecasts only)")
        if weather.summary:
            parts = paragraphs(weather.summary)
            count = words(weather.summary)
            card.check(len(parts) == 2, f"weather write-up has {len(parts)} paragraphs (aim 2)")
            card.check(25 <= count <= 85, f"weather write-up is {count} words (aim 35-75)")
            card.check(
                all(RUN_IN_RE.match(part) for part in parts),
                "weather paragraph without a bold run-in",
            )
    return card
