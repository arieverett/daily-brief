"""HTML and plain-text rendering."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files

from jinja2 import Environment, FileSystemLoader, Template, select_autoescape
from markupsafe import Markup, escape

from .models import Edition, IndonesiaEdition, NewsletterEdition, Story

# The editor may bold a short run-in at the start of a paragraph (**Zoom out:**) and separate
# paragraphs with a blank line. Nothing else is interpreted; everything is HTML-escaped first.
BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")


def paragraphs(text: str) -> list[str]:
    """Split on blank lines and collapse stray whitespace inside each paragraph."""
    return [" ".join(part.split()) for part in PARAGRAPH_BREAK_RE.split(text or "") if part.strip()]


def inline_markup(text: str) -> Markup:
    """Escape text, then turn **run-ins** into <strong>; drop any unpaired markers."""
    html = BOLD_RE.sub(lambda match: f"<strong>{match.group(1)}</strong>", str(escape(text)))
    return Markup(html.replace("**", ""))


def rich_paragraphs(text: str, css_class: str, tight_last: bool = False) -> Markup:
    parts = paragraphs(text)
    rendered = []
    for index, part in enumerate(parts):
        last = ' style="margin-bottom:0"' if tight_last and index == len(parts) - 1 else ""
        rendered.append(f'<p class="{css_class}"{last}>{inline_markup(part)}</p>')
    return Markup("".join(rendered))


def rich_inline(text: str) -> Markup:
    return Markup(" ".join(inline_markup(part) for part in paragraphs(text)))


def plain(text: str) -> str:
    """Plain-text version: keep paragraph breaks, drop bold markers."""
    return "\n\n".join(part.replace("**", "") for part in paragraphs(text))


@lru_cache(maxsize=1)
def _newsletter_template() -> Template:
    """Build the Jinja environment once per process instead of once per render."""
    template_dir = files("briefing").joinpath("templates")
    environment = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters.update(
        rich_paragraphs=rich_paragraphs,
        rich_inline=rich_inline,
        plain=plain,
    )
    return environment.get_template("newsletter.html")


def render_html(edition: NewsletterEdition) -> str:
    return _newsletter_template().render(edition=edition)


def _source_lines(story: Story, prefix: str) -> list[str]:
    links = story.source_links or []
    if links:
        return [f"{prefix}: {link.source} | {link.url}" for link in links]
    return [f"{prefix}: {story.source} | {story.url}"]


def _story_text(story: Story, *, source_prefix: str) -> str:
    lines = [f"{plain(story.label)}: {plain(story.headline)}", plain(story.summary)]
    lines.extend(f"• {plain(item)}" for item in story.highlights)
    lines.extend(_source_lines(story, source_prefix))
    return "\n".join(lines)


def render_text(edition: Edition) -> str:
    blocks = ["DAILY BRIEF", edition.date_label, "", "THE SETUP", plain(edition.setup)]
    for heading, section in (("SWEDEN", edition.sweden), ("INDONESIA", edition.indonesia)):
        blocks.extend(("", heading, _story_text(section.lead, source_prefix="Read more")))
        blocks.extend(
            _story_text(story, source_prefix="Read more") for story in section.stories
        )
        blocks.append("SPEED READ")
        blocks.extend(
            _story_text(story, source_prefix="Read article") for story in section.quick_hits
        )
    return "\n\n".join(blocks)


def render_indonesia_text(edition: IndonesiaEdition) -> str:
    section = edition.indonesia
    blocks = [
        "NUSANTARA DAILY",
        edition.date_label,
        "",
        "DALAM EDISI HARI INI",
        plain(edition.setup),
        "",
        "INDONESIA",
        _story_text(section.lead, source_prefix="Baca selengkapnya"),
        *(
            _story_text(story, source_prefix="Baca selengkapnya")
            for story in section.stories
        ),
        "BACA KILAT",
        *(
            _story_text(story, source_prefix="Baca artikel")
            for story in section.quick_hits
        ),
    ]
    return "\n\n".join(blocks)
