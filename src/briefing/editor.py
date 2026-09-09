"""AI-editor orchestration for the two newsletter editions."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from openai import OpenAI

from .editorial import (
    EDITION_SCHEMA,
    INDONESIA_EDITION_SCHEMA,
    INDONESIA_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    prefix_indonesia_subject,
    validate_edition,
)
from .models import (
    Candidate,
    Edition,
    IndonesiaEdition,
    edition_from_dict,
    indonesia_edition_from_dict,
)

MIN_CANDIDATES_PER_COUNTRY = 6
OPENAI_TIMEOUT_SECONDS = 300.0


def _date_context(timezone_name: str) -> datetime:
    return datetime.now(ZoneInfo(timezone_name))


def _standard_prompt(candidates: list[Candidate], timezone_name: str) -> str:
    local_now = _date_context(timezone_name)
    payload = [candidate.prompt_dict() for candidate in candidates]
    return (
        f"Create the edition for {local_now:%Y-%m-%d}. "
        f"Use date_label '{local_now:%A, %B} {local_now.day}'. "
        "Candidate stories follow as JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _indonesia_prompt(candidates: list[Candidate], timezone_name: str) -> str:
    local_now = _date_context(timezone_name)
    days = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
    months = (
        "Januari",
        "Februari",
        "Maret",
        "April",
        "Mei",
        "Juni",
        "Juli",
        "Agustus",
        "September",
        "Oktober",
        "November",
        "Desember",
    )
    date_label = f"{days[local_now.weekday()]}, {local_now.day} {months[local_now.month - 1]}"
    payload = [candidate.prompt_dict() for candidate in candidates]
    return (
        f"Buat edisi untuk {local_now:%Y-%m-%d}. Gunakan date_label '{date_label}'. "
        "Berikut kandidat berita dalam JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _generate_structured_output(
    *,
    api_key: str,
    model: str,
    instructions: str,
    prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    retries: int,
) -> dict[str, Any]:
    """Make one structured-output request and return the decoded JSON object."""
    client = OpenAI(api_key=api_key, timeout=OPENAI_TIMEOUT_SECONDS, max_retries=retries)
    request: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                "strict": True,
            }
        },
    }
    if model.startswith(("gpt-5", "o")):
        request["reasoning"] = {"effort": "low"}

    response = client.responses.create(**request)
    return json.loads(response.output_text)


def _country_counts(candidates: list[Candidate]) -> dict[str, int]:
    return {
        country: sum(candidate.country == country for candidate in candidates)
        for country in ("Sweden", "Indonesia")
    }


def create_edition(
    candidates: list[Candidate], api_key: str, model: str, timezone_name: str
) -> Edition:
    counts = _country_counts(candidates)
    if min(counts.values()) < MIN_CANDIDATES_PER_COUNTRY:
        raise RuntimeError(f"Not enough candidate stories to publish safely: {counts}")

    payload = _generate_structured_output(
        api_key=api_key,
        model=model,
        instructions=SYSTEM_PROMPT,
        prompt=_standard_prompt(candidates, timezone_name),
        schema=EDITION_SCHEMA,
        schema_name="daily_brief",
        retries=0,
    )
    edition = edition_from_dict(payload)
    validated = validate_edition(edition, candidates)
    assert isinstance(validated, Edition)
    return validated


def create_indonesia_edition(
    candidates: list[Candidate], api_key: str, model: str, timezone_name: str
) -> IndonesiaEdition:
    indonesia_candidates = [candidate for candidate in candidates if candidate.country == "Indonesia"]
    if len(indonesia_candidates) < MIN_CANDIDATES_PER_COUNTRY:
        raise RuntimeError(
            "Not enough Indonesia candidate stories to publish safely: "
            f"{len(indonesia_candidates)}"
        )

    payload = _generate_structured_output(
        api_key=api_key,
        model=model,
        instructions=INDONESIA_SYSTEM_PROMPT,
        prompt=_indonesia_prompt(indonesia_candidates, timezone_name),
        schema=INDONESIA_EDITION_SCHEMA,
        schema_name="indonesia_daily_brief",
        retries=1,
    )
    edition = indonesia_edition_from_dict(payload)
    edition = replace(edition, subject=prefix_indonesia_subject(edition.subject))
    validated = validate_edition(edition, indonesia_candidates)
    assert isinstance(validated, IndonesiaEdition)
    return validated
