"""The Sunday edition: one theme, explored through 3-5 local stories per section.

Morning Brew's Sunday issue skips the news roundup and goes deep on a single topic that is
having a moment (gaming the week GTA VI came out, say). Ours does the same for each edition:
the editor picks one cultural or lifestyle theme from the past week's coverage and tells it
through stories specific to each section's place (Sweden and Indonesia, Indonesia, or
Arizona and Colorado).

The result uses the same edition types as the daily brief (each section's first story is
the lead, the rest follow, no Speed reads) plus ``theme``, so images, weather, rendering and
delivery need nothing special.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from .editorial import (
    INDONESIA_SYSTEM_PROMPT,
    STORY_SCHEMA,
    SYSTEM_PROMPT,
    prefix_indonesia_subject,
    strip_em_dashes,
    validate_edition,
)
from .models import (
    Candidate,
    CountrySection,
    Edition,
    IndonesiaEdition,
    NewsletterEdition,
    RegionalEdition,
    RegionalProfile,
    story_from_dict,
)

MIN_STORIES = 3
MAX_STORIES = 5
# After link repair a section may lose a story the editor invented; fewer than this regenerates.
MIN_KEPT_STORIES = 2
MIN_CANDIDATES_PER_SECTION = 6
# A week of coverage, so a theme can build from more than one news cycle.
LOOKBACK_HOURS = 168
MAX_CANDIDATES = 100


@dataclass(frozen=True)
class Section:
    key: str  # schema key
    country: str  # the `country` of this section's candidates
    place: str  # what the stories must be about, for the prompt


def sections_for(edition_name: str, profile: RegionalProfile | None = None) -> list[Section]:
    if profile is not None:
        return [
            Section(
                region.key,
                region.name,
                f"{region.state} (the {region.name} area first, then anywhere in {region.state})"
                if region.state
                else region.name,
            )
            for region in profile.regions
        ]
    if edition_name == "indonesia":
        return [Section("indonesia", "Indonesia", "Indonesia")]
    return [Section("sweden", "Sweden", "Sweden"), Section("indonesia", "Indonesia", "Indonesia")]


def sunday_schema(sections: list[Section]) -> dict[str, Any]:
    section_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "stories": {
                "type": "array",
                "items": STORY_SCHEMA,
                "minItems": MIN_STORIES,
                "maxItems": MAX_STORIES,
            }
        },
        "required": ["stories"],
    }
    fixed = ["theme", "subject", "preview_text", "setup"]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **{name: {"type": "string"} for name in fixed},
            **{section.key: section_schema for section in sections},
        },
        "required": [*fixed, *(section.key for section in sections)],
    }


ENGLISH_SUNDAY_SHAPE = """SUNDAY EDITION SHAPE
It's Sunday, so there is no news roundup. Like Morning Brew's Sunday issue, the whole edition goes deep on ONE
theme that is having a moment, told through 3-5 stories per section about how that theme plays out in that
section's place. (Morning Brew once spent a Sunday on gaming the week GTA VI came out: how big gaming has
gotten, who's playing, the business behind it.)

CHOOSING THE THEME
- Look across the past week's candidates for a cultural or lifestyle subject with real momentum in EVERY
  section: arts and entertainment, music, film and TV, gaming, food and drink, fashion and design, athletics
  (pro, college, school, and amateur), schools and universities, local politics and civic life, the outdoors
  and travel, holidays and traditions, everyday tech, health and wellness trends.
- Prefer a theme with a hook this week (a big release, a season opening, a festival, a vote, a record, a
  holiday) and at least three distinct candidates about it in every section.
- Broad enough to fit every section, specific enough to be interesting: "Gaming's big moment", "Ski season",
  "Holiday markets", "The college sports money rush", never just "Culture" or "This week".
- Never a tragedy, crime, disaster, war, or a single breaking-news event as the theme.
- theme is 1-5 words in title case, without the word "Sunday".

THE STORIES
- Each section gets 3-5 stories, every one about the theme AND about that section's place. Use only that
  section's candidates; a story never appears in two sections.
- Each story is a different angle built on its own primary candidate (different url and topic_key): the hook
  this week, how big it's gotten (the numbers), the people and places behind it, the money, the culture
  around it, what's next. Order them so the section reads like one story told in chapters.
- The first story in each section is its lead: 110-170 words in 2-4 short paragraphs. The others are 70-130
  words in 2-3 short paragraphs. Bullets follow the usual rules.
- A story may tie its candidate's facts to the theme in our own words ("Stockholm's game studios are part of the
  same boom"), but every fact still comes from the metadata.
