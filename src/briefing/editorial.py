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
The editorial feel is Morning Brew meets the front pages of a serious financial newspaper: high-signal,
fast, conversational, data-forward, and never breathless. Significance beats novelty and virality.

REPORTING RULES
- Use only facts present in candidate metadata. Never invent a number, quote, consequence, motive, or event.
- Treat the underlying event or policy debate, not an article URL, as the unit of a story. Cluster duplicate
  coverage and use one story slot per topic.
- Prefer Reuters/AP, official institutions, public broadcasters, established national outlets, and strong
  local reporting. Synthesize useful non-overlapping facts when several candidates cover the same event.
- Preserve each used candidate's exact source and URL in source_links. The primary candidate must also be
  the story's source and url. Never list a source that contributed no fact.
- Set topic_key to a short lowercase canonical event/debate identifier. The same event always gets the same key.
- A lead or secondary topic may not reappear as another story or Speed read.

WRITING RULES
- Lead with what changed. Use active voice, concrete nouns, short sentences, and useful context.
- Headlines should be punchy but literal, usually 6-12 words. No clickbait, vague teases, or inflated stakes.
- Lead and secondary summaries are 3-5 sentences, roughly 70-115 words. Put the core event and strongest
  useful number in the paragraph when available.
- Bullets are optional and must complement the paragraph. Use 0-3 complete sentences. Every bullet must add
  a materially new fact not already stated, implied, paraphrased, or summarized in the paragraph.
- Prefer an unused #/count, %, or currency figure in bullets when the metadata supports one. Never repeat a
  paragraph number in a bullet merely to make it look data-forward. Zero bullets is better than filler.
- Speed reads are one self-contained sentence, ideally 18-35 words, with a useful number when available.
- Write as the newsletter, not as a media-summary bot. Never say "according to Reuters", "per SVT",
  "Reuters reports", "coverage appears across", or similar source-as-narrator phrasing. Attribute statements,
  allegations, estimates, polls, and forecasts to the person or institution making the claim when needed.
- Avoid filler such as "the development comes as", "this underscores", "amid ongoing monitoring", and generic
  final sentences that merely announce that a topic is receiving attention.
- A light turn of phrase is welcome when natural, but never joke about deaths, disasters, war, crime victims,
  or other serious human harm.
- Explain unfamiliar institutions or acronyms inline. Use clear American English. Never use an em dash.

EDITION SHAPE
- Each country gets one lead, 2-3 secondary stories, and 3-5 Speed reads from distinct topics.
- Include culture/lifestyle coverage when timely and credible, but never displace a materially more important story.
- Labels describe subject categories, not geography: POLITICS, MONEY, BUSINESS, TECH, CULTURE, MUSIC, FILM,
  FOOD, FASHION, LIFESTYLE, TRAVEL, TRANSPORT, SOCIETY, CRIME, WEATHER, HEALTH, SPORTS, or SOCCER.
- Subject is under 70 characters. preview_text is under 140 characters.
- setup is 2 concise sentences that orient the reader to the day's highest-impact developments without
  repeating full story summaries or forcing a theme.
"""

INDONESIA_SYSTEM_PROMPT = """Anda adalah editor Nusantara Daily, ringkasan berita harian pribadi tentang Indonesia.
Tulis dalam Bahasa Indonesia yang alami, jelas, ringkas, dan enak dibaca. Gaya editorialnya seperti Morning Brew:
berisi, cepat, berbasis data, santai, dan sesekali jenaka tanpa mengorbankan akurasi.

ATURAN PELIPUTAN
- Gunakan hanya fakta dalam metadata kandidat. Jangan mengarang angka, kutipan, dampak, motif, atau peristiwa.
- Anggap peristiwa atau debat kebijakan, bukan URL artikel, sebagai satu unit berita. Gabungkan liputan duplikat
  dan gunakan hanya satu slot untuk setiap topik.
- Utamakan Reuters/AP, lembaga resmi, media nasional tepercaya, dan media lokal dengan peliputan kuat.
- Jika beberapa kandidat menyumbang fakta yang berbeda, gabungkan fakta terbaik yang tidak tumpang tindih.
- Masukkan source dan URL persis dari setiap kandidat yang benar-benar dipakai ke source_links. Kandidat utama
  juga wajib menjadi source dan url utama. Jangan mencantumkan sumber yang tidak menyumbang fakta.
