from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Candidate:
    """A trusted article candidate collected from a configured feed."""

    country: str
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    summary: str = ""
    image_url: str = ""

    def prompt_dict(self) -> dict[str, str]:
        """Return only the fields the AI editor needs."""
        return {
            "country": self.country,
            "title": self.title,
            "source": self.source,
            "published_at": self.published_at.isoformat() if self.published_at else "unknown",
            "summary": self.summary[:900],
            "url": self.url,
        }


@dataclass(frozen=True)
class SourceLink:
    source: str
    url: str


@dataclass(frozen=True)
class Story:
    """A rendered main story or speed-read item."""

    headline: str
    summary: str
    url: str
    source: str
    label: str = ""
    image_url: str = ""
    highlights: list[str] = field(default_factory=list)
    source_links: list[SourceLink] = field(default_factory=list)
    topic_key: str = ""


@dataclass(frozen=True)
class CountrySection:
    lead: Story
    stories: list[Story] = field(default_factory=list)
    quick_hits: list[Story] = field(default_factory=list)


@dataclass(frozen=True)
class Edition:
    edition_date: str
    date_label: str
    subject: str
    preview_text: str
    sweden: CountrySection
    indonesia: CountrySection
    setup: str

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IndonesiaEdition:
    edition_date: str
    date_label: str
    subject: str
    preview_text: str
    indonesia: CountrySection
    setup: str

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


NewsletterEdition = Edition | IndonesiaEdition


def source_link_from_dict(value: dict[str, Any]) -> SourceLink:
    return SourceLink(source=value["source"], url=value["url"])


def story_from_dict(value: dict[str, Any]) -> Story:
    """Parse current output while tolerating obsolete V1 compatibility fields."""
    data = dict(value)
    data.pop("why_it_matters", None)
    data["source_links"] = [
        source_link_from_dict(item) for item in value.get("source_links", [])
    ]
    return Story(**data)


def country_from_dict(value: dict[str, Any]) -> CountrySection:
    return CountrySection(
        lead=story_from_dict(value["lead"]),
        stories=[story_from_dict(item) for item in value.get("stories", [])],
        quick_hits=[story_from_dict(item) for item in value.get("quick_hits", [])],
    )


def _setup_from_dict(value: dict[str, Any]) -> str:
    # ``bottom_line`` is accepted only so old sample fixtures remain readable.
    return str(value.get("setup", value.get("bottom_line", "")))


def edition_from_dict(value: dict[str, Any]) -> Edition:
    return Edition(
        edition_date=value["edition_date"],
        date_label=value["date_label"],
        subject=value["subject"],
        preview_text=value["preview_text"],
        sweden=country_from_dict(value["sweden"]),
        indonesia=country_from_dict(value["indonesia"]),
        setup=_setup_from_dict(value),
    )


def indonesia_edition_from_dict(value: dict[str, Any]) -> IndonesiaEdition:
    return IndonesiaEdition(
        edition_date=value["edition_date"],
        date_label=value["date_label"],
        subject=value["subject"],
        preview_text=value["preview_text"],
        indonesia=country_from_dict(value["indonesia"]),
        setup=_setup_from_dict(value),
    )
