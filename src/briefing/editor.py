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

ENGLISH_VOICE_GUIDANCE = """VOICE AND TONE OVERRIDE
- Write like a staff journalist delivering the newsletter directly to readers. Report the news itself; do not describe the source material as source material.
- State facts directly: write "Volvo plans to build an energy park in Mariestad," not "Reporting says Volvo plans to build an energy park in Mariestad."
- Never use meta-reporting phrases such as "coverage reports", "coverage indicates", "coverage highlights", "coverage focuses on", "reporting states", "reporting indicates", "reporting notes", "the article says", "the story says", "the story frames", "the announcement adds", "the report says", or close variants.
- More broadly, do not make "coverage", "reporting", "the article", "the story", "the report", or "the announcement" the narrator or subject of a sentence. Sources belong in links and in necessary attribution for claims, polls, estimates, allegations, or forecasts.
- Use direct newsroom voice with occasional "we" or "you" only when it helps orient the reader. Never imply that we personally witnessed an event or did original reporting we did not do.
- Cut throat-clearing, source-description, and generic wrap-up sentences. Every sentence should deliver a fact, number, useful context, or a sharp observation.
- Keep the tone brisk, clear, conversational, and lightly witty in the spirit of Morning Brew. A natural pun or playful aside is welcome occasionally, but do not force one into every story and never joke about serious human harm.
- Prefer the shortest phrasing that preserves the important facts. If a sentence can lose words without losing information, tighten it.
- Keep the existing rule that bullets must add new information rather than recap the paragraph, and favor unused numbers, percentages, or currency figures when available.

STYLE EXAMPLES
Bad: "Recent coverage highlights how seat math may give the Sweden Democrats more influence. The story frames the election as decisive."
Good: "Sweden's tight election could give the Sweden Democrats outsized leverage in coalition talks, even without leading the vote. The real action starts once the seat math is in."
Bad: "Reporting indicates state spending approached Rp 2 quadrillion by July. Coverage provides a snapshot of budget execution."
Good: "Indonesia's state spending approached Rp 2 quadrillion by the end of July, putting the government's midyear budget execution in clearer view."
"""

INDONESIA_VOICE_GUIDANCE = """ARAH SUARA DAN GAYA
- Tulis seperti jurnalis redaksi yang menyampaikan berita langsung kepada pembaca. Ceritakan beritanya, jangan membahas bahan sumber sebagai bahan sumber.
- Nyatakan fakta secara langsung: tulis "Belanja negara mendekati Rp 2 kuadriliun hingga akhir Juli," bukan "Pemberitaan menunjukkan belanja negara mendekati Rp 2 kuadriliun."
- Jangan gunakan frasa meta seperti "menurut pemberitaan", "pemberitaan menunjukkan", "pemberitaan menyoroti", "laporan menyebut", "laporan menunjukkan", "artikel ini menyebut", "berita ini menyoroti", "cerita ini menggambarkan", "pengumuman tersebut menambahkan", atau variasi dekatnya.
- Secara umum, jangan jadikan "pemberitaan", "laporan", "artikel", "berita", atau "pengumuman" sebagai narator kalimat. Sumber tetap ada di tautan dan hanya disebut di teks bila atribusi memang diperlukan untuk klaim, survei, estimasi, tuduhan, atau proyeksi.
- Gunakan suara redaksi yang langsung, dengan "kita" atau sapaan pembaca sesekali jika membantu alur. Jangan memberi kesan bahwa redaksi menyaksikan peristiwa secara langsung atau melakukan peliputan asli yang tidak dilakukan.
- Pangkas pembukaan yang bertele-tele, penjelasan tentang sumber, dan kalimat penutup generik. Setiap kalimat harus memberi fakta, angka, konteks berguna, atau observasi singkat yang tajam.
- Nadanya cepat, jelas, santai, dan ringan seperti Morning Brew. Permainan kata atau seloroh boleh sesekali jika alami, tetapi jangan dipaksakan dan jangan digunakan untuk kematian, bencana, perang, korban kejahatan, atau penderitaan manusia.
- Pilih kalimat sesingkat mungkin tanpa membuang fakta penting.
- Pertahankan aturan bahwa bullet hanya berisi informasi baru, bukan mengulang paragraf, dan utamakan angka, persentase, atau nilai mata uang yang belum dipakai bila tersedia.
"""


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
        instructions=f"{SYSTEM_PROMPT}\n\n{ENGLISH_VOICE_GUIDANCE}",
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
        instructions=f"{INDONESIA_SYSTEM_PROMPT}\n\n{INDONESIA_VOICE_GUIDANCE}",
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
