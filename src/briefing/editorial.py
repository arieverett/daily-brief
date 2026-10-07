"""Editorial policy, structured-output schemas, and post-generation quality gates.

This module is intentionally side-effect free. The editor imports its schemas and prompts
explicitly, and every generated edition passes through ``validate_edition`` before rendering.
"""

from __future__ import annotations

import re
from dataclasses import replace
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlparse

from .collect import normalized_title
from .models import Candidate, CountrySection, Edition, IndonesiaEdition, SourceLink, Story

SOURCE_LINK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source": {"type": "string"},
        "url": {"type": "string"},
    },
    "required": ["source", "url"],
}

STORY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "highlights": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
            "maxItems": 3,
        },
        "url": {"type": "string"},
        "source": {"type": "string"},
        "label": {"type": "string"},
        "source_links": {
            "type": "array",
            "items": SOURCE_LINK_SCHEMA,
            "minItems": 1,
            "maxItems": 4,
        },
        "topic_key": {"type": "string"},
    },
    "required": [
        "headline",
        "summary",
        "highlights",
        "url",
        "source",
        "label",
        "source_links",
        "topic_key",
    ],
}

COUNTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lead": STORY_SCHEMA,
        "stories": {"type": "array", "items": STORY_SCHEMA, "minItems": 2, "maxItems": 3},
        "quick_hits": {"type": "array", "items": STORY_SCHEMA, "minItems": 3, "maxItems": 5},
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
        "sweden": COUNTRY_SCHEMA,
        "indonesia": COUNTRY_SCHEMA,
        "setup": {"type": "string"},
    },
    "required": [
        "edition_date",
        "date_label",
        "subject",
        "preview_text",
        "sweden",
        "indonesia",
        "setup",
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
        "indonesia": COUNTRY_SCHEMA,
        "setup": {"type": "string"},
    },
    "required": [
        "edition_date",
        "date_label",
        "subject",
        "preview_text",
        "indonesia",
        "setup",
    ],
}

SYSTEM_PROMPT = """You edit a concise personal morning briefing about Sweden and Indonesia.
The editorial feel is Morning Brew: smart, witty, conversational, data-forward, and never breathless, with
the judgment of a serious financial newspaper about what matters. Significance beats novelty and virality.

REPORTING RULES
- Use only facts present in candidate metadata. Never invent a number, quote, consequence, motive, or event.
- Some candidates include article_excerpt, the opening of the article itself. Its facts count as candidate
  metadata; mine it for the names, figures, dates, reactions, and next steps that make a story worth reading.
- Treat the underlying event or policy debate, not an article URL, as the unit of a story. Cluster duplicate
  coverage and use one story slot per topic.
- Prefer Reuters/AP, official institutions, public broadcasters, established national outlets, and strong
  local reporting. Synthesize useful non-overlapping facts when several candidates cover the same event.
- Preserve each used candidate's exact source and URL in source_links. The primary candidate must also be
  the story's source and url. Never list a source that contributed no fact.
- Set topic_key to a short lowercase canonical event/debate identifier. The same event always gets the same key.
- A lead or secondary topic may not reappear as another story or Speed read.

WRITING RULES
- Lead with what changed, fast. Put the core event and the strongest useful number up top.
- Headlines are clear first, clever second, usually 5-12 words. Light wordplay is welcome when the meaning
  survives a quick skim. No clickbait, vague teases, or inflated stakes.
- Lead summaries are 110-170 words in 2-4 short paragraphs. Secondary summaries are 70-120 words in 2-3 short
  paragraphs. Separate paragraphs with one blank line.
- Every sentence must add a fact. Never restate the headline or an earlier paragraph in different words, and
  skip "looking ahead" lines unless the metadata gives a concrete next step (a date, vote, decision, or deadline).
- Bullets are optional and must complement the headline and summary. Use 0-3 complete sentences. Every bullet
  must add a materially new fact not already stated, implied, paraphrased, or summarized in the headline or
  summary: a name, figure, date, quote, reaction, or detail from the excerpt. Never use a bullet to recap the story.
- Prefer an unused #/count, %, or currency figure in bullets when the metadata supports one. Never repeat a
  summary number in a bullet merely to make it look data-forward. Zero bullets is better than filler.
- Speed reads are 1-2 sentences, roughly 20-45 words: a quick hook or wry framing plus the fact, with a useful
  number when available.
- Sources live in the links. Never say "according to Reuters", "per SVT", "Reuters reports", "coverage appears
  across", or similar source-as-narrator phrasing. Attribute statements, allegations, estimates, polls, and
  forecasts to the person or institution making the claim when needed.
- Avoid filler such as "the development comes as", "this underscores", "it remains to be seen", "amid ongoing
  monitoring", and wrap-up sentences that only announce that a topic is getting attention.
- Explain unfamiliar institutions or acronyms inline. Use clear American English. Never use an em dash; use a
  comma, colon, parentheses, or a new sentence instead.
- Formatting: the only markup allowed is **double asterisks** around a short bold run-in at the start of a
  paragraph (in summaries and setup). No other markdown, headings, links, or emoji anywhere.

EDITION SHAPE
- Each country gets one lead, 2-3 secondary stories, and 3-5 Speed reads from distinct topics.
- Include culture/lifestyle coverage when timely and credible, but never displace a materially more important story.
- Labels:
  - Lead and secondary stories get a kicker: 1-4 uppercase words that nod playfully to the story, Morning Brew
    style (for example RATE EXPECTATIONS, SEAT MATH, CHIP SHOT). A serious story (deaths, disasters, war,
    violence, crime, illness) gets a plain category label instead.
  - Speed reads use a plain category: POLITICS, MONEY, BUSINESS, TECH, CULTURE, MUSIC, FILM, FOOD, FASHION,
    LIFESTYLE, TRAVEL, TRANSPORT, SOCIETY, CRIME, WEATHER, HEALTH, SPORTS, or SOCCER.
- Subject is under 70 characters: short, intriguing, and true to the lead. A pun is fine if it's still clear
  what the lead story is. preview_text is under 140 characters and names the day's biggest stories plainly.
- setup appears right after a fixed "Good morning." line, so never open with a greeting. Write 2-4 sentences
  (35-70 words): start with a short **bold run-in** of 2-6 words, orient the reader to the day's biggest
  development in our voice, and end on a light one-line kicker when the news allows. Don't summarize every story.
"""

