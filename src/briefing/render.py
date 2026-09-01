from __future__ import annotations

from importlib.resources import files

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import Edition, Story


def _template_env() -> Environment:
    template_dir = files("briefing").joinpath("templates")
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html(edition: Edition) -> str:
    return _template_env().get_template("newsletter.html").render(edition=edition)


def _story_text(story: Story, *, include_why: bool = True) -> str:
    lines = [f"{story.label}: {story.headline}", story.summary]
    if include_why:
        lines.append(f"Why it matters: {story.why_it_matters}")
    lines.append(f"{story.source}: {story.url}")
    return "\n".join(lines)


def render_text(edition: Edition) -> str:
    blocks = [
        "DAILY BRIEF",
        edition.date_label,
        "",
        "THE SETUP",
        edition.bottom_line,
        "",
        "THE FRONT PAGE",
        *(_story_text(story, include_why=False) for story in edition.front_page),
    ]
    for heading, section in (("SWEDEN", edition.sweden), ("INDONESIA", edition.indonesia)):
        blocks += ["", heading, _story_text(section.lead)]
        blocks.extend(_story_text(story) for story in section.stories)
        blocks.append("QUICK HITS")
        blocks.extend(_story_text(story, include_why=False) for story in section.quick_hits)
    return "\n\n".join(blocks)
