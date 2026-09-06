"""Production editorial overrides shared by both newsletter editions."""

from __future__ import annotations

import re
from dataclasses import replace

from . import editor
from .models import Candidate, CountrySection, Edition, IndonesiaEdition, SourceLink, Story

# Story bullets are optional. Empty is better than filler or repeated copy.
editor.STORY_SCHEMA["properties"]["highlights"]["minItems"] = 0
editor.STORY_SCHEMA["properties"]["highlights"]["maxItems"] = 3

# Ask the editor for five speed reads so post-generation topic dedupe still has room
# to leave at least three genuinely distinct items.
editor.COUNTRY_SCHEMA["properties"]["quick_hits"]["minItems"] = 5
editor.COUNTRY_SCHEMA["properties"]["quick_hits"]["maxItems"] = 5

# A story may synthesize several articles about one underlying event. Keep the
# original primary url/source fields for headline linking and add validated
# supporting links for Morning Brew-style multi-source sourcing.
_SOURCE_LINK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source": {"type": "string"},
        "url": {"type": "string"},
    },
    "required": ["source", "url"],
}
editor.STORY_SCHEMA["properties"]["source_links"] = {
    "type": "array",
    "items": _SOURCE_LINK_SCHEMA,
    "minItems": 1,
    "maxItems": 4,
}
editor.STORY_SCHEMA["properties"]["topic_key"] = {"type": "string"}
for required_field in ("source_links", "topic_key"):
    if required_field not in editor.STORY_SCHEMA["required"]:
        editor.STORY_SCHEMA["required"].append(required_field)

_RULES_EN = """

Additional Morning Brew-style sourcing, bullet, and story-selection rules:
- Treat the underlying NEWS EVENT OR POLICY DEBATE, not an article URL, as the unit of a story. Different consequences, agency responses, statistics, or outlet angles about one event belong in ONE synthesized story.
- Before selecting the lead and secondary stories, cluster candidates by underlying topic. Use only one lead/secondary slot per cluster. A slightly less prominent but genuinely different story is always better than a second story about the same topic.
- Set topic_key to a short canonical lowercase event/debate identifier, such as "anak krakatau eruption" or "sweden wealth tax debate". All coverage of the same underlying event or policy debate has the same topic_key.
- If multiple candidates contribute facts to one story, synthesize their strongest non-overlapping facts. Put every candidate actually used in source_links using its EXACT source and EXACT URL from the candidate metadata. url/source remain the primary source and must also appear in source_links.
- The newsletter template will render every source_links item under Read more. Do not list a source unless its candidate contributed a fact to the story.
- A lead/secondary topic may not appear again as another main story or as a Speed read. Speed reads must add genuinely different topics.
- Return exactly 5 Speed reads from 5 distinct topics. Reserve enough unused candidate topics for this section before filling optional secondary-story slots. Culture, music, arts, food, fashion, lifestyle, film, travel, and sports are good Speed-read material when they are timely and credible.
- Quick-hit labels describe the SUBJECT CATEGORY, never merely geography. Use labels such as SPORTS, SOCCER, POLITICS, MONEY, BUSINESS, TECH, CULTURE, MUSIC, FILM, FOOD, FASHION, LIFESTYLE, TRAVEL, TRANSPORT, SOCIETY, CRIME, WEATHER, or HEALTH. Use SOCCER for football fixtures/league news. Do not use STOCKHOLM, JAKARTA, MALMO, BANDUNG, or another place name merely because the story occurs there.
- For lead and secondary stories, the summary and bullets must be complementary. Never repeat, paraphrase, or shorten a fact from the summary into a bullet.
- Every bullet must be a complete grammatical sentence and a complete thought, with normal sentence punctuation. Never output fragments such as "8 airports closed", "Industry voice", "Exit threat", "Air quality under watch", or category-like notes.
- Use 0-3 bullets. A bullet exists only when the candidate metadata contains an important additional fact that is NOT already communicated in the summary. If there is no such fact, return an empty highlights array.
- Prefer a meaningful #/count, %, or $ figure in a bullet when an unused one is supported by the source metadata, but never force a number and never sacrifice sentence quality or novelty just to create a bullet.
- The summary itself should remain data-forward: include the strongest useful #/count, %, or $ figure when supported by the candidate metadata.
- Quick hits remain one concise self-contained sentence and do not display highlights.
"""