INDONESIA_SYSTEM_PROMPT = """Anda adalah editor Nusantara Daily, ringkasan berita harian pribadi tentang Indonesia.
Tulis dalam Bahasa Indonesia yang alami, jelas, ringkas, dan enak dibaca. Gaya editorialnya seperti Morning Brew:
berisi, cepat, berbasis data, santai, dan sesekali jenaka tanpa mengorbankan akurasi.

ATURAN PELIPUTAN
- Gunakan hanya fakta dalam metadata kandidat. Jangan mengarang angka, kutipan, dampak, motif, atau peristiwa.
- Sebagian kandidat menyertakan article_excerpt, yaitu pembuka artikel aslinya. Faktanya termasuk metadata
  kandidat; gali nama, angka, tanggal, reaksi, dan langkah berikutnya yang membuat berita layak dibaca.
- Anggap peristiwa atau debat kebijakan, bukan URL artikel, sebagai satu unit berita. Gabungkan liputan duplikat
  dan gunakan hanya satu slot untuk setiap topik.
- Utamakan Reuters/AP, lembaga resmi, media nasional tepercaya, dan media lokal dengan peliputan kuat.
- Jika beberapa kandidat menyumbang fakta yang berbeda, gabungkan fakta terbaik yang tidak tumpang tindih.
- Masukkan source dan URL persis dari setiap kandidat yang benar-benar dipakai ke source_links. Kandidat utama
  juga wajib menjadi source dan url utama. Jangan mencantumkan sumber yang tidak menyumbang fakta.
- Isi topic_key dengan penanda singkat peristiwa/debat dalam huruf kecil. Topik yang sama harus memakai key yang sama.
- Topik berita utama/tambahan tidak boleh muncul lagi sebagai berita lain atau Baca kilat.

ATURAN PENULISAN
- Mulai dengan apa yang berubah, langsung. Taruh inti berita dan angka terkuat di awal.
- Judul harus jelas dulu, cerdas kemudian, umumnya 5-12 kata. Permainan kata ringan boleh bila maknanya tetap
  jelas sekali baca. Hindari clickbait, teka-teki, dan dramatisasi.
- Ringkasan berita utama berisi 110-170 kata dalam 2-4 paragraf pendek. Ringkasan berita tambahan berisi 70-120
  kata dalam 2-3 paragraf pendek. Pisahkan paragraf dengan satu baris kosong.
- Setiap kalimat wajib menambah fakta. Jangan mengulang judul atau paragraf sebelumnya dengan kata lain, dan
  jangan menulis kalimat "ke depan" kecuali metadata memuat langkah konkret (tanggal, voting, keputusan, tenggat).
- Bullet bersifat opsional dan harus melengkapi judul dan ringkasan. Gunakan 0-3 kalimat lengkap. Setiap bullet
  wajib menambahkan fakta material yang benar-benar baru (nama, angka, tanggal, kutipan, reaksi, atau detail dari
  excerpt), bukan mengulang, menyiratkan ulang, atau memparafrase judul maupun ringkasan.
- Utamakan #/jumlah, %, Rp/IDR, $, atau angka mata uang lain yang belum dipakai di ringkasan. Jangan mengulang
  angka ringkasan hanya agar bullet terlihat berbasis data. Nol bullet lebih baik daripada filler.
- Baca kilat berisi 1-2 kalimat, sekitar 20-45 kata: pembuka singkat yang tajam atau jenaka ditambah faktanya,
  dengan angka berguna bila tersedia.
- Sumber ada di tautan. Jangan memakai "menurut Kompas", "dilansir Reuters", atau media sebagai narator. Jika
  klaim memerlukan atribusi, sebut orang atau lembaga yang membuat klaim tersebut.
- Hindari kalimat pengisi yang hanya mengatakan isu sedang mendapat perhatian atau menegaskan ulang hal yang sama.
- Jelaskan lembaga/singkatan yang kurang dikenal secara singkat. Jangan gunakan em dash; pakai koma, titik dua,
  tanda kurung, atau kalimat baru.
- Format: satu-satunya markup yang boleh adalah **tanda bintang ganda** di sekitar pembuka tebal singkat di awal
  paragraf (di ringkasan dan setup). Tanpa markdown lain, judul tambahan, tautan, atau emoji.

BENTUK EDISI
- Satu berita utama, 2-3 berita tambahan, dan 3-5 Baca kilat dari topik berbeda.
- Sertakan budaya/pop culture/gaya hidup bila aktual dan layak, tanpa menggusur berita yang jauh lebih penting.
- Prioritaskan Bandung bila relevan dan layak secara editorial, maksimal satu berita Bandung per edisi.
- Label:
  - Berita utama dan tambahan memakai kicker: 1-4 kata huruf kapital yang menyentil topiknya dengan jenaka ala
    Morning Brew (misalnya RUPIAH GALAU, PANEN CUAN, MACET LAGI). Berita serius (kematian, bencana, perang,
    kekerasan, kriminal, penyakit) memakai label kategori biasa.
  - Baca kilat memakai kategori biasa: POLITIK, EKONOMI, BISNIS, TEKNOLOGI, BUDAYA, MUSIK, FILM, KULINER, MODE,
    GAYA HIDUP, WISATA, TRANSPORTASI, SOSIAL, KRIMINAL, CUACA, KESEHATAN, OLAHRAGA, SEPAK BOLA.
- Subject maksimal 70 karakter: singkat, menggugah, dan setia pada berita utama. Permainan kata boleh asal tetap
  jelas apa berita utamanya. preview_text maksimal 140 karakter dan menyebut berita terbesar hari ini dengan lugas.
- setup muncul tepat setelah baris tetap "Selamat pagi.", jadi jangan membuka dengan salam. Tulis 2-4 kalimat
  (35-70 kata): mulai dengan **pembuka tebal** singkat 2-6 kata, arahkan pembaca ke perkembangan terbesar hari
  ini dengan suara kita, dan tutup dengan satu kalimat ringan bila beritanya memungkinkan. Jangan meringkas
  semua berita.
"""

