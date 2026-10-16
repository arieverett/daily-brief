from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest

from briefing import weather
from briefing.main import _add_weather, load_sample
from briefing.render import render_html, render_text
from briefing.weather import (
    WeatherConfig,
    build_weather,
    lead_day,
    load_weather_config,
    unsupported_numbers,
    write_weather_summary,
)

ROOT = Path(__file__).resolve().parents[1]
NEW_YORK = ZoneInfo("America/New_York")
# 06:00 in New York is noon in Stockholm and 17:00 in Jakarta.
READER_TIME = datetime(2026, 10, 16, 6, 0, tzinfo=NEW_YORK)
DAY = READER_TIME.date()
TIMEZONES = {"59.3293": "Europe/Stockholm", "-6.2088": "Asia/Jakarta", "57.7089": "Europe/Stockholm"}

CONFIG = WeatherConfig(
    language="en",
    unit="F",
    featured=[
        weather.Place("Stockholm", "Sweden", 59.3293, 18.0686, "🇸🇪"),
        weather.Place("Jakarta", "Indonesia", -6.2088, 106.8456, "🇮🇩"),
    ],
    around=[weather.Place("Gothenburg", "Sweden", 57.7089, 11.9746, "🇸🇪")],
)


def _forecast_item(latitude: str, past_days: int, forecast_days: int) -> dict:
    timezone = TIMEZONES[latitude]
    today = READER_TIME.astimezone(ZoneInfo(timezone)).date()
    days = [today + timedelta(days=offset) for offset in range(-past_days, forecast_days)]
    count = len(days)
    return {
        "timezone": timezone,
        "daily": {
            "time": [day.isoformat() for day in days],
            "weather_code": [61] * count,
            "temperature_2m_max": [15.0] * count,
            "temperature_2m_min": [5.0] * count,
            "precipitation_probability_max": [40] * count,
            "precipitation_sum": [2.0] * count,
            "daylight_duration": [36000.0 - 300 * index for index in range(count)],
        },
    }


def _archive_item(latitude: str) -> dict:
    start, end = date(1991, 1, 1), date(2025, 12, 31)
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    # Highs are 10°C, except a warm mid-October 2023 (outside the 1991-2020 normals) at 20°C.
    highs = [
        20.0 if day.year == 2023 and day.month == 10 and 10 <= day.day <= 31 else 10.0
        for day in days
    ]
    return {
        "timezone": TIMEZONES[latitude],
        "daily": {
            "time": [day.isoformat() for day in days],
            "temperature_2m_max": highs,
            "temperature_2m_min": [2.0] * len(days),
            "precipitation_sum": [1.0] * len(days),
        },
    }


