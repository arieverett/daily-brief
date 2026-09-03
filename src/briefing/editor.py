from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlparse
from zoneinfo import ZoneInfo

from openai import OpenAI

from .collect import normalized_title
from .models import (
    Candidate,
    CountrySection,
    Edition,
    IndonesiaEdition,
    Story,
    edition_from_dict,
    indonesia_edition_from_dict,
)

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

INDONESIA_EDITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "edition_date": {"type": "string"},
        "date_label": {"type": "string"},
        "subject": {"type": "string"},
        "preview_text": {"type": "string"},
        "front_page": {"type": "array", "items": STORY_SCHEMA, "minItems": 3, "maxItems": 3},
        "indonesia": COUNTRY_SCHEMA,
        "bottom_line": {"type": "string"},
    },
    "required": [
        "edition_date",
        "date_label",
        "subject",
        "preview_text",
        "front_page",
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

INDONESIA_SYSTEM_PROMPT = """Anda adalah editor Nusantara Daily, ringkasan berita harian pribadi
tentang Indonesia. Tulis seluruh keluaran dalam Bahasa Indonesia yang alami, jelas, dan mudah dibaca.
Gaya tulisan seperti Morning Brew: ringkas, cerdas, santai, dan sedikit jenaka. Gunakan beberapa
permainan kata yang enak dibaca, terutama pada judul atau transisi, tetapi jangan memaksakannya,
mengurangi akurasi, atau bercanda tentang tragedi dan berita serius.

Aturan:
- Gunakan hanya fakta dalam metadata kandidat. Jangan mengarang angka, kutipan, dampak, atau peristiwa.
- Pertahankan URL dan sumber kandidat secara persis untuk setiap berita terpilih.
- Utamakan berita penting daripada viralitas. Pilih Reuters/AP, lembaga resmi, media nasional tepercaya,
  dan media lokal dengan peliputan yang kuat.
- Front page harus berisi tepat tiga berita berbeda yang menggambarkan situasi Indonesia hari ini.
- Bagian Indonesia berisi satu berita utama, 2-4 berita tambahan, dan tepat 5 speed reads.
- Jangan mengulang berita di dalam bagian Indonesia.
- Sertakan 1-3 berita selebritas atau budaya pop setiap hari. Berita tersebut hanya masuk front page
  atau menjadi berita utama jika memang cukup penting; jika tidak, tempatkan sebagai berita tambahan
  atau speed read.
- Prioritaskan berita Bandung ketika relevan dan layak secara editorial, tetapi jangan memaksakannya.
  Maksimal satu berita Bandung per edisi.
- Label harus singkat dan memakai huruf kapital, misalnya POLITIK, EKONOMI, JAKARTA, BANDUNG,
  SELEBRITAS, MUSIK, FILM, atau BUDAYA.
- Judul harus menarik namun akurat. Ringkasan 1-2 kalimat. Mengapa penting: satu kalimat tajam.
- Ringkasan speed read dan kolom mengapa penting masing-masing hanya satu kalimat pendek.
- Jelaskan lembaga atau singkatan yang mungkin kurang dikenal secara singkat.
- Subject maksimal 70 karakter dan preview_text maksimal 140 karakter.
- Bottom line berisi 2-3 kalimat yang menghubungkan perkembangan terpenting tanpa memaksakan tema.
- Jangan gunakan em dash.
"""


def prefix_indonesia_subject(subject: str) -> str:
    if not subject.casefold().startswith("nusantara daily:"):
        subject = f"Nusantara Daily: {subject}"
    return subject[:70]


def build_prompt(candidates: list[Candidate], timezone_name: str) -> str:
    local_now = datetime.now(ZoneInfo(timezone_name))
    payload = [candidate.prompt_dict() for candidate in candidates]
    return (
        f"Create the edition for {local_now:%Y-%m-%d}. Use date label {local_now:%A, %B %-d}. "
        "Candidate stories follow as JSON.\n\n" + json.dumps(payload, ensure_ascii=False)
    )


TRACKING_PARAMS = {"oc", "ref", "smid", "partner", "cmpid", "srnd", "hl", "gl", "ceid"}


def canonical_url_key(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = parsed.path.rstrip("/")
    query = "&".join(
        sorted(
            f"{key}={value}"
            for key, value in parse_qsl(parsed.query)
            if key.casefold() not in TRACKING_PARAMS
            and not key.casefold().startswith("utm_")
        )
    )
    return f"{host}{path}?{query}" if query else f"{host}{path}"


def build_link_index(
    candidates: list[Candidate],
) -> tuple[dict[str, Candidate], dict[str, Candidate]]:
    by_url: dict[str, Candidate] = {}
    by_title: dict[str, Candidate] = {}
    for candidate in candidates:
        by_url.setdefault(candidate.url, candidate)
        by_url.setdefault(canonical_url_key(candidate.url), candidate)
        by_url.setdefault(canonical_url_key(candidate.url).split("?")[0], candidate)
        by_title.setdefault(normalized_title(candidate.title), candidate)
    return by_url, by_title


def match_candidate(
    story: Story, by_url: dict[str, Candidate], by_title: dict[str, Candidate]
) -> Candidate | None:
    for key in (
        story.url,
        story.url.strip(),
        canonical_url_key(story.url),
        canonical_url_key(story.url).split("?")[0],
    ):
        if key in by_url:
            return by_url[key]
    headline_key = normalized_title(story.headline)
    if headline_key in by_title:
        return by_title[headline_key]
    best_score, best = 0.0, None
    for title_key, candidate in by_title.items():
        score = SequenceMatcher(None, headline_key, title_key).ratio()
        if score > best_score:
            best_score, best = score, candidate
    return best if best_score >= 0.75 else None


def repair_links(
    edition: Edition | IndonesiaEdition, candidates: list[Candidate]
) -> Edition | IndonesiaEdition:
    by_url, by_title = build_link_index(candidates)
    dropped = 0

    def fix(story: Story) -> Story | None:
        nonlocal dropped
        candidate = match_candidate(story, by_url, by_title)
        if candidate is None:
            dropped += 1
            return None
        return replace(story, url=candidate.url, source=candidate.source or story.source)

    def fix_list(stories: list[Story]) -> list[Story]:
        return [fixed for fixed in (fix(story) for story in stories) if fixed is not None]

    def fix_section(section: CountrySection) -> CountrySection:
        lead = fix(section.lead)
        stories = fix_list(section.stories)
        quick_hits = fix_list(section.quick_hits)
        if lead is None:
            if stories:
                lead, stories = stories[0], stories[1:]
            elif quick_hits:
                lead, quick_hits = quick_hits[0], quick_hits[1:]
            else:
                raise ValueError("A country section lost every story during link repair")
        return replace(section, lead=lead, stories=stories, quick_hits=quick_hits)

    changes = {
        "front_page": fix_list(edition.front_page),
        "indonesia": fix_section(edition.indonesia),
    }
    if isinstance(edition, Edition):
        changes["sweden"] = fix_section(edition.sweden)
    repaired = replace(edition, **changes)
    if not repaired.front_page:
        raise ValueError("Editor returned no usable front page links")
    if dropped:
        print(f"  Dropped {dropped} story link(s) the editor invented", flush=True)
    return repaired


def validate_links(
    edition: Edition | IndonesiaEdition, candidates: list[Candidate]
) -> Edition | IndonesiaEdition:
    return repair_links(edition, candidates)


def create_edition(
    candidates: list[Candidate], api_key: str, model: str, timezone_name: str
) -> Edition:
    counts = {
        country: sum(1 for item in candidates if item.country == country)
        for country in ("Sweden", "Indonesia")
    }
    if min(counts.values()) < 9:
        raise RuntimeError(f"Not enough fresh stories to publish safely: {counts}")
    # The two-country schema is large. Give one request enough time to finish
    # instead of repeating the full generation after a short timeout.
    client = OpenAI(api_key=api_key, timeout=300.0, max_retries=0)
    request_kwargs = {
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "input": build_prompt(candidates, timezone_name),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "daily_brief",
                "schema": EDITION_SCHEMA,
                "strict": True,
            }
        },
    }
    if model.startswith(("gpt-5", "o")):
        request_kwargs["reasoning"] = {"effort": "low"}
    response = client.responses.create(**request_kwargs)
    edition = edition_from_dict(json.loads(response.output_text))
    return validate_links(edition, candidates)


