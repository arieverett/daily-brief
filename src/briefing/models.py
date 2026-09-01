from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Candidate:
    country: str
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    summary: str = ""

    def prompt_dict(self) -> dict[str, str]:
        return {
            "country": self.country,
            "title": self.title,
            "source": self.source,
            "published_at": self.published_at.isoformat() if self.published_at else "unknown",
            "summary": self.summary[:900],
            "url": self.url,
        }


@dataclass(frozen=True)
class Story:
    headline: str
    summary: str
    why_it_matters: str
    url: str
    source: str
    label: str = ""


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
    front_page: list[Story]
    sweden: CountrySection
    indonesia: CountrySection
    bottom_line: str

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


def story_from_dict(value: dict[str, Any]) -> Story:
    return Story(**value)


def country_from_dict(value: dict[str, Any]) -> CountrySection:
    return CountrySection(
        lead=story_from_dict(value["lead"]),
        stories=[story_from_dict(item) for item in value.get("stories", [])],
        quick_hits=[story_from_dict(item) for item in value.get("quick_hits", [])],
    )


def edition_from_dict(value: dict[str, Any]) -> Edition:
    return Edition(
        edition_date=value["edition_date"],
        date_label=value["date_label"],
        subject=value["subject"],
        preview_text=value["preview_text"],
        front_page=[story_from_dict(item) for item in value["front_page"]],
        sweden=country_from_dict(value["sweden"]),
        indonesia=country_from_dict(value["indonesia"]),
        bottom_line=value["bottom_line"],
    )
