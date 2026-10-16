"""Voice measurements from Morning Brew reference issues, and the same measurements of ours.

The house style was distilled from Morning Brew issues Ari shares. To keep checking the AI
editor against them over time (the "test" half of training the editor), each reference
issue is reduced to a handful of numbers: how long sentences and paragraphs run, how often
paragraphs open with a bold run-in, how much wit punctuation (parenthetical asides, questions,
ellipses) shows up, and how data-heavy the copy is. Only those numbers are stored, in
data/style_reference.json; the issues' text stays out of this public repo.

Add an issue:   python -m briefing.reference add path/to/issue.eml
Show averages:  python -m briefing.reference show
"""

from __future__ import annotations

import argparse
import email
import json
import re
import sys
from datetime import UTC, datetime
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from .models import Edition, NewsletterEdition

REFERENCE_PATH = Path(__file__).with_name("data") / "style_reference.json"
SENTENCE_RE = re.compile(r"[^.!?…]+[.!?…]+")
WORD_RE = re.compile(r"[A-Za-z0-9][\w'’%$.,-]*")
RUN_IN_RE = re.compile(r"^(?:\*\*)?[A-Z][^.:!?]{1,40}:(?:\*\*)?\s")
NUMBER_RE = re.compile(r"\d")
# Lines in a Morning Brew text part that are boilerplate, ads or games, not editorial copy.
SKIP_RE = re.compile(
    r"(sponsor|presented by|a message from|referral|subscribe|unsubscribe|view online|"
    r"advertis|share the brew|trivia|crossword|jigsaw|reader poll|©|https?://)",
    re.IGNORECASE,
)
MIN_PARAGRAPH_WORDS = 12

METRICS = {
    "words_per_sentence": "Words per sentence",
    "words_per_paragraph": "Words per paragraph",
    "run_in_share_pct": "Paragraphs opening with a run-in (%)",
    "asides_per_1000_words": "Parenthetical asides per 1,000 words",
    "questions_per_1000_words": "Questions per 1,000 words",
    "ellipses_per_1000_words": "Ellipses per 1,000 words",
    "numbers_per_100_words": "Words containing a figure, per 100",
    "contractions_per_100_words": "Contractions per 100 words",
}


def measure(paragraph_texts: list[str]) -> dict[str, float]:
    """Voice metrics over editorial paragraphs (markdown bold allowed)."""
    paras = [
        " ".join(text.split())
        for text in paragraph_texts
        if len(WORD_RE.findall(text.replace("**", ""))) >= MIN_PARAGRAPH_WORDS
    ]
    if not paras:
        return {}
    plain = [para.replace("**", "") for para in paras]
    words = [word for para in plain for word in WORD_RE.findall(para)]
    sentences = [s for para in plain for s in SENTENCE_RE.findall(para) if WORD_RE.search(s)]
    total = len(words)
    joined = " ".join(plain)
    return {
        "words_per_sentence": round(total / max(len(sentences), 1), 1),
        "words_per_paragraph": round(median(len(WORD_RE.findall(p)) for p in plain), 1),
        "run_in_share_pct": round(100 * mean(bool(RUN_IN_RE.match(p)) for p in paras), 1),
        "asides_per_1000_words": round(1000 * joined.count("(") / total, 1),
        "questions_per_1000_words": round(1000 * joined.count("?") / total, 1),
        "ellipses_per_1000_words": round(
            1000 * (joined.count("…") + joined.count("...")) / total, 1
        ),
        "numbers_per_100_words": round(100 * sum(bool(NUMBER_RE.search(w)) for w in words) / total, 1),
        "contractions_per_100_words": round(
            100 * sum(bool(re.search(r"\w['’](?:s|t|re|ve|ll|d|m)\b", w)) for w in words) / total,
            1,
        ),
    }


def issue_paragraphs(raw: bytes) -> tuple[dict[str, Any], list[str]]:
    """Editorial paragraphs from a Morning Brew .eml (its plain-text part)."""
    message = email.message_from_bytes(raw, policy=policy.default)
    body = message.get_body(preferencelist=("plain",))
    text = body.get_content() if body else ""
    paras = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not SKIP_RE.search(line)
    ]
    sent = message.get("Date")
    info = {
        "date": parsedate_to_datetime(sent).date().isoformat() if sent else "",
        "subject": str(message.get("Subject", "")).strip()[:120],
    }
    return info, paras


def load_reference(path: Path = REFERENCE_PATH) -> list[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("issues", [])
    except (OSError, ValueError):
        return []


def reference_averages(issues: list[dict[str, Any]]) -> dict[str, float]:
    averages = {}
    for key in METRICS:
        values = [issue["metrics"][key] for issue in issues if key in issue.get("metrics", {})]
        if values:
            averages[key] = round(mean(values), 1)
    return averages


def edition_paragraphs(edition: NewsletterEdition) -> list[str]:
    """English editorial copy only: the voice metrics are calibrated on English text."""
    if not isinstance(edition, Edition):
        return []
    texts = [edition.setup]
    for section in (edition.sweden, edition.indonesia):
        for story in (section.lead, *section.stories):
            texts.extend(re.split(r"\n\s*\n", story.summary))
            texts.extend(story.highlights)
        texts.extend(story.summary for story in section.quick_hits)
    if edition.weather and edition.weather.summary:
        texts.extend(re.split(r"\n\s*\n", edition.weather.summary))
    return texts


def compare_to_reference(edition: NewsletterEdition) -> str:
    """One line per metric: ours vs the reference average. Empty if not comparable."""
    issues = load_reference()
    ours = measure(edition_paragraphs(edition))
    if not issues or not ours:
        return ""
    reference = reference_averages(issues)
    lines = [f"Voice vs Morning Brew ({len(issues)} reference issues): ours / theirs"]
    for key, label in METRICS.items():
        if key in ours and key in reference:
            lines.append(f"  {label}: {ours[key]} / {reference[key]}")
    return "\n".join(lines)


def _add(paths: list[Path]) -> int:
    issues = load_reference()
    for path in paths:
        info, paras = issue_paragraphs(path.read_bytes())
        metrics = measure(paras)
        if not metrics:
            print(f"{path}: no editorial paragraphs found", file=sys.stderr)
            return 1
        issues = [issue for issue in issues if issue.get("subject") != info["subject"]]
        issues.append({**info, "added": datetime.now(UTC).date().isoformat(), "metrics": metrics})
        print(f"Added {info['date']} {info['subject']}: {metrics}")
    issues.sort(key=lambda issue: issue.get("date", ""))
    REFERENCE_PATH.write_text(
        json.dumps({"issues": issues}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Morning Brew reference measurements")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Measure .eml issues and store their numbers")
    add.add_argument("paths", nargs="+", type=Path)
    sub.add_parser("show", help="Print the reference averages")
    args = parser.parse_args(argv)
    if args.command == "add":
        return _add(args.paths)
    issues = load_reference()
    print(f"{len(issues)} reference issues")
    for key, value in reference_averages(issues).items():
        print(f"  {METRICS[key]}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