def create_indonesia_edition(
    candidates: list[Candidate], api_key: str, model: str, timezone_name: str
) -> IndonesiaEdition:
    indonesia_candidates = [item for item in candidates if item.country == "Indonesia"]
    if len(indonesia_candidates) < 9:
        raise RuntimeError(
            f"Not enough fresh Indonesia stories to publish safely: {len(indonesia_candidates)}"
        )
    client = OpenAI(api_key=api_key, timeout=300.0, max_retries=1)
    local_now = datetime.now(ZoneInfo(timezone_name))
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
    input_prompt = (
        f"Buat edisi untuk {local_now:%Y-%m-%d}. Gunakan date_label '{date_label}'. "
        "Berikut kandidat berita dalam JSON.\n\n"
        + json.dumps([item.prompt_dict() for item in indonesia_candidates], ensure_ascii=False)
    )
    request_kwargs = {
        "model": model,
        "instructions": INDONESIA_SYSTEM_PROMPT,
        "input": input_prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "indonesia_daily_brief",
                "schema": INDONESIA_EDITION_SCHEMA,
                "strict": True,
            }
        },
    }
    if model.startswith(("gpt-5", "o")):
        request_kwargs["reasoning"] = {"effort": "low"}
    response = client.responses.create(**request_kwargs)
    edition = indonesia_edition_from_dict(json.loads(response.output_text))
    edition = replace(edition, subject=prefix_indonesia_subject(edition.subject))
    return validate_links(edition, indonesia_candidates)
