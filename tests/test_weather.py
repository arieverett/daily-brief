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
    # Topics that need no extra request; the network ones have their own tests below.
    topics=["climate_context", "rain_check", "on_this_day", "around_the_world", "daylight", "uv"],
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
            "uv_index_max": [3.0] * count,
            "sunrise": [f"{day.isoformat()}T07:30" for day in days],
            "sunset": [f"{day.isoformat()}T17:45" for day in days],
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
def no_retry_waits(monkeypatch):
    monkeypatch.setattr(weather.time, "sleep", lambda seconds: None)


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

    assert (stockholm.high_alt, stockholm.low_alt) == (15, 5)  # the other unit
    assert stockholm.days[0].high_alt == 15

    # The forecast model runs 2°C warmer than the reanalysis on recent days, so the
    # 10°C normal is read as 12°C: the 15°C forecast is 3°C above normal, not 5°C.
    context = data.topics["climate_context"].facts["cities"][0]
    assert context["normal_high"] == {"°F": 54, "°C": 12}
    assert context["high_vs_normal"] == {"°F": 5.4, "°C": 3.0}
    week = context["week_ahead"]
    assert week["years_compared"] == 35 and week["first_year_compared"] == 1991
    assert week["warmth_rank"] == 2 and week["most_recent_warmer_year"] == 2023
    assert week["vs_normal"] == {"°F": 5.4, "°C": 3.0}
    month = data.topics["rain_check"].facts["cities"][0]
    assert month["temperature_vs_normal"] == {"°F": 3.6, "°C": 2.0}  # 8°C vs 6°C normal
    assert month["rain_pct_of_normal"] == 200
    history = data.topics["on_this_day"].facts["cities"][0]
    assert history["warmest_high"]["year"] == 2023 and history["years_on_record"] == 35
    light = data.topics["daylight"].facts["cities"][0]
    assert light["change_vs_week_ago_minutes"] == -35 and light["sunset"] == "17:45"
    assert any("highs +2.0°C" in note for note in data.notes)

    # The editor gets the day ahead plus exactly one special topic.
    assert [city["day"] for city in data.facts["day_ahead"]] == ["today", "tomorrow"]
    assert data.facts["day_ahead"][0]["high"] == {"°F": 59, "°C": 15}
    assert data.facts["temperature_style"] == "°F only, e.g. 55°F"
    assert data.facts["special_topic"]["topic"] == data.report.topic in data.topics

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
    assert not {"climate_context", "rain_check", "on_this_day"} & set(data.topics)
    assert {"around_the_world", "daylight", "uv"} <= set(data.topics)


def test_transient_failures_are_retried():
    fake = FakeOpenMeteo()
    failures = iter([httpx.ConnectTimeout("handshake"), httpx.Response(503)])

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("archive") and fake.history_calls == 0:
            failure = next(failures, None)
            if isinstance(failure, Exception):
                raise failure
            if failure is not None:
                return httpx.Response(failure.status_code, request=request)
        return fake(request)

    client = httpx.Client(transport=httpx.MockTransport(flaky))
    data = build_weather(replace(CONFIG, featured=CONFIG.featured[:1]), READER_TIME, client=client)
    assert data is not None and "climate_context" in data.topics


def test_missing_climate_history_still_gives_forecasts():
    data = build_weather(CONFIG, READER_TIME, client=_client(FakeOpenMeteo(archive_status=503)))
    assert data is not None
    assert len(data.report.featured) == 2
    assert not {"climate_context", "rain_check", "on_this_day"} & set(data.topics)
    assert data.report.topic in data.topics


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


