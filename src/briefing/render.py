"""HTML and plain-text rendering."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from jinja2 import Environment, FileSystemLoader, Template, select_autoescape

from .models import Edition, IndonesiaEdition, NewsletterEdition, Story


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
    return environment.get_template("newsletter.html")


def render_html(edition: NewsletterEdition) -> str:
    return _newsletter_template().render(edition=edition)


def _source_lines(story: Story, prefix: str) -> list[str]:
    links = story.source_links or []
    if links:
        return [f"{prefix}: {link.source} | {link.url}" for link in links]
    return [f"{prefix}: {story.source} | {story.url}"]


def _story_text(story: Story, *, source_prefix: str) -> str:
    lines = [f"{story.label}: {story.headline}", story.summary]
    lines.extend(f"• {item}" for item in story.highlights)
    lines.extend(_source_lines(story, source_prefix))
    return "\n".join(lines)


def render_text(edition: Edition) -> str:
    blocks = ["DAILY BRIEF", edition.date_label, "", "THE SETUP", edition.setup]
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
        edition.setup,
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
