from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import OpenAI

from .models import Candidate, Edition, edition_from_dict

STORY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "url": {"type": "string"},
        "source": {"type": "string"},
        "label": {"type": "string"},
    },
    "required": ["headline", "summary", "why_it_matters", "url", "source", "label"],
}

COUNTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lead": STORY_SCHEMA,
        "stories": {"type": "array", "items": STORY_SCHEMA, "minItems": 2, "maxItems": 4},
        "quick_hits": {"type": "array", "items": STORY_SCHEMA, "minItems": 5, "maxItems": 5},
    },
    "required": ["lead", "stories", "quick_hits"],
}

EDITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "edition_date": {"type": "string"},
        "date_label": {"type": "string"},
        "subject": {"type": "string"},
        "preview_text": {"type": "string"},
        "front_page": {"type": "array", "items": STORY_SCHEMA, "minItems": 3, "maxItems": 3},
        "sweden": COUNTRY_SCHEMA,
        "indonesia": COUNTRY_SCHEMA,
        "bottom_line": {"type": "string"},
    },
    "required": [
        "edition_date",
        "date_label",
        "subject",
        "preview_text",
        "front_page",
        "sweden",
        "indonesia",
        "bottom_line",
    ],
}

SYSTEM_PROMPT = """You are the editor of a concise personal daily briefing covering Sweden and Indonesia.
Your reader likes the front two pages of the Wall Street Journal and Morning Brew: high-signal, fast,
smart, conversational, and never breathless. Select significance over novelty or virality.

Rules:
- Use only facts present in the candidate metadata. Never invent a number, quote, implication, or event.
- Preserve the exact candidate URL and source for every selected story.
- Combine duplicate coverage into one story and prefer Reuters/AP, official institutions, public broadcasters,
  established national outlets, and well-sourced local outlets.
- Front page: exactly three distinct stories that convey the day's overall picture.
- Each country: one lead, 2-4 secondary stories, and exactly 5 quick hits. Do not repeat an item within a country.
- Include at least one culture story for each country daily. Culture includes pop culture, internet culture,
  music, film, television, books, arts, food, fashion, travel, and lifestyle.
- Labels should be short uppercase categories such as POLITICS, MONEY, STOCKHOLM, JAKARTA, SOCIETY, or WATCH.
- Headline: punchy but accurate, no clickbait. Summary: 1-2 sentences. Why it matters: one crisp sentence.
- Quick-hit summaries and why-it-matters fields should each be a single short sentence.
- Explain unfamiliar institutions or acronyms inline. Use clear American English.
- Subject should be under 70 characters and preview_text under 140 characters.
- The bottom line is 2-3 sentences connecting the day's highest-impact developments without forcing a theme.
- Never use em dashes. Use commas, colons, periods, or parentheses instead.
"""


def build_prompt(candidates: list[Candidate], timezone_name: str) -> str:
    local_now = datetime.now(ZoneInfo(timezone_name))
    payload = [candidate.prompt_dict() for candidate in candidates]
    return (
        f"Create the edition for {local_now:%Y-%m-%d}. Use date label {local_now:%A, %B %-d}. "
        "Candidate stories follow as JSON.\n\n" + json.dumps(payload, ensure_ascii=False)
    )


def validate_links(edition: Edition, candidates: list[Candidate]) -> None:
    allowed_urls = {item.url for item in candidates}
    stories = [*edition.front_page]
    for section in (edition.sweden, edition.indonesia):
        stories.extend([section.lead, *section.stories, *section.quick_hits])
    invalid = [story.url for story in stories if story.url not in allowed_urls]
    if invalid:
        raise ValueError(f"Editor returned {len(invalid)} links not present in candidates")


def create_edition(
    candidates: list[Candidate], api_key: str, model: str, timezone_name: str
) -> Edition:
    counts = {
        country: sum(1 for item in candidates if item.country == country)
        for country in ("Sweden", "Indonesia")
    }
    if min(counts.values()) < 9:
        raise RuntimeError(f"Not enough fresh stories to publish safely: {counts}")
    # Avoid the SDK's long default timeout/retry cycle hiding a stalled workflow.
    client = OpenAI(api_key=api_key, timeout=120.0, max_retries=1)
    response = client.responses.create(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=build_prompt(candidates, timezone_name),
        text={
            "format": {
                "type": "json_schema",
                "name": "daily_brief",
                "schema": EDITION_SCHEMA,
                "strict": True,
            }
        },
    )
    edition = edition_from_dict(json.loads(response.output_text))
    validate_links(edition, candidates)
    return edition