- Labels: every story gets a kicker (1-4 uppercase words with a playful nod to the story).
- setup appears right after a fixed "Good morning." line, so never open with a greeting. Write 2-4 sentences
  (35-70 words): start with a short **bold run-in** of 2-6 words, say what this Sunday's theme is and why
  it's having a moment, and tease what's ahead in each section without listing every story.
- Subject is under 70 characters and names the theme. preview_text is under 140 characters and says what the
  theme is and where it takes the reader.
"""

INDONESIA_SUNDAY_SHAPE = """BENTUK EDISI MINGGU
Hari ini Minggu, jadi tidak ada rangkuman berita harian. Seperti edisi Minggu Morning Brew, seluruh edisi
membahas mendalam SATU tema yang sedang naik daun, lewat 3-5 berita tentang bagaimana tema itu terasa di
Indonesia. (Morning Brew pernah memakai satu edisi Minggu untuk gaming saat GTA VI rilis: seberapa besar gaming
sekarang, siapa yang bermain, dan bisnis di baliknya.)

MEMILIH TEMA
- Telusuri kandidat sepekan terakhir untuk mencari topik budaya atau gaya hidup yang benar-benar sedang ramai:
  seni dan hiburan, musik, film dan serial, game, kuliner, mode dan desain, olahraga (profesional, kampus,
  sekolah, dan amatir), sekolah dan kampus, politik daerah dan kehidupan warga, wisata dan alam, hari raya dan
  tradisi, teknologi sehari-hari, tren kesehatan.
- Utamakan tema dengan pemicu pekan ini (rilis besar, musim baru, festival, keputusan, rekor, hari raya) dan
  setidaknya tiga kandidat berbeda tentangnya.
- Cukup luas untuk 3-5 berita, cukup spesifik agar menarik: "Demam Esports", "Musim Konser", "Kuliner Viral",
  bukan sekadar "Budaya" atau "Pekan Ini".
- Jangan pernah memilih tragedi, kriminal, bencana, perang, atau satu peristiwa breaking news sebagai tema.
- theme berisi 1-5 kata dengan huruf kapital di awal kata, tanpa kata "Minggu".

BERITA-BERITANYA
- Bagian indonesia berisi 3-5 berita, semuanya tentang tema itu DAN tentang Indonesia (utamakan Bandung bila ada).
- Setiap berita mengambil sudut berbeda dengan kandidat utamanya sendiri (url dan topic_key berbeda): pemicunya
  pekan ini, seberapa besar sekarang (angka), orang dan tempat di baliknya, uangnya, budayanya, apa selanjutnya.
  Urutkan agar terbaca seperti satu cerita dalam beberapa bab.
- Berita pertama adalah berita utama: 110-170 kata dalam 2-4 paragraf pendek. Lainnya 70-130 kata dalam 2-3
  paragraf pendek. Bullet mengikuti aturan biasa.
- Berita boleh mengaitkan fakta kandidat dengan tema memakai kata-kata kita sendiri, tetapi setiap fakta tetap
  berasal dari metadata.
- Label: setiap berita memakai kicker (1-4 kata huruf kapital yang menyentil topiknya dengan jenaka).
- setup muncul tepat setelah baris tetap "Selamat pagi.", jadi jangan membuka dengan salam. Tulis 2-4 kalimat
  (35-70 kata): mulai dengan **pembuka tebal** singkat 2-6 kata, sebutkan tema Minggu ini dan mengapa sedang
  ramai, lalu beri bocoran isi edisi tanpa mendaftar semua berita.
- Subject maksimal 70 karakter dan menyebut temanya. preview_text maksimal 140 karakter dan menjelaskan tema
  serta ke mana edisi ini membawa pembaca.
"""


def _rules(prompt: str, marker: str) -> str:
    """The daily rules up to (not including) the daily edition's shape section."""
    head, found, _ = prompt.partition(marker)
    if not found:
        raise AssertionError(f"daily prompt no longer has {marker!r}")
    return head.rstrip()


def instructions(
    edition_name: str, sections: list[Section], profile: RegionalProfile | None
) -> str:
    from .editor import ENGLISH_VOICE_GUIDANCE, INDONESIA_VOICE_GUIDANCE
    from .regional import system_prompt, voice_guidance

    if edition_name == "indonesia":
        return "\n\n".join(
            [
                _rules(INDONESIA_SYSTEM_PROMPT, "BENTUK EDISI"),
                INDONESIA_SUNDAY_SHAPE,
                INDONESIA_VOICE_GUIDANCE,
            ]
        )
    if profile is not None:
        rules, voice = _rules(system_prompt(profile), "EDITION SHAPE"), voice_guidance(profile)
    else:
        rules, voice = _rules(SYSTEM_PROMPT, "EDITION SHAPE"), ENGLISH_VOICE_GUIDANCE
    listing = "\n".join(
        f"- {section.key}: candidates whose country is {section.country}; stories about {section.place}."
        for section in sections
    )
    return "\n\n".join(
        [
            rules,
            ENGLISH_SUNDAY_SHAPE,
            "SECTIONS\n" + listing,
            voice,
        ]
    )