- Isi topic_key dengan penanda singkat peristiwa/debat dalam huruf kecil. Topik yang sama harus memakai key yang sama.
- Topik berita utama/tambahan tidak boleh muncul lagi sebagai berita lain atau Baca kilat.

ATURAN PENULISAN
- Mulai dengan apa yang berubah. Gunakan kalimat aktif, kata konkret, dan konteks yang benar-benar membantu.
- Judul menarik tetapi literal, umumnya 6-12 kata. Hindari clickbait dan dramatisasi.
- Ringkasan berita utama/tambahan berisi 3-5 kalimat, kira-kira 70-115 kata. Masukkan inti berita dan angka
  terkuat di paragraf bila tersedia.
- Bullet bersifat opsional dan harus melengkapi paragraf. Gunakan 0-3 kalimat lengkap. Setiap bullet wajib
  menambahkan fakta material yang benar-benar baru, bukan mengulang, menyiratkan ulang, atau memparafrase paragraf.
- Utamakan #/jumlah, %, Rp/IDR, $, atau angka mata uang lain yang belum dipakai di paragraf. Jangan mengulang
  angka paragraf hanya agar bullet terlihat berbasis data. Nol bullet lebih baik daripada filler.
- Baca kilat adalah satu kalimat mandiri, idealnya 18-35 kata, dengan angka berguna bila tersedia.
- Tulis langsung sebagai suara newsletter. Jangan memakai "menurut Kompas", "dilansir Reuters", atau media
  sebagai narator. Jika klaim memerlukan atribusi, sebut orang atau lembaga yang membuat klaim tersebut.
- Hindari kalimat pengisi yang hanya mengatakan isu sedang mendapat perhatian atau menegaskan ulang hal yang sama.
- Permainan kata ringan boleh jika alami, tetapi jangan bercanda tentang kematian, bencana, perang, korban kejahatan,
  atau penderitaan manusia.
- Jelaskan lembaga/singkatan yang kurang dikenal secara singkat. Jangan gunakan em dash.

BENTUK EDISI
- Satu berita utama, 2-3 berita tambahan, dan 3-5 Baca kilat dari topik berbeda.
- Sertakan budaya/pop culture/gaya hidup bila aktual dan layak, tanpa menggusur berita yang jauh lebih penting.
- Prioritaskan Bandung bila relevan dan layak secara editorial, maksimal satu berita Bandung per edisi.
- Label menjelaskan kategori isi, bukan lokasi: POLITIK, EKONOMI, BISNIS, TEKNOLOGI, BUDAYA, MUSIK, FILM,
  KULINER, MODE, GAYA HIDUP, WISATA, TRANSPORTASI, SOSIAL, KRIMINAL, CUACA, KESEHATAN, OLAHRAGA, SEPAK BOLA.
- Subject maksimal 70 karakter dan preview_text maksimal 140 karakter.
- setup berisi 2 kalimat ringkas yang mengarahkan pembaca ke perkembangan terpenting hari ini tanpa mengulang
  ringkasan berita atau memaksakan satu tema.
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


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in WORD_RE.findall(text.casefold())
        if len(token) > 2 and token not in STOPWORDS
    }


def _data_markers(text: str) -> set[str]:
    return {
        re.sub(r"\s+", "", match.group(0).casefold()).rstrip(".,")
        for match in DATA_RE.finditer(text)
    }


def clean_highlights(story: Story) -> list[str]:
    """Remove fragments and bullets that add little beyond the summary."""
    summary_tokens = _tokens(story.summary)
    summary_normalized = " ".join(WORD_RE.findall(story.summary.casefold()))
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
        if bullet_tokens and len(bullet_tokens - summary_tokens) < 2:
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
    changes: dict[str, CountrySection] = {"indonesia": indonesia}

    if isinstance(edition, Edition):
        changes["sweden"] = fix_section(
            edition.sweden,
            "Sweden",
            localized=False,
            strict_indonesia=False,
        )

    if dropped:
        print(f"  Dropped {dropped} story link(s) the editor invented", flush=True)
    return replace(edition, **changes)
