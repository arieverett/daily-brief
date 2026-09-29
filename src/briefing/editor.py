"""AI-editor orchestration for the two newsletter editions."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any, TypeVar

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
OPENAI_MAX_RETRIES = 1
# Extra full regenerations when the model's output can't be parsed or validated
# (e.g. a truncated response or a section whose every link was invented).
GENERATION_ATTEMPTS = 2

T = TypeVar("T")

INDONESIAN_DAYS = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
INDONESIAN_MONTHS = (
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

ENGLISH_VOICE_GUIDANCE = """VOICE: MORNING BREW
Write like a sharp, witty Morning Brew staff writer who knows Sweden and Indonesia cold and is talking straight
to one smart reader over coffee. Every story should leave the reader better informed and a little amused, in
that order.

WHO'S TALKING
- The newsletter speaks as "we" and talks to the reader as "you" ("Here's the deal," "If you've been waiting
  for rates to drop...," "We'd keep an eye on..."). Use it where it's natural, a few times per edition, not in
  every sentence.
- "We" are the editors: we explain, connect the dots, and say plainly why something matters. Never claim
  reporting we didn't do or imply we were there: no "we spoke to", "we visited", "we've learned", "sources
  tell us".
- Sound like the expert friend: confident, specific, and allergic to jargon (translate it in a few words when
  you must use it).

HOW A STORY READS
- Open with a hook that carries the news in the same breath: an everyday or pop-culture analogy, a wry framing
  of the stakes, or a striking number. The hook must be accurate and must set up the fact, never replace it.
- Keep paragraphs short, 1-3 sentences.
- After the opening paragraph, start most paragraphs with a bold run-in in double asterisks, such as
  **Zoom out:**, **The catch:**, **Why it matters:**, **For context,** **Meanwhile...**, **Not so fast:**,
  **Looking ahead...**, or **Bottom line:**. Vary them and never repeat one within a story. A local-flavored
  run-in is fun now and then when it genuinely fits.
- End on what happens next or a crisp kicker line, never on a restatement of the story.
- Parenthetical asides are where the wit lives: a quick wink that also adds a real observation.
- Contractions, plain words, active verbs. Mix sentence lengths; a four-word sentence can land a point.
- State facts directly: write "Volvo plans to build an energy park in Mariestad," not "Reporting says Volvo
  plans to build an energy park in Mariestad." Never make "coverage", "reporting", "the article", "the story",
  "the report", or "the announcement" the narrator or subject of a sentence.

HUMOR RULES
- Aim for about one clever moment per story (a pun, an analogy, a dry aside), not a joke in every line. Wit
  comes from how real facts are framed; never invent a detail, quote, reaction, or number to set up a joke.
- Serious stories (deaths, disasters, war, violence, crime victims, illness, human suffering) get a straight,
  humane voice with no jokes anywhere: headline, kicker, or aside.
- Aim the wit at situations, institutions, markets, and the absurd, never at people for who they are.

STYLE EXAMPLES (invented facts, for tone only; never reuse their wording, jokes, or facts)
Flat: "The Riksbank cut its policy rate by 0.25 percentage points to 1.75% on Wednesday, citing lower inflation."
Our voice: "Swedish borrowers, you can exhale (a little). The Riksbank trimmed its policy rate by a quarter
point to 1.75% on Wednesday, saying inflation is finally behaving.

**Not so fast:** The bank's governor said further cuts depend on a steadier krona, so maybe hold off on
pricing that summer cabin."
Flat speed read: "Spotify reported record quarterly profit of SEK 4 billion."
Our voice: "Spotify's biggest hit this quarter wasn't a song: the streamer posted a record SEK 4 billion profit."
Flat setup: "Today's newsletter covers Riksbank rates, Jakarta transit, and a Swedish film award."
Our voice: "**Cheaper money, faster trains.** Sweden's central bank just handed borrowers a little breathing
room, and Jakarta is betting big on pulling commuters out of traffic. Plus, a Swedish film is collecting
awards faster than we collect open browser tabs."
"""

INDONESIA_VOICE_GUIDANCE = """SUARA: MORNING BREW VERSI INDONESIA
Tulis seperti penulis Morning Brew yang cerdas dan jenaka, paham Indonesia luar dalam, dan sedang bercerita
langsung kepada satu pembaca yang pintar sambil ngopi pagi. Setiap berita harus membuat pembaca lebih paham dan
sedikit tersenyum, dengan urutan itu.

SIAPA YANG BICARA
- Newsletter berbicara sebagai "kita" dan menyapa pembaca sebagai "Anda": santai dan hangat, tetapi tetap sopan.
  Pakai secara alami, beberapa kali per edisi, bukan di setiap kalimat.
- "Kita" adalah redaksi: kita menjelaskan, menghubungkan titik-titik, dan mengatakan dengan jelas mengapa
  sesuatu penting. Jangan pernah mengklaim peliputan yang tidak kita lakukan atau memberi kesan kita ada di
  lokasi: tidak ada "kami mewawancarai", "kami mengunjungi", "sumber kami mengatakan".
- Terdengar seperti teman yang ahli: yakin, spesifik, dan anti-jargon (jelaskan singkat bila istilah teknis
  memang harus dipakai).

CARA MENULIS BERITA
- Buka dengan pengait yang langsung membawa beritanya: analogi sehari-hari atau budaya pop, cara pandang yang
  jenaka tentang taruhannya, atau angka yang mencolok. Pengait harus akurat dan mengantar fakta, bukan
  menggantikannya.
- Paragraf pendek, 1-3 kalimat.
- Setelah paragraf pembuka, mulai sebagian besar paragraf dengan pembuka tebal dalam tanda bintang ganda,
  misalnya **Konteksnya:**, **Masalahnya:**, **Kenapa penting:**, **Sementara itu...**, **Tunggu dulu:**,
  **Ke depan...**, atau **Intinya:**. Variasikan dan jangan ulangi dalam satu berita. Sesekali boleh ungkapan
  lokal yang benar-benar pas.
- Akhiri dengan apa yang terjadi selanjutnya atau kalimat penutup yang renyah, bukan pengulangan isi berita.
- Keterangan dalam kurung adalah tempat humor: sentilan singkat yang tetap menambah pengamatan nyata.
- Kalimat aktif, kata sederhana, panjang kalimat bervariasi.
- Nyatakan fakta secara langsung: tulis "Belanja negara mendekati Rp 2 kuadriliun hingga akhir Juli," bukan
  "Pemberitaan menunjukkan belanja negara mendekati Rp 2 kuadriliun." Jangan jadikan "pemberitaan", "laporan",
  "artikel", "berita", atau "pengumuman" sebagai narator atau subjek kalimat.

ATURAN HUMOR
- Sekitar satu momen cerdas per berita (permainan kata, analogi, sentilan kering), bukan lelucon di setiap
  kalimat. Humor berasal dari cara membingkai fakta nyata; jangan mengarang detail, kutipan, reaksi, atau angka
  demi lelucon.
- Berita serius (kematian, bencana, perang, kekerasan, korban kejahatan, penyakit, penderitaan manusia) ditulis
  lugas dan manusiawi tanpa lelucon di mana pun: judul, kicker, maupun keterangan.
- Arahkan humor ke situasi, lembaga, pasar, dan hal-hal absurd, bukan ke orang karena siapa mereka.

CONTOH GAYA (fakta karangan, hanya untuk nada; jangan pakai ulang kata, lelucon, atau faktanya)
Datar: "Bank Indonesia menahan suku bunga acuan di 5,75% pada Rabu."
Suara kita: "Kalau Anda berharap cicilan KPR turun bulan ini, simpan dulu kalkulatornya. Bank Indonesia menahan
suku bunga acuan di 5,75% pada Rabu.

**Kenapa ditahan:** Gubernur BI mengatakan rupiah masih perlu dijaga, jadi pemangkasan harus menunggu sampai
mata uang lebih tenang."
Datar (Baca kilat): "Penjualan mobil nasional naik 12% pada Agustus."
Suara kita: "Siap-siap jalanan makin ramai: penjualan mobil nasional naik 12% pada Agustus."
Datar (setup): "Edisi hari ini membahas suku bunga, MRT Jakarta, dan festival film."
Suara kita: "**Uang tetap mahal, kereta makin cepat.** Bank Indonesia belum mau melonggarkan dompet kita, sementara
Jakarta bertaruh besar untuk menarik komuter keluar dari macet. Plus, sebuah film lokal sedang panen penghargaan."
"""


def standard_date_label(day: date) -> str:
    return f"{day:%A, %B} {day.day}"


def indonesia_date_label(day: date) -> str:
    return f"{INDONESIAN_DAYS[day.weekday()]}, {day.day} {INDONESIAN_MONTHS[day.month - 1]}"


def _standard_prompt(candidates: list[Candidate], day: date) -> str:
    payload = [candidate.prompt_dict() for candidate in candidates]
    return (
        f"Create the edition for {day.isoformat()}. "
        f"Use date_label '{standard_date_label(day)}'. "
        "Candidate stories follow as JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _indonesia_prompt(candidates: list[Candidate], day: date) -> str:
    payload = [candidate.prompt_dict() for candidate in candidates]
    return (
        f"Buat edisi untuk {day.isoformat()}. "
        f"Gunakan date_label '{indonesia_date_label(day)}'. "
        "Berikut kandidat berita dalam JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _with_regeneration(build: Callable[[], T]) -> T:
    """Retry generation when the model returns output that can't be used."""
    for attempt in range(1, GENERATION_ATTEMPTS + 1):
        try:
            return build()
        except (ValueError, KeyError, TypeError) as exc:
            if attempt == GENERATION_ATTEMPTS:
                raise
            print(f"  Editor output unusable ({exc!r}); regenerating", flush=True)
    raise AssertionError("unreachable")


def _generate_structured_output(
    *,
    api_key: str,
    model: str,
    instructions: str,
    prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    retries: int,
    http_client: Any = None,
) -> dict[str, Any]:
    """Make one structured-output request and return the decoded JSON object."""
    client = OpenAI(
        api_key=api_key,
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=retries,
        http_client=http_client,
    )
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
    candidates: list[Candidate], api_key: str, model: str, day: date
) -> Edition:
    counts = _country_counts(candidates)
    if min(counts.values()) < MIN_CANDIDATES_PER_COUNTRY:
        raise RuntimeError(f"Not enough candidate stories to publish safely: {counts}")

    def build() -> Edition:
        payload = _generate_structured_output(
            api_key=api_key,
            model=model,
            instructions=f"{SYSTEM_PROMPT}\n\n{ENGLISH_VOICE_GUIDANCE}",
            prompt=_standard_prompt(candidates, day),
            schema=EDITION_SCHEMA,
            schema_name="daily_brief",
            retries=OPENAI_MAX_RETRIES,
        )
        # The date is ours, not the model's: it keys duplicate protection.
        edition = replace(
            edition_from_dict(payload),
            edition_date=day.isoformat(),
            date_label=standard_date_label(day),
        )
        validated = validate_edition(edition, candidates)
        assert isinstance(validated, Edition)
        return validated

    return _with_regeneration(build)