def _prompt(candidates: list[Candidate], day: date, indonesian: bool) -> str:
    payload = json.dumps([candidate.prompt_dict() for candidate in candidates], ensure_ascii=False)
    if indonesian:
        return (
            f"Buat edisi Minggu untuk {day.isoformat()}. Berikut kandidat berita sepekan terakhir "
            f"dalam JSON.\n\n{payload}"
        )
    return (
        f"Create the Sunday edition for {day.isoformat()}. Candidate stories from the past week "
        f"follow as JSON.\n\n{payload}"
    )


def _section(payload: dict[str, Any]) -> CountrySection:
    stories = [story_from_dict(item) for item in payload["stories"]]
    if not stories:
        raise ValueError("A Sunday section came back with no stories")
    return CountrySection(lead=stories[0], stories=stories[1:], quick_hits=[])


def _check_sections(edition: NewsletterEdition) -> None:
    if isinstance(edition, RegionalEdition):
        named = [(region.name, section) for region, section in edition.named_sections()]
    elif isinstance(edition, Edition):
        named = [("Sweden", edition.sweden), ("Indonesia", edition.indonesia)]
    else:
        named = [("Indonesia", edition.indonesia)]
    for name, section in named:
        kept = 1 + len(section.stories)
        if kept < MIN_KEPT_STORIES:
            raise ValueError(f"Sunday section {name} kept only {kept} story after link repair")


def build_edition(
    payload: dict[str, Any],
    *,
    edition_name: str,
    day: date,
    profile: RegionalProfile | None,
) -> NewsletterEdition:
    """Turn the editor's JSON into the edition type the rest of the pipeline expects."""
    from .editor import indonesia_date_label, standard_date_label
    from .regional import prefix_subject

    theme = strip_em_dashes(" ".join(str(payload["theme"]).split()))
    if not theme:
        raise ValueError("The Sunday edition came back without a theme")
    common = {
        "edition_date": day.isoformat(),
        "preview_text": payload["preview_text"],
        "setup": payload["setup"],
        "theme": theme,
    }
    if profile is not None:
        return RegionalEdition(
            **common,
            date_label=standard_date_label(day),
            subject=prefix_subject(payload["subject"], profile.title),
            profile=profile,
            sections={region.key: _section(payload[region.key]) for region in profile.regions},
        )
    if edition_name == "indonesia":
        return IndonesiaEdition(
            **common,
            date_label=indonesia_date_label(day),
            subject=prefix_indonesia_subject(payload["subject"]),
            indonesia=_section(payload["indonesia"]),
        )
    return Edition(
        **common,
        date_label=standard_date_label(day),
        subject=payload["subject"],
        sweden=_section(payload["sweden"]),
        indonesia=_section(payload["indonesia"]),
    )


def create_sunday_edition(
    candidates: list[Candidate],
    *,
    edition_name: str,
    api_key: str,
    model: str,
    day: date,
    profile: RegionalProfile | None = None,
) -> NewsletterEdition:
    from .editor import OPENAI_MAX_RETRIES, _generate_structured_output, _with_regeneration

    sections = sections_for(edition_name, profile)
    countries = {section.country for section in sections}
    candidates = [candidate for candidate in candidates if candidate.country in countries]
    counts = {
        section.country: sum(c.country == section.country for c in candidates)
        for section in sections
    }
    if min(counts.values()) < MIN_CANDIDATES_PER_SECTION:
        raise RuntimeError(f"Not enough candidate stories for a Sunday edition: {counts}")

    def build() -> NewsletterEdition:
        payload = _generate_structured_output(
            api_key=api_key,
            model=model,
            instructions=instructions(edition_name, sections, profile),
            prompt=_prompt(candidates, day, edition_name == "indonesia"),
            schema=sunday_schema(sections),
            schema_name="sunday_brief",
            retries=OPENAI_MAX_RETRIES,
        )
        edition = build_edition(payload, edition_name=edition_name, day=day, profile=profile)
        validated = validate_edition(edition, candidates, themed=True)
        _check_sections(validated)
        return replace(validated, theme=edition.theme)

    return _with_regeneration(build)