def test_weather_card_is_the_second_section_with_both_units():
    edition = load_sample()
    html = render_html(edition)
    body = html.split("<body>")[1]
    assert body.index("The setup") < body.index("🌤 Weather") < body.index("🇸🇪 Sweden</span>")
    assert body.count('class="wx-card"') == 2
    assert body.count('class="wx-day"') == 10
    assert body.count('class="wx-cell"') == 6 and "🇺🇸 New York" in body
    assert "16°C" in body and "61°" in body  # both units
    assert "Open-Meteo.com" in body and ", NOAA" in body  # El Niño topic credits NOAA
    # Gmail swaps emoji for <img>; a global block rule would put every flag on its own line.
    assert "img { display: block" not in html and ".story-image { display: block; }" in html

    text = render_text(edition)
    assert text.index("WEATHER") < text.index("SWEDEN")
    assert "Stockholm, today: Partly cloudy, 61°F (16°C)" in text

    bare = render_html(replace(edition, weather=None)).split("<body>")[1]
    assert "🌤 Weather" not in bare and "wx-card" not in bare


def test_footer_has_no_divider_rule():
    html = render_html(load_sample())
    footer_css = next(line for line in html.splitlines() if line.strip().startswith(".footer {"))
    assert "border" not in footer_css


def test_rate_limits_wait_for_the_api_before_retrying(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(weather.time, "sleep", waits.append)
    replies = iter([httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(429)])

    def limited(request: httpx.Request) -> httpx.Response:
        reply = next(replies, None)
        return reply if reply is not None else httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(limited))
    response = weather._get(client, weather.ARCHIVE_URL, {}, weather.REQUEST_TIMEOUT)
    assert response.status_code == 200
    assert waits == [7.0, weather.RATE_LIMIT_WAIT]


def test_weather_step_reports_what_it_produced(monkeypatch, capsys):
    data = build_weather(CONFIG, READER_TIME, client=_client(FakeOpenMeteo()))
    monkeypatch.setattr("briefing.main.build_weather", lambda config, reader_time: data)
    monkeypatch.setattr(
        "briefing.main.write_weather_summary", lambda *args, **kwargs: "**Today:** 59°F."
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    args = SimpleNamespace(sources=ROOT / "config" / "sources.yml")
    settings = SimpleNamespace(
        send_at="", delivery_nonce="", timezone="America/New_York",
        openai_api_key="x", openai_model="gpt-5-mini",
    )
    edition = _add_weather(replace(load_sample(), weather=None), args, settings, DAY)
    assert edition.weather is not None and edition.weather.summary == "**Today:** 59°F."
    out = capsys.readouterr().out
    assert "::notice title=Weather::Widgets: Stockholm" in out and "%0AWrite-up: **Today:**" in out


def test_write_up_paragraphs_are_normalized_and_overlong_drafts_retried(monkeypatch):
    data = weather.WeatherData(report=None, facts={"high": 59, "aqi": 223})  # type: ignore[arg-type]
    long_draft = "**Today:** " + "Warm and bright out there today. " * 20 + "\n**Air:** AQI 223."
    tidy = "**Today:** 59°F and bright.  \n**Air check:** Jakarta's AQI hits 223, so mask up."
    replies = iter([{"summary": long_draft}, {"summary": tidy}])
    monkeypatch.setattr(
        "briefing.editor._generate_structured_output", lambda **kwargs: next(replies)
    )
    summary = write_weather_summary(data, api_key="x", model="m", language="en", day=DAY)
    assert summary == "**Today:** 59°F and bright.\n\n**Air check:** Jakarta's AQI hits 223, so mask up."
    assert weather.summary_shape_problems(summary) == []


def test_write_up_keeps_one_temperature_unit():
    text = "**Today:** Stockholm hits 55°F (13°C), Jakarta 98°F ( 36 °C ).\n**Hari ini:** 33°C (92°F)."
    assert weather.normalize_summary(text) == (
        "**Today:** Stockholm hits 55°F, Jakarta 98°F.\n\n**Hari ini:** 33°C."
    )


def test_city_grids_fill_whole_rows():
    # Three per row on desktop, two on phones: six cities leave no orphan in either.
    for name in ("sources.yml", "indonesia_sources.yml"):
        config = load_weather_config(ROOT / "config" / name)
        assert config is not None and len(config.around) == 6, name
    standard = load_weather_config(ROOT / "config" / "sources.yml")
    assert "Denpasar" not in [place.name for place in standard.around]