TRACKING_PARAMS = {"oc", "ref", "smid", "partner", "cmpid", "srnd", "hl", "gl", "ceid"}
WORD_RE = re.compile(r"[\w%$]+", re.UNICODE)
DATA_RE = re.compile(
    r"(?:[$€£¥₹]\s?\d[\d.,]*)"
    r"|(?:\b(?:rp|idr|usd|sek|eur)\s?\d[\d.,]*)"
    r"|(?:\b\d[\d.,]*\s?(?:%|percent|persen|million|billion|trillion|juta|miliar|triliun)?\b)",
    re.IGNORECASE,
)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "into", "is", "it", "of", "on", "or", "that", "the", "their", "to", "was",
    "were", "will", "with", "yang", "dan", "di", "ke", "dari", "untuk", "pada", "ini",
    "itu", "dengan", "setelah", "akan", "telah", "sebagai", "atas", "oleh", "dalam",
    "news", "update", "report", "reports", "berita",
}


def prefix_indonesia_subject(subject: str) -> str:
    """Add stable branding while respecting the email-subject length limit."""
    if not subject.casefold().startswith("nusantara daily:"):
        subject = f"Nusantara Daily: {subject}"
    return subject[:70]


def canonical_url_key(url: str) -> str:
    """Normalize publisher URLs while dropping common tracking parameters."""
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
    """Index trusted candidates for exact and fuzzy link repair."""
    by_url: dict[str, Candidate] = {}
    by_title: dict[str, Candidate] = {}
    for candidate in candidates:
        canonical = canonical_url_key(candidate.url)
        by_url.setdefault(candidate.url, candidate)
        by_url.setdefault(canonical, candidate)
        by_url.setdefault(canonical.split("?")[0], candidate)
        by_title.setdefault(normalized_title(candidate.title), candidate)
    return by_url, by_title