def create_indonesia_edition(
    candidates: list[Candidate], api_key: str, model: str, day: date
) -> IndonesiaEdition:
    indonesia_candidates = [candidate for candidate in candidates if candidate.country == "Indonesia"]
    if len(indonesia_candidates) < MIN_CANDIDATES_PER_COUNTRY:
        raise RuntimeError(
            "Not enough Indonesia candidate stories to publish safely: "
            f"{len(indonesia_candidates)}"
        )

    def build() -> IndonesiaEdition:
        payload = _generate_structured_output(
            api_key=api_key,
            model=model,
            instructions=f"{INDONESIA_SYSTEM_PROMPT}\n\n{INDONESIA_VOICE_GUIDANCE}",
            prompt=_indonesia_prompt(indonesia_candidates, day),
            schema=INDONESIA_EDITION_SCHEMA,
            schema_name="indonesia_daily_brief",
            retries=OPENAI_MAX_RETRIES,
        )
        edition = indonesia_edition_from_dict(payload)
        edition = replace(
            edition,
            subject=prefix_indonesia_subject(edition.subject),
            edition_date=day.isoformat(),
            date_label=indonesia_date_label(day),
        )
        validated = validate_edition(edition, indonesia_candidates)
        assert isinstance(validated, IndonesiaEdition)
        return validated

    return _with_regeneration(build)