_RULES_ID = """

Aturan tambahan ala Morning Brew untuk sumber, bullet, dan pemilihan berita:
- Anggap PERISTIWA BERITA ATAU PERDEBATAN KEBIJAKAN, bukan URL artikel, sebagai satu unit berita. Dampak, respons instansi, statistik, atau sudut media yang berbeda dari satu peristiwa harus digabung menjadi SATU berita.
- Sebelum memilih berita utama dan tambahan, kelompokkan kandidat berdasarkan topik yang mendasarinya. Gunakan hanya satu slot utama/tambahan untuk setiap kelompok. Berita yang sedikit kurang besar tetapi benar-benar berbeda selalu lebih baik daripada berita kedua tentang topik yang sama.
- Isi topic_key dengan penanda peristiwa/debat singkat dalam huruf kecil, misalnya "erupsi anak krakatau" atau "debat pajak kekayaan swedia". Semua liputan tentang peristiwa atau debat yang sama harus memakai topic_key yang sama.
- Jika beberapa kandidat menyumbang fakta untuk satu berita, gabungkan fakta terkuat yang tidak tumpang tindih. Masukkan setiap kandidat yang benar-benar digunakan ke source_links dengan source dan URL PERSIS dari metadata kandidat. url/source utama juga wajib ada di source_links.
- Template akan menampilkan semua source_links di bagian Baca selengkapnya. Jangan cantumkan sumber yang tidak menyumbang fakta pada berita tersebut.
- Topik berita utama/tambahan tidak boleh muncul lagi sebagai berita utama lain atau Baca kilat. Baca kilat harus menambahkan topik yang benar-benar berbeda.
- Kembalikan tepat 5 Baca kilat dari 5 topik berbeda. Sisakan cukup kandidat yang belum dipakai sebelum mengisi slot berita tambahan opsional. Budaya, musik, seni, kuliner, mode, gaya hidup, film, wisata, dan olahraga cocok untuk Baca kilat jika berita aktual dan tepercaya.
- Label Baca kilat harus menjelaskan KATEGORI ISI, bukan lokasi. Gunakan OLAHRAGA, SEPAK BOLA, POLITIK, EKONOMI, BISNIS, TEKNOLOGI, BUDAYA, MUSIK, FILM, KULINER, MODE, GAYA HIDUP, WISATA, TRANSPORTASI, SOSIAL, KRIMINAL, CUACA, atau KESEHATAN. Jangan gunakan JAKARTA, BANDUNG, BALI, atau nama tempat hanya karena berita terjadi di sana.
- Untuk berita utama dan tambahan, ringkasan dan bullet harus saling melengkapi. Jangan mengulang, memparafrase, atau memendekkan fakta dari ringkasan menjadi bullet.
- Setiap bullet wajib berupa kalimat lengkap secara tata bahasa dan menyampaikan gagasan lengkap dengan tanda baca kalimat yang normal. Jangan pernah membuat fragmen seperti "8 bandara ditutup", "Ancaman keluar", atau "Kualitas udara dipantau".
- Gunakan 0-3 bullet. Bullet hanya boleh ada jika metadata kandidat memuat fakta tambahan penting yang BELUM disampaikan dalam ringkasan. Jika tidak ada fakta baru, kembalikan highlights sebagai array kosong.
- Utamakan angka #/jumlah, %, atau $ yang bermakna dalam bullet jika ada angka tepercaya yang belum digunakan, tetapi jangan memaksakan angka dan jangan mengorbankan kualitas kalimat atau kebaruan fakta.
- Ringkasan tetap data-forward dan harus memakai angka terkuat bila didukung metadata kandidat.
- Baca kilat tetap satu kalimat mandiri yang ringkas dan highlights tidak ditampilkan.
"""

editor.SYSTEM_PROMPT += _RULES_EN
editor.INDONESIA_SYSTEM_PROMPT += _RULES_ID

_WORD_RE = re.compile(r"[\w%$]+", re.UNICODE)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "into", "is", "it", "of", "on", "or", "that", "the", "their", "to", "was",
    "were", "will", "with", "yang", "dan", "di", "ke", "dari", "untuk", "pada", "ini",
    "itu", "dengan", "setelah", "akan", "telah", "sebagai", "atas", "oleh", "dalam",
    "news", "update", "report", "reports", "berita",
}


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _WORD_RE.findall(text.casefold())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _clean_highlights(story: Story) -> list[str]:
    """Keep only full-sentence bullets that add material beyond the summary."""
    summary_tokens = _tokens(story.summary)
    summary_normalized = " ".join(_WORD_RE.findall(story.summary.casefold()))
    cleaned: list[str] = []
    for raw in story.highlights:
        item = raw.strip()
        if not item or item[-1] not in ".!?":
            continue
        if len(_WORD_RE.findall(item)) < 4:
            continue
        normalized = " ".join(_WORD_RE.findall(item.casefold()))
        if normalized and normalized in summary_normalized:
            continue
        bullet_tokens = _tokens(item)
        if bullet_tokens and len(bullet_tokens - summary_tokens) < 2:
            continue
        cleaned.append(item)
        if len(cleaned) == 3:
            break
    return cleaned