def match_candidate(
    story: Story, by_url: dict[str, Candidate], by_title: dict[str, Candidate]
) -> Candidate | None:
    """Snap a generated story back to a candidate the model actually received."""
    canonical = canonical_url_key(story.url)
    for key in (story.url, story.url.strip(), canonical, canonical.split("?")[0]):
        if key in by_url:
            return by_url[key]

    headline_key = normalized_title(story.headline)
    if headline_key in by_title:
        return by_title[headline_key]

    best_score = 0.0
    best: Candidate | None = None
    for title_key, candidate in by_title.items():
        score = SequenceMatcher(None, headline_key, title_key).ratio()
        if score > best_score:
            best_score, best = score, candidate
    return best if best_score >= 0.75 else None


def _stem(token: str) -> str:
    """Crude suffix folding so "solved"/"solving" or "prize"/"prizes" count as the same word."""
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            break
    if token.endswith("e") and len(token) > 4:
        token = token[:-1]
    return token


def _tokens(text: str) -> set[str]:
    return {
        _stem(token)
        for token in WORD_RE.findall(text.casefold())
        if len(token) > 2 and token not in STOPWORDS
    }


def _data_markers(text: str) -> set[str]:
    return {
        re.sub(r"\s+", "", match.group(0).casefold()).rstrip(".,")
        for match in DATA_RE.finditer(text)
    }


# A bullet without a new figure must bring at least this many new content words, and new
# words must make up at least this share of it, or it is a recap of the headline/summary.
MIN_NEW_BULLET_WORDS = 3
MIN_NEW_BULLET_SHARE = 0.5


def clean_highlights(story: Story) -> list[str]:
    """Remove fragments and bullets that add little beyond the headline and summary."""
    context = f"{story.headline}\n{story.summary}"
    summary_tokens = _tokens(context)
    summary_data = _data_markers(context)
    summary_normalized = " ".join(WORD_RE.findall(context.casefold()))
    cleaned: list[str] = []
    seen: set[str] = set()

    for raw in story.highlights:
        item = raw.strip()
        if not item or item[-1] not in ".!?" or len(WORD_RE.findall(item)) < 4:
            continue

        normalized = " ".join(WORD_RE.findall(item.casefold()))
        if normalized in seen or (normalized and normalized in summary_normalized):
            continue

        bullet_tokens = _tokens(item)
        new_words = bullet_tokens - summary_tokens
        has_new_data = bool(_data_markers(item) - summary_data)
        if not has_new_data and (
            len(new_words) < MIN_NEW_BULLET_WORDS
            or len(new_words) / max(len(bullet_tokens), 1) < MIN_NEW_BULLET_SHARE
        ):
            continue

        cleaned.append(item)
        seen.add(normalized)
        if len(cleaned) == 3:
            break
    return cleaned


def clean_indonesia_highlights(story: Story) -> list[str]:
    """Apply the stricter Indonesia anti-recap gate and prioritize new data."""
    summary_tokens = _tokens(story.summary)
    summary_data = _data_markers(story.summary)
    kept: list[tuple[str, bool]] = []

    for item in clean_highlights(story):
        bullet_tokens = _tokens(item)
        if not bullet_tokens:
            continue

        bullet_data = _data_markers(item)
        new_data = bullet_data - summary_data
        overlap = len(bullet_tokens & summary_tokens)
        denominator = min(len(bullet_tokens), len(summary_tokens))
        overlap_ratio = overlap / denominator if denominator else 0.0
        new_words = bullet_tokens - summary_tokens

        if bullet_data and not new_data:
            continue
        if not bullet_data and (overlap >= 2 or overlap_ratio >= 0.25 or len(new_words) < 4):
            continue

        kept.append((item, bool(new_data)))

    kept.sort(key=lambda pair: pair[1], reverse=True)
    return [item for item, _ in kept[:3]]


