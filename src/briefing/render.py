"""HTML and plain-text rendering."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files

from jinja2 import Environment, FileSystemLoader, Template, select_autoescape
from markupsafe import Markup, escape

from .models import Edition, IndonesiaEdition, NewsletterEdition, Story, WeatherReport

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


def _weather_text(weather: WeatherReport | None, *, indonesian: bool) -> list[str]:
    if not weather or not weather.featured:
        return []
    unit, alt = f"°{weather.unit}", f"°{weather.alt_unit}"
    low = "min" if indonesian else "low"
    blocks = ["", "CUACA" if indonesian else "WEATHER"]
    for city in weather.featured:
        days = "  ".join(f"{day.label} {day.high}°/{day.low}°" for day in city.days)
        blocks.append(
            f"{city.name}, {city.day_label.lower()}: {city.condition}, {city.high}{unit} "
            f"({city.high_alt}{alt}), {low} {city.low}{unit} ({city.low_alt}{alt})\n{days}"
        )
    if weather.around:
        blocks.append(
            "\n".join(
                f"{city.name}{' (' + city.day_tag.lower() + ')' if city.day_tag else ''}: "
                f"{city.condition}, {city.high}°/{city.low}°{unit[-1]} "
                f"({city.high_alt}°/{city.low_alt}°{alt[-1]})"
                for city in weather.around
            )
        )
    if weather.summary:
        blocks.append(plain(weather.summary))
    blocks.append(
        "Data cuaca: Open-Meteo.com" if indonesian else "Weather data: Open-Meteo.com"
    )
    return blocks


def render_text(edition: Edition) -> str:
    blocks = ["DAILY BRIEF", edition.date_label, "", "THE SETUP", plain(edition.setup)]
    blocks.extend(_weather_text(edition.weather, indonesian=False))
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
        *_weather_text(edition.weather, indonesian=True),
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
