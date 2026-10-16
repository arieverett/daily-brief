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
    # Opening paragraphs of the article itself, when the publisher page could be read.
    excerpt: str = ""

    def prompt_dict(self) -> dict[str, str]:
        """Return only the fields the AI editor needs."""
        fields = {
            "country": self.country,
            "title": self.title,
            "source": self.source,
            "published_at": self.published_at.isoformat() if self.published_at else "unknown",
            "summary": self.summary[:900],
            "url": self.url,
        }
        if self.excerpt:
            fields["article_excerpt"] = self.excerpt[:1500]
        return fields


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
class ForecastDay:
    """One column of a forecast widget."""

    date: str
    label: str
    icon: str
    high: int
    low: int
    rain_chance: int | None = None


@dataclass(frozen=True)
class CityForecast:
    """A featured city's widget (with `days`) or one row of the other-cities table."""

    name: str
    country: str
    day_label: str
    condition: str
    icon: str
    theme: str
    high: int
    low: int
    flag: str = ""
    rain_chance: int | None = None
    days: list[ForecastDay] = field(default_factory=list)
    # Short "tomorrow" marker for a table row whose day differs from the others.
    day_tag: str = ""


@dataclass(frozen=True)
class WeatherReport:
    unit: str  # "F" or "C"
    summary: str
    featured: list[CityForecast] = field(default_factory=list)
    around: list[CityForecast] = field(default_factory=list)
    # Said once in the table title when every row is for the same (non-today) day.
    around_note: str = ""


@dataclass(frozen=True)
class Edition:
    edition_date: str
    date_label: str
    subject: str
    preview_text: str
    sweden: CountrySection
    indonesia: CountrySection
    setup: str
    weather: WeatherReport | None = None

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
    weather: WeatherReport | None = None

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


NewsletterEdition = Edition | IndonesiaEdition


def source_link_from_dict(value: dict[str, Any]) -> SourceLink:
    return SourceLink(source=value["source"], url=value["url"])


def story_from_dict(value: dict[str, Any]) -> Story:
    data = dict(value)
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


def city_forecast_from_dict(value: dict[str, Any]) -> CityForecast:
    data = dict(value)
    data["days"] = [ForecastDay(**item) for item in value.get("days", [])]
    return CityForecast(**data)


def weather_from_dict(value: dict[str, Any] | None) -> WeatherReport | None:
    if not value:
        return None
    return WeatherReport(
        unit=value["unit"],
        summary=value.get("summary", ""),
        featured=[city_forecast_from_dict(item) for item in value.get("featured", [])],
        around=[city_forecast_from_dict(item) for item in value.get("around", [])],
        around_note=value.get("around_note", ""),
    )


def edition_from_dict(value: dict[str, Any]) -> Edition:
    return Edition(
        edition_date=value["edition_date"],
        date_label=value["date_label"],
        subject=value["subject"],
        preview_text=value["preview_text"],
        sweden=country_from_dict(value["sweden"]),
        indonesia=country_from_dict(value["indonesia"]),
        setup=value["setup"],
        weather=weather_from_dict(value.get("weather")),
    )


def indonesia_edition_from_dict(value: dict[str, Any]) -> IndonesiaEdition:
    return IndonesiaEdition(
        edition_date=value["edition_date"],
        date_label=value["date_label"],
        subject=value["subject"],
        preview_text=value["preview_text"],
        indonesia=country_from_dict(value["indonesia"]),
        setup=value["setup"],
        weather=weather_from_dict(value.get("weather")),
    )