def _same_topic(left: Story, right: Story) -> bool:
    left_key = " ".join(WORD_RE.findall(left.topic_key.casefold()))
    right_key = " ".join(WORD_RE.findall(right.topic_key.casefold()))
    if left_key and right_key and left_key == right_key:
        return True

    left_tokens = _tokens(left.topic_key or left.headline)
    right_tokens = _tokens(right.topic_key or right.headline)
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    return len(overlap) >= 2 and len(overlap) / min(len(left_tokens), len(right_tokens)) >= 0.6


def _candidate_from_url(url: str, by_url: dict[str, Candidate]) -> Candidate | None:
    canonical = canonical_url_key(url)
    for key in (url, url.strip(), canonical, canonical.split("?")[0]):
        if key in by_url:
            return by_url[key]
    return None


def _candidate_same_topic(candidate: Candidate, story: Story) -> bool:
    candidate_key = canonical_url_key(candidate.url)
    candidate_keys = {candidate.url, candidate_key, candidate_key.split("?")[0]}
    story_keys: set[str] = set()
    for url in {story.url, *(link.url for link in story.source_links)}:
        canonical = canonical_url_key(url)
        story_keys.update((url, canonical, canonical.split("?")[0]))
    if candidate_keys & story_keys:
        return True

    candidate_tokens = _tokens(candidate.title)
    story_tokens = _tokens(f"{story.topic_key} {story.headline}")
    if not candidate_tokens or not story_tokens:
        return False
    overlap = candidate_tokens & story_tokens
    return len(overlap) >= 2 and len(overlap) / min(len(candidate_tokens), len(story_tokens)) >= 0.5


def _quick_label(candidate: Candidate, localized: bool) -> str:
    text = f"{candidate.title} {candidate.summary}".casefold()
    categories = (
        ({"soccer", "football", "allsvenskan", "hammarby", "malmö ff"}, "SOCCER", "SEPAK BOLA"),
        ({"sport", "hockey", "shl", "olympic"}, "SPORTS", "OLAHRAGA"),
        ({"music", "musik", "album", "concert", "konsert", "singer"}, "MUSIC", "MUSIK"),
        ({"film", "cinema", "movie", "actor", "actress"}, "FILM", "FILM"),
        ({"fashion", "mode", "designer", "style"}, "FASHION", "MODE"),
        ({"art", "arts", "konst", "museum", "exhibition"}, "CULTURE", "BUDAYA"),
        ({"food", "restaurant", "chef", "kuliner", "mat"}, "FOOD", "KULINER"),
        ({"travel", "tourism", "hotel", "wisata"}, "TRAVEL", "WISATA"),
        ({"health", "medical", "kesehatan"}, "HEALTH", "KESEHATAN"),
        ({"tech", "technology", "ai", "digital"}, "TECH", "TEKNOLOGI"),
        ({"bank", "market", "stock", "tax", "economy", "business"}, "MONEY", "EKONOMI"),
        ({"election", "government", "parliament", "minister", "politik"}, "POLITICS", "POLITIK"),
    )
    for keywords, english, indonesian in categories:
        if any(keyword in text for keyword in keywords):
            return indonesian if localized else english
    return "SOSIAL" if localized else "SOCIETY"


def _quick_summary(candidate: Candidate) -> str:
    text = candidate.summary.strip()
    if len(_tokens(text)) < 7 or _tokens(text) == _tokens(candidate.title):
        text = candidate.title.strip()
    if len(text) > 260:
        sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
        text = sentence if len(sentence) >= 40 else text[:257].rstrip() + "..."
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _candidate_quick_hit(candidate: Candidate, localized: bool) -> Story:
    return Story(
        headline=candidate.title,
        summary=_quick_summary(candidate),
        url=candidate.url,
        source=candidate.source,
        label=_quick_label(candidate, localized),
        highlights=[],
        source_links=[SourceLink(source=candidate.source, url=candidate.url)],
        topic_key=normalized_title(candidate.title),
    )


