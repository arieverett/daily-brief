"""Stricter anti-redundancy rules for Indonesia story bullets."""

from __future__ import annotations

import re
from dataclasses import replace

from . import editor, editorial_rules

_RULE_EN = """

Indonesia-section highlight rule:
- Be conservative with bullets in the Indonesia section. Zero bullets is better than repeating the summary.
- Every Indonesia bullet must introduce a materially new fact that the summary does not already state, imply, paraphrase, or summarize.
- Prefer unused quantitative details: a new #/count, %, Rp/IDR amount, $ amount, or other currency figure that does not already appear in the summary.
- Do not turn the summary's ranking, cause, government response, assistance offer, athlete count, bonus amount, or other already-covered fact into a bullet using different wording.
- If the source material does not contain an important unused fact, return an empty highlights array for that story.
"""

_RULE_ID = """

Aturan khusus bullet Indonesia:
- Gunakan bullet secara konservatif. Nol bullet lebih baik daripada mengulang ringkasan.
- Setiap bullet harus menambahkan fakta yang benar-benar baru dan belum dinyatakan, disiratkan, diparafrasekan, atau diringkas dalam paragraf.
- Utamakan data kuantitatif yang belum dipakai: #/jumlah baru, %, nilai Rp/IDR, $, atau angka mata uang lain yang belum muncul dalam ringkasan.
- Jangan mengubah peringkat, penyebab, respons pemerintah, tawaran bantuan, jumlah atlet, nilai bonus, atau fakta lain yang sudah ada di ringkasan menjadi bullet dengan susunan kata berbeda.
- Jika sumber tidak memiliki fakta penting tambahan yang belum dipakai, kembalikan highlights sebagai array kosong.
"""

editor.SYSTEM_PROMPT += _RULE_EN
editor.INDONESIA_SYSTEM_PROMPT += _RULE_ID

_DATA_RE = re.compile(
    r"(?:[$€£¥₹]\s?\d[\d.,]*)"
    r"|(?:\b(?:rp|idr|usd|sek|eur)\s?\d[\d.,]*)"
    r"|(?:\b\d[\d.,]*\s?(?:%|percent|persen|million|billion|trillion|juta|miliar|triliun)?\b)",
    re.IGNORECASE,
)


def _data_markers(text: str) -> set[str]:
    """Return normalized numeric/currency facts used by a sentence."""
    return {
        re.sub(r"\s+", "", match.group(0).casefold()).rstrip(".,")
        for match in _DATA_RE.finditer(text)
    }


def _strict_clean(story):
    """Keep only Indonesia bullets that add genuinely new information."""
    summary_tokens = editorial_rules._tokens(story.summary)
    summary_data = _data_markers(story.summary)
    kept: list[tuple[str, bool]] = []

    for item in editorial_rules._clean_highlights(story):
        bullet_tokens = editorial_rules._tokens(item)
        if not bullet_tokens:
            continue

        bullet_data = _data_markers(item)
        new_data = bullet_data - summary_data
        overlap = len(bullet_tokens & summary_tokens)
        new_words = bullet_tokens - summary_tokens
        denominator = min(len(bullet_tokens), len(summary_tokens))
        overlap_ratio = overlap / denominator if denominator else 0.0

        # Reusing a figure from the paragraph is usually a recap, not a highlight.
        if bullet_data and not new_data:
            continue

        # Non-numeric bullets must be very clearly separate from the paragraph.
        # Empty highlights are explicitly preferable to paraphrased filler.
        if not bullet_data and (
            overlap >= 2 or overlap_ratio >= 0.25 or len(new_words) < 4
        ):
            continue

        kept.append((item, bool(new_data)))

    # Morning Brew-style data bullets get priority, while preserving source order
    # within the quantitative and non-quantitative groups.
    kept.sort(key=lambda pair: pair[1], reverse=True)
    return [item for item, _ in kept[:3]]


def _strict_section(section):
    lead = replace(section.lead, highlights=_strict_clean(section.lead))
    stories = [replace(story, highlights=_strict_clean(story)) for story in section.stories]
    return replace(section, lead=lead, stories=stories)


_base_validate_links = editor.validate_links


def _validate_links_with_indonesia_gate(edition, candidates):
    cleaned = _base_validate_links(edition, candidates)
    return replace(cleaned, indonesia=_strict_section(cleaned.indonesia))


# editor.create_edition/create_indonesia_edition resolve this module global at runtime.
editor.validate_links = _validate_links_with_indonesia_gate