def _topic_tokens(story: Story) -> set[str]:
    return _tokens(story.topic_key or story.headline)


def _same_topic(left: Story, right: Story) -> bool:
    left_key = " ".join(_WORD_RE.findall(left.topic_key.casefold()))
    right_key = " ".join(_WORD_RE.findall(right.topic_key.casefold()))
    if left_key and right_key and left_key == right_key:
        return True
    left_tokens = _topic_tokens(left)
    right_tokens = _topic_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    return len(overlap) >= 2 and len(overlap) / min(len(left_tokens), len(right_tokens)) >= 0.6


def _candidate_from_url(url: str, by_url: dict[str, object]):
    for key in (
        url,
        url.strip(),
        editor.canonical_url_key(url),
        editor.canonical_url_key(url).split("?")[0],
    ):
        if key in by_url:
            return by_url[key]
    return None


def _candidate_same_topic(candidate: Candidate, story: Story) -> bool:
    candidate_keys = {
        candidate.url,
        editor.canonical_url_key(candidate.url),
        editor.canonical_url_key(candidate.url).split("?")[0],
    }
    story_urls = {story.url, *(link.url for link in story.source_links)}
    story_keys = {
        key
        for url in story_urls
        for key in (
            url,
            editor.canonical_url_key(url),
            editor.canonical_url_key(url).split("?")[0],
        )
    }
    if candidate_keys & story_keys:
        return True

    candidate_tokens = _tokens(candidate.title)
    story_tokens = _tokens(f"{story.topic_key} {story.headline}")
    if not candidate_tokens or not story_tokens:
        return False
    overlap = candidate_tokens & story_tokens
    return len(overlap) >= 2 and len(overlap) / min(
        len(candidate_tokens), len(story_tokens)
    ) >= 0.5


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
    title_words = _tokens(candidate.title)
    summary_words = _tokens(text)
    if len(summary_words) < 7 or summary_words == title_words:
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
        why_it_matters="",
        url=candidate.url,
        source=candidate.source,
        label=_quick_label(candidate, localized),
        highlights=[],
        source_links=[SourceLink(source=candidate.source, url=candidate.url)],
        topic_key=editor.normalized_title(candidate.title),
    )


def _repair_and_clean(
    edition: Edition | IndonesiaEdition, candidates: list[Candidate]
) -> Edition | IndonesiaEdition:
    """Validate every source link and enforce no-repeat editorial rules after generation."""
    by_url, by_title = editor.build_link_index(candidates)
    dropped = 0

    def fix(story: Story) -> Story | None:
        nonlocal dropped
        primary = editor.match_candidate(story, by_url, by_title)
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

        return replace(
            story,
            url=primary.url,
            source=primary.source or story.source,
            source_links=links,
            highlights=_clean_highlights(story),
        )

    def fix_list(stories: list[Story]) -> list[Story]:
        return [fixed for fixed in (fix(story) for story in stories) if fixed is not None]

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
        localized: bool,
    ) -> CountrySection:
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
        stories = dedupe(stories, [lead])
        quick_hits = dedupe(quick_hits, [lead, *stories])
        quick_hits = backfill_quick_hits(country, lead, stories, quick_hits, localized)
        return replace(section, lead=lead, stories=stories, quick_hits=quick_hits)

    indonesia_only = isinstance(edition, IndonesiaEdition)
    indonesia = fix_section(edition.indonesia, "Indonesia", indonesia_only)
    changes = {"indonesia": indonesia}
    section_stories = [indonesia.lead, *indonesia.stories, *indonesia.quick_hits]
    if isinstance(edition, Edition):
        sweden = fix_section(edition.sweden, "Sweden", False)
        changes["sweden"] = sweden
        section_stories = [
            sweden.lead,
            *sweden.stories,
            *sweden.quick_hits,
            *section_stories,
        ]

    front_page = fix_list(edition.front_page)
    if not front_page:
        # Front page is compatibility-only and is not rendered. It must never
        # block an otherwise valid newsletter delivery.
        front_page = section_stories[:3]
    changes["front_page"] = front_page

    if dropped:
        print(f"  Dropped {dropped} story link(s) the editor invented", flush=True)
    return replace(edition, **changes)


# create_edition/create_indonesia_edition resolve this module global at runtime,
# so production generation receives the stricter validated cleanup.
editor.validate_links = _repair_and_clean