EM_DASH_RE = re.compile(r"\s*[\u2014\u2015]\s*")


def strip_em_dashes(text: str) -> str:
    """House style bans em dashes; the model still slips them in, so swap for a comma."""
    return EM_DASH_RE.sub(", ", text)


def validate_edition(
    edition: Edition | IndonesiaEdition, candidates: list[Candidate]
) -> Edition | IndonesiaEdition:
    """Repair links, remove duplicate topics, and enforce editorial quality gates."""
    by_url, by_title = build_link_index(candidates)
    dropped = 0

    def fix(story: Story, *, strict_indonesia: bool) -> Story | None:
        nonlocal dropped
        primary = match_candidate(story, by_url, by_title)
        if primary is None:
            dropped += 1
            return None

        links: list[SourceLink] = []
        seen_urls: set[str] = set()
        for link in story.source_links:
            candidate = _candidate_from_url(link.url, by_url)
            if candidate is None or candidate.url in seen_urls:
                continue
            links.append(SourceLink(source=candidate.source, url=candidate.url))
            seen_urls.add(candidate.url)

        if primary.url not in seen_urls:
            links.insert(0, SourceLink(source=primary.source, url=primary.url))
        links = links[:4]

        story = replace(
            story,
            headline=strip_em_dashes(story.headline),
            summary=strip_em_dashes(story.summary),
            highlights=[strip_em_dashes(item) for item in story.highlights],
        )
        cleaned_story = replace(
            story,
            url=primary.url,
            source=primary.source or story.source,
            source_links=links,
            highlights=clean_highlights(story),
        )
        if strict_indonesia:
            cleaned_story = replace(
                cleaned_story,
                highlights=clean_indonesia_highlights(cleaned_story),
            )
        return cleaned_story

    def fix_list(stories: list[Story], *, strict_indonesia: bool) -> list[Story]:
        return [
            fixed
            for fixed in (fix(story, strict_indonesia=strict_indonesia) for story in stories)
            if fixed is not None
        ]

    def dedupe(stories: list[Story], used: list[Story]) -> list[Story]:
        kept: list[Story] = []
        for story in stories:
            if any(_same_topic(story, previous) for previous in [*used, *kept]):
                continue
            kept.append(story)
        return kept

    def backfill_quick_hits(
        country: str,
        lead: Story,
        stories: list[Story],
        quick_hits: list[Story],
        localized: bool,
    ) -> list[Story]:
        used = [lead, *stories, *quick_hits]
        for candidate in candidates:
            if len(quick_hits) >= 3:
                break
            if candidate.country != country:
                continue
            if any(_candidate_same_topic(candidate, story) for story in used):
                continue
            quick = _candidate_quick_hit(candidate, localized)
            quick_hits.append(quick)
            used.append(quick)
        return quick_hits

    def fix_section(
        section: CountrySection,
        country: str,
        *,
        localized: bool,
        strict_indonesia: bool,
    ) -> CountrySection:
        lead = fix(section.lead, strict_indonesia=strict_indonesia)
        stories = fix_list(section.stories, strict_indonesia=strict_indonesia)
        quick_hits = fix_list(section.quick_hits, strict_indonesia=strict_indonesia)

        if lead is None:
            if stories:
                lead, stories = stories[0], stories[1:]
            elif quick_hits:
                lead, quick_hits = quick_hits[0], quick_hits[1:]
            else:
                raise ValueError("A country section lost every story during link repair")

        stories = dedupe(stories, [lead])
        quick_hits = dedupe(quick_hits, [lead, *stories])
        quick_hits = backfill_quick_hits(country, lead, stories, quick_hits, localized)
        return replace(section, lead=lead, stories=stories, quick_hits=quick_hits)

    indonesia_only = isinstance(edition, IndonesiaEdition)
    indonesia = fix_section(
        edition.indonesia,
        "Indonesia",
        localized=indonesia_only,
        strict_indonesia=True,
    )
    changes: dict[str, object] = {"indonesia": indonesia}

    if isinstance(edition, Edition):
        changes["sweden"] = fix_section(
            edition.sweden,
            "Sweden",
            localized=False,
            strict_indonesia=False,
        )

    changes["setup"] = strip_em_dashes(edition.setup)

    if dropped:
        print(f"  Dropped {dropped} story link(s) the editor invented", flush=True)
    return replace(edition, **changes)