def _recent_item(latitude: str, start: str, end: str, *, days_available: int) -> dict:
    """Recent reanalysis: 2°C cooler highs and lows than the forecast model, wetter than normal."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    have = [index < days_available for index in range(len(days))]
    return {
        "timezone": TIMEZONES[latitude],
        "daily": {
            "time": [day.isoformat() for day in days],
            "temperature_2m_max": [13.0 if ok else None for ok in have],
            "temperature_2m_min": [3.0 if ok else None for ok in have],
            "precipitation_sum": [2.0 if ok else None for ok in have],
        },
    }


class FakeOpenMeteo:
    def __init__(
        self,
        *,
        archive_status: int = 200,
        forecast_status: int = 200,
        recent_days_available: int = 31,
    ):
        self.archive_status = archive_status
        self.forecast_status = forecast_status
        self.recent_days_available = recent_days_available
        self.history_calls = 0
        self.recent_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        latitudes = query["latitude"][0].split(",")
        if request.url.host.startswith("archive"):
            if self.archive_status != 200:
                return httpx.Response(self.archive_status, request=request)
            start, end = query["start_date"][0], query["end_date"][0]
            if start == "1991-01-01":
                self.history_calls += 1
                return httpx.Response(200, json=_archive_item(latitudes[0]), request=request)
            self.recent_calls += 1
            item = _recent_item(
                latitudes[0], start, end, days_available=self.recent_days_available
            )
            return httpx.Response(200, json=item, request=request)
        if self.forecast_status != 200:
            return httpx.Response(self.forecast_status, request=request)
        past, ahead = int(query["past_days"][0]), int(query["forecast_days"][0])
        items = [_forecast_item(latitude, past, ahead) for latitude in latitudes]
        return httpx.Response(200, json=items if len(items) > 1 else items[0], request=request)


@pytest.fixture(autouse=True)
def weather_cache(tmp_path, monkeypatch):
    monkeypatch.setenv(weather.CACHE_ENV, str(tmp_path / "weather-cache"))
    return tmp_path / "weather-cache"


def _client(fake: FakeOpenMeteo) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake))


def test_forecast_leads_with_tomorrow_once_a_city_is_into_its_evening():
    assert lead_day(datetime(2026, 10, 16, 12, 0, tzinfo=NEW_YORK)) == date(2026, 10, 16)
    assert lead_day(datetime(2026, 10, 16, 14, 59, tzinfo=NEW_YORK)) == date(2026, 10, 16)
    assert lead_day(datetime(2026, 10, 16, 17, 0, tzinfo=NEW_YORK)) == date(2026, 10, 17)


def test_build_weather_makes_widgets_rows_and_climate_facts(weather_cache):
    fake = FakeOpenMeteo()
    data = build_weather(CONFIG, READER_TIME, client=_client(fake))
    assert data is not None
    stockholm, jakarta = data.report.featured
    assert (stockholm.day_label, jakarta.day_label) == ("Today", "Tomorrow")
    assert [day.label for day in stockholm.days][:2] == ["Today", "Sat"]
    assert jakarta.days[0].label == "Tmrw" and jakarta.days[0].date == "2026-10-17"
    assert len(stockholm.days) == weather.STRIP_DAYS
    assert (stockholm.high, stockholm.low) == (59, 41)  # 15°C / 5°C in °F
    assert stockholm.theme == "rain" and stockholm.condition == "Light rain"

    # One around row, for "today", so no tag and no note.
    assert [city.name for city in data.report.around] == ["Gothenburg"]
    assert data.report.around_note == "" and data.report.around[0].day_tag == ""

    stockholm_facts = data.facts["featured_cities"][0]
    climate = stockholm_facts["climate"]
    # The forecast model runs 2°C warmer than the reanalysis on recent days, so the
    # 10°C normal is read as 12°C: the 15°C forecast is 3°C above normal, not 5°C.
    assert climate["normal_high"] == 54  # 12°C
    assert climate["headline_high_vs_normal"] == 5.4  # 3°C, in °F
    week = climate["week_ahead"]
    assert week["years_compared"] == 35 and week["first_year_compared"] == 1991
    assert week["warmth_rank"] == 2 and week["most_recent_warmer_year"] == 2023
    assert week["vs_normal"] == 5.4
    month = climate["latest_30_days"]
    assert month["temp_vs_normal"] == 3.6  # reanalysis 8°C mean vs 6°C normal
    assert month["rain_pct_of_normal"] == 200
    assert stockholm_facts["daylight"]["change_vs_week_ago_minutes"] == -35  # 5 min/day shorter
    assert data.facts["featured_cities"][1]["headline_day"] == "tomorrow"
    assert any("highs +2.0°C" in note for note in data.notes)

    # Climate history is cached: a second run only asks for the recent days again.
    assert (fake.history_calls, fake.recent_calls) == (2, 2)
    assert len(list(weather_cache.glob("*.json"))) == 2
    build_weather(CONFIG, READER_TIME, client=_client(fake))
    assert (fake.history_calls, fake.recent_calls) == (2, 4)


def test_no_comparisons_without_enough_days_to_calibrate():
    data = build_weather(
        CONFIG, READER_TIME, client=_client(FakeOpenMeteo(recent_days_available=5))
    )
    assert data is not None
    assert all("climate" not in fact for fact in data.facts["featured_cities"])
    assert all("daylight" in fact for fact in data.facts["featured_cities"])


def test_missing_climate_history_still_gives_forecasts():
    data = build_weather(CONFIG, READER_TIME, client=_client(FakeOpenMeteo(archive_status=503)))
    assert data is not None
    assert len(data.report.featured) == 2
    assert all("climate" not in fact for fact in data.facts["featured_cities"])


def test_forecast_outage_skips_the_section_without_failing_the_edition(monkeypatch, tmp_path):
    def outage(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("briefing.main.build_weather", outage)
    edition = load_sample()
    edition = replace(edition, weather=None)
    args = SimpleNamespace(sources=ROOT / "config" / "sources.yml")
    settings = SimpleNamespace(
        send_at="", delivery_nonce="", timezone="America/New_York",
        openai_api_key="x", openai_model="gpt-5-mini",
    )
    assert _add_weather(edition, args, settings, date(2026, 10, 16)) is edition


def test_unsupported_numbers_catches_invented_figures():
    facts = {"high": 59, "vs_normal": 1.2, "past_30_days_rain": 0.5, "years": [1991, 2018]}
    assert unsupported_numbers("A 59°F day, 1.2° above normal, warmest since 2018.", facts) == set()
    assert unsupported_numbers("Hangat 1,2°C, hujan 0.50 in dalam 30 hari.", facts) == set()
    assert unsupported_numbers("Expect 64°F and a record 3 inches.", facts) == {"64", "3"}


def test_write_up_is_regenerated_once_then_dropped(monkeypatch):
    data = weather.WeatherData(report=None, facts={"high": 59})  # type: ignore[arg-type]
    replies = iter([{"summary": "It hits 70°F."}, {"summary": "**Today:** 59°F — pleasant."}])
    monkeypatch.setattr(
        "briefing.editor._generate_structured_output", lambda **kwargs: next(replies)
    )
    summary = write_weather_summary(data, api_key="x", model="m", language="en", day=DAY)
    assert summary.startswith("**Today:** 59°F") and "—" not in summary

    monkeypatch.setattr(
        "briefing.editor._generate_structured_output", lambda **kwargs: {"summary": "71°F!"}
    )
    assert write_weather_summary(data, api_key="x", model="m", language="en", day=DAY) == ""


def test_edition_configs_have_the_agreed_cities_and_units():
    standard = load_weather_config(ROOT / "config" / "sources.yml")
    assert standard is not None
    assert (standard.language, standard.unit) == ("en", "F")
    assert [place.name for place in standard.featured] == ["Stockholm", "Jakarta"]

    mom = load_weather_config(ROOT / "config" / "indonesia_sources.yml")
    assert mom is not None
    assert (mom.language, mom.unit) == ("id", "C")
    assert [place.name for place in mom.featured] == ["Bandung"]
    assert len(mom.around) >= 3


def test_weather_section_renders_after_the_news_and_only_when_present():
    edition = load_sample()
    html = render_html(edition)
    body = html.split("<body>")[1]
    assert body.index("🇮🇩 Indonesia</span>") < body.index("🌤 Weather") < body.index("Coded by Ari")
    assert body.count('class="wx-card"') == 2
    assert body.count('class="wx-day"') == 10
    assert "Open-Meteo.com" in body and "Around the map" in body
    assert "TMRW".lower() in body.lower()

    text = render_text(edition)
    assert "WEATHER" in text and "Stockholm, today: Partly cloudy, 61°" in text

    bare = render_html(replace(edition, weather=None)).split("<body>")[1]
    assert "Weather" not in bare and "wx-card" not in bare


def test_footer_has_no_divider_rule():
    html = render_html(load_sample())
    footer_css = next(line for line in html.splitlines() if line.strip().startswith(".footer {"))
    assert "border" not in footer_css
