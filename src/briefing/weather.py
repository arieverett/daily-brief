"""Weather section: Open-Meteo forecasts, climate context, and the editor's write-up.

Everything here is best-effort. If a request fails or the write-up can't be trusted,
the newsletter still goes out (with fewer weather parts, or none). Nothing in this
module may stop the morning send.

Data (free, no key, CC BY 4.0, credited in the email):
- Forecast API: today plus the next week for every configured city, and the past 30
  days for featured cities (for "how has the month been" context).
- Archive API (ERA5 reanalysis): daily history since 1991 for featured cities, used for
  1991-2020 normals and same-week comparisons with past years. It's large, so it's
  cached on disk per city and year (see BRIEF_WEATHER_CACHE) and only fetched again on
  a cache miss, normally once a year.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import yaml

from .models import CityForecast, ForecastDay, WeatherReport

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HISTORY_FIRST_YEAR = 1991
NORMALS_YEARS = range(1991, 2021)  # the WMO 1991-2020 standard normal period
NORMALS_WINDOW_DAYS = 7  # a date's normal averages +/- this many days around it
STRIP_DAYS = 5
WEEK_DAYS = 7
PAST_DAYS = 30
# When the reader opens the email after this local hour in a city, its day is mostly
# over, so the forecast leads with tomorrow (Indonesia is late afternoon at 06:00 ET).
LATE_LOCAL_HOUR = 15
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
ARCHIVE_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
CACHE_ENV = "BRIEF_WEATHER_CACHE"
DEFAULT_CACHE_DIR = Path(".cache") / "weather"

FORECAST_DAILY = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "precipitation_sum",
)
FEATURED_DAILY = (*FORECAST_DAILY, "daylight_duration")
ARCHIVE_DAILY = ("temperature_2m_max", "temperature_2m_min", "precipitation_sum")

# WMO weather interpretation codes -> (theme, icon, English, Indonesian).
# Themes pick the widget's sky color in the template.
CONDITIONS: dict[int, tuple[str, str, str, str]] = {
    0: ("clear", "☀️", "Sunny", "Cerah"),
    1: ("clear", "🌤️", "Mostly sunny", "Cerah berawan"),
    2: ("cloudy", "⛅", "Partly cloudy", "Berawan sebagian"),
    3: ("overcast", "☁️", "Cloudy", "Berawan"),
    45: ("fog", "🌫️", "Fog", "Berkabut"),
    48: ("fog", "🌫️", "Freezing fog", "Kabut beku"),
    51: ("rain", "🌦️", "Light drizzle", "Gerimis ringan"),
    53: ("rain", "🌦️", "Drizzle", "Gerimis"),
    55: ("rain", "🌧️", "Heavy drizzle", "Gerimis lebat"),
    56: ("rain", "🌧️", "Freezing drizzle", "Gerimis beku"),
    57: ("rain", "🌧️", "Freezing drizzle", "Gerimis beku"),
    61: ("rain", "🌦️", "Light rain", "Hujan ringan"),
    63: ("rain", "🌧️", "Rain", "Hujan"),
    65: ("rain", "🌧️", "Heavy rain", "Hujan lebat"),
    66: ("rain", "🌧️", "Freezing rain", "Hujan beku"),
    67: ("rain", "🌧️", "Freezing rain", "Hujan beku"),
    71: ("snow", "🌨️", "Light snow", "Salju ringan"),
    73: ("snow", "🌨️", "Snow", "Salju"),
    75: ("snow", "❄️", "Heavy snow", "Salju lebat"),
    77: ("snow", "🌨️", "Snow grains", "Butiran salju"),
    80: ("rain", "🌦️", "Showers", "Hujan lokal"),
    81: ("rain", "🌧️", "Showers", "Hujan lokal"),
    82: ("storm", "🌧️", "Heavy showers", "Hujan deras"),
    85: ("snow", "🌨️", "Snow showers", "Hujan salju"),
    86: ("snow", "❄️", "Heavy snow showers", "Hujan salju lebat"),
    95: ("storm", "⛈️", "Thunderstorms", "Badai petir"),
    96: ("storm", "⛈️", "Storms, hail", "Badai petir, hujan es"),
    99: ("storm", "⛈️", "Storms, hail", "Badai petir, hujan es"),
}
UNKNOWN_CONDITION = ("overcast", "🌡️", "Mixed", "Campuran")

DAY_ABBR = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "id": ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"),
}
DAY_NAMES = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "id": ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"),
}
TODAY = {"en": "Today", "id": "Hari ini"}
TOMORROW = {"en": "Tomorrow", "id": "Besok"}
TOMORROW_SHORT = {"en": "Tmrw", "id": "Besok"}


# ---------------------------------------------------------------------------
# Configuration


@dataclass(frozen=True)
class Place:
    name: str
    country: str
    latitude: float
    longitude: float
    flag: str = ""

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


@dataclass(frozen=True)
class WeatherConfig:
    language: str  # "en" or "id"
    unit: str  # "F" or "C"
    featured: list[Place]
    around: list[Place] = field(default_factory=list)


def _place(raw: dict[str, Any]) -> Place:
    return Place(
        name=str(raw["name"]),
        country=str(raw["country"]),
        latitude=float(raw["latitude"]),
        longitude=float(raw["longitude"]),
        flag=str(raw.get("flag", "")),
    )


def load_weather_config(sources_path: Path) -> WeatherConfig | None:
    """Read the optional `weather:` block of an edition's sources file."""
    config = yaml.safe_load(Path(sources_path).read_text(encoding="utf-8")) or {}
    raw = config.get("weather")
    if not raw or not raw.get("featured"):
        return None
    unit = str(raw.get("unit", "celsius")).lower()
    return WeatherConfig(
        language=str(raw.get("language", "en")),
        unit="F" if unit.startswith("f") else "C",
        featured=[_place(item) for item in raw["featured"]],
        around=[_place(item) for item in raw.get("around", [])],
    )


# ---------------------------------------------------------------------------
# Fetching


@dataclass(frozen=True)
class DailySeries:
    """One location's daily forecast arrays, in Celsius and millimetres."""

    timezone: str
    dates: list[date]
    values: dict[str, list[float | None]]

    def index(self, day: date) -> int | None:
        try:
            return self.dates.index(day)
        except ValueError:
            return None

    def get(self, key: str, index: int) -> float | None:
        series = self.values.get(key) or []
        return series[index] if 0 <= index < len(series) else None


RETRY_DELAYS = (1.0, 4.0)
# Open-Meteo's free tier limits weighted calls per minute, and one 35-year history download
# weighs as much as hundreds of forecasts. A 429 right after one clears within the minute.
RATE_LIMIT_WAIT = 65.0


def _retry_after(response: httpx.Response) -> float:
    try:
        return min(max(float(response.headers.get("retry-after", "")), 1.0), RATE_LIMIT_WAIT)
    except ValueError:
        return RATE_LIMIT_WAIT


def _get(client: httpx.Client, url: str, params: dict[str, Any], timeout: httpx.Timeout):
    """GET with two retries: quick for blips and 5xx, a minute's wait for rate limits."""
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            response = client.get(url, params=params, timeout=timeout)
        except httpx.TransportError:
            if delay is None:
                raise
        else:
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or delay is None:
                return response
            if response.status_code == 429:
                delay = _retry_after(response)
                print(f"  Weather API rate limit; waiting {delay:.0f}s (attempt {attempt + 1})")
        time.sleep(delay)
    raise AssertionError("unreachable")


def _as_list(payload: Any) -> list[dict[str, Any]]:
    """Open-Meteo returns an object for one location and a list for several."""
    return payload if isinstance(payload, list) else [payload]


def _series(item: dict[str, Any], keys: tuple[str, ...]) -> DailySeries:
    daily = item["daily"]
    return DailySeries(
        timezone=item.get("timezone") or "UTC",
        dates=[date.fromisoformat(value) for value in daily["time"]],
        values={key: list(daily.get(key) or []) for key in keys},
    )


def _coords(places: list[Place]) -> dict[str, str]:
    return {
        "latitude": ",".join(f"{place.latitude:.4f}" for place in places),
        "longitude": ",".join(f"{place.longitude:.4f}" for place in places),
    }


def fetch_forecasts(
    client: httpx.Client, places: list[Place], *, featured: bool
) -> list[DailySeries]:
    """One request for several places; featured places also get the past 30 days."""
    if not places:
        return []
    keys = FEATURED_DAILY if featured else FORECAST_DAILY
    params = {
        **_coords(places),
        "daily": ",".join(keys),
        "timezone": "auto",
        "forecast_days": WEEK_DAYS + 1,
        "past_days": PAST_DAYS + 7 if featured else 1,
    }
    response = _get(client, FORECAST_URL, params, REQUEST_TIMEOUT)
    response.raise_for_status()
    items = _as_list(response.json())
    if len(items) != len(places):
        raise ValueError(f"Expected {len(places)} forecast locations, got {len(items)}")
    return [_series(item, keys) for item in items]


def _cache_dir() -> Path:
    return Path(os.getenv(CACHE_ENV) or DEFAULT_CACHE_DIR)


def _history_path(place: Place, last_year: int) -> Path:
    return _cache_dir() / (
        f"{place.slug}_{place.latitude:.2f}_{place.longitude:.2f}_"
        f"{HISTORY_FIRST_YEAR}-{last_year}.json"
    )


def load_history(client: httpx.Client, place: Place, last_year: int) -> DailySeries:
    """Daily history from 1991 through the end of `last_year`, cached on disk."""
    path = _history_path(place, last_year)
    if path.exists():
        try:
            return _series(json.loads(path.read_text(encoding="utf-8")), ARCHIVE_DAILY)
        except (ValueError, KeyError):
            path.unlink(missing_ok=True)  # corrupt cache: fetch again below

    params = {
        "latitude": f"{place.latitude:.4f}",
        "longitude": f"{place.longitude:.4f}",
        "start_date": f"{HISTORY_FIRST_YEAR}-01-01",
        "end_date": f"{last_year}-12-31",
        "daily": ",".join(ARCHIVE_DAILY),
        "timezone": "auto",
    }
    response = _get(client, ARCHIVE_URL, params, ARCHIVE_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    series = _series(payload, ARCHIVE_DAILY)  # validate before caching
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"timezone": payload.get("timezone"), "daily": payload["daily"]}),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"  WARNING: could not cache weather history for {place.name}: {exc}")
    return series


def fetch_recent(client: httpx.Client, place: Place, local_today: date) -> DailySeries:
    """The latest reanalysis days (it runs about 5 days behind), for calibration."""
    response: httpx.Response | None = None
    # Days the reanalysis hasn't reached come back empty; if the API refuses the range
    # outright, ask again for an older window.
    for lag in (2, 8):
        params = {
            "latitude": f"{place.latitude:.4f}",
            "longitude": f"{place.longitude:.4f}",
            "start_date": (local_today - timedelta(days=PAST_DAYS + 7)).isoformat(),
            "end_date": (local_today - timedelta(days=lag)).isoformat(),
            "daily": ",".join(ARCHIVE_DAILY),
            "timezone": "auto",
        }
        response = _get(client, ARCHIVE_URL, params, REQUEST_TIMEOUT)
        if response.status_code != 400:
            break
    assert response is not None
    response.raise_for_status()
    return _series(response.json(), ARCHIVE_DAILY)


# ---------------------------------------------------------------------------
# Climate arithmetic (all in Celsius / millimetres)


class Climate:
    """Normals and past-year lookups from a city's daily history."""

    def __init__(self, history: DailySeries):
        self._by_date: dict[date, tuple[float | None, float | None, float | None]] = {}
        highs = history.values.get("temperature_2m_max") or []
        lows = history.values.get("temperature_2m_min") or []
        rain = history.values.get("precipitation_sum") or []
        for index, day in enumerate(history.dates):
            self._by_date[day] = (
                highs[index] if index < len(highs) else None,
                lows[index] if index < len(lows) else None,
                rain[index] if index < len(rain) else None,
            )
        self.years = sorted({day.year for day in self._by_date})

    @staticmethod
    def _same_day(year: int, day: date) -> date:
        try:
            return day.replace(year=year)
        except ValueError:  # Feb 29 in a non-leap year
            return date(year, 2, 28)

    def value(self, day: date, field_index: int) -> float | None:
        values = self._by_date.get(day)
        return values[field_index] if values else None

    def normal(self, day: date, field_index: int) -> float | None:
        """1991-2020 average for this time of year (+/- a week)."""
        samples = []
        for year in NORMALS_YEARS:
            center = self._same_day(year, day)
            for offset in range(-NORMALS_WINDOW_DAYS, NORMALS_WINDOW_DAYS + 1):
                value = self.value(center + timedelta(days=offset), field_index)
                if value is not None:
                    samples.append(value)
        return mean(samples) if samples else None

    def same_days_mean(self, year: int, days: list[date], field_index: int) -> float | None:
        values = [self.value(self._same_day(year, day), field_index) for day in days]
        values = [value for value in values if value is not None]
        return mean(values) if len(values) == len(days) else None


HIGH, LOW, RAIN = 0, 1, 2


# ---------------------------------------------------------------------------
# Building the report


def _condition(code: float | None, language: str) -> tuple[str, str, str]:
    theme, icon, english, indonesian = CONDITIONS.get(
        int(code) if code is not None else -1, UNKNOWN_CONDITION
    )
    return theme, icon, indonesian if language == "id" else english


def _temp(celsius: float | None, unit: str) -> int | None:
    if celsius is None:
        return None
    return round(celsius * 9 / 5 + 32) if unit == "F" else round(celsius)


def _temp_delta(celsius: float, unit: str) -> float:
    return round(celsius * 9 / 5 if unit == "F" else celsius, 1)


def _rain_amount(mm: float, unit: str) -> float:
    return round(mm / 25.4, 2) if unit == "F" else round(mm)


def _pct(value: float | None) -> int | None:
    return None if value is None else round(value)


def lead_day(local_now: datetime) -> date:
    """The day a reader cares about when the email lands at `local_now`."""
    today = local_now.date()
    return today + timedelta(days=1) if local_now.hour >= LATE_LOCAL_HOUR else today


def _strip_label(day: date, offset: int, local_today: date, language: str) -> str:
    """First column says Today/Tmrw (like Apple Weather's "Now"); the rest are weekdays."""
    if offset == 0:
        return TODAY[language] if day == local_today else TOMORROW_SHORT[language]
    return DAY_ABBR[language][day.weekday()]


def city_forecast(
    place: Place,
    series: DailySeries,
    reader_time: datetime,
    config: WeatherConfig,
    *,
    with_strip: bool,
) -> tuple[CityForecast, int] | None:
    local_now = reader_time.astimezone(ZoneInfo(series.timezone))
    lead = lead_day(local_now)
    index = series.index(lead)
    if index is None:
        return None
    high = _temp(series.get("temperature_2m_max", index), config.unit)
    low = _temp(series.get("temperature_2m_min", index), config.unit)
    if high is None or low is None:
        return None
    theme, icon, condition = _condition(series.get("weather_code", index), config.language)
    language = config.language

    days: list[ForecastDay] = []
    if with_strip:
        for offset in range(STRIP_DAYS):
            day_index = index + offset
            if day_index >= len(series.dates):
                break
            day = series.dates[day_index]
            day_high = _temp(series.get("temperature_2m_max", day_index), config.unit)
            day_low = _temp(series.get("temperature_2m_min", day_index), config.unit)
            if day_high is None or day_low is None:
                break
            _, day_icon, _ = _condition(series.get("weather_code", day_index), language)
            days.append(
                ForecastDay(
                    date=day.isoformat(),
                    label=_strip_label(day, offset, local_now.date(), language),
                    icon=day_icon,
                    high=day_high,
                    low=day_low,
                    rain_chance=_pct(series.get("precipitation_probability_max", day_index)),
                )
            )

    is_today = lead == local_now.date()
    forecast = CityForecast(
        name=place.name,
        country=place.country,
        flag=place.flag,
        day_label=(TODAY if is_today else TOMORROW)[language],
        condition=condition,
        icon=icon,
        theme=theme,
        high=high,
        low=low,
        rain_chance=_pct(series.get("precipitation_probability_max", index)),
        days=days,
        day_tag="" if is_today else TOMORROW_SHORT[language],
    )
    return forecast, index


def _forecast_facts(
    series: DailySeries, start: int, count: int, config: WeatherConfig
) -> list[dict[str, Any]]:
    facts = []
    for index in range(start, min(start + count, len(series.dates))):
        day = series.dates[index]
        rain = series.get("precipitation_sum", index)
        facts.append(
            {
                "date": day.isoformat(),
                "weekday": DAY_NAMES["en"][day.weekday()],
                "condition": _condition(series.get("weather_code", index), "en")[2],
                "high": _temp(series.get("temperature_2m_max", index), config.unit),
                "low": _temp(series.get("temperature_2m_min", index), config.unit),
                "rain_chance_pct": _pct(series.get("precipitation_probability_max", index)),
                "rain_amount": None if rain is None else _rain_amount(rain, config.unit),
            }
        )
    return facts


MIN_CALIBRATION_DAYS = 10


@dataclass(frozen=True)
class Calibration:
    """How far the forecast model runs from the reanalysis, measured on recent days.

    Forecasts and history come from different models, and in the tropics they can sit a few
    degrees apart (coastal and mountain grid cells especially). Shifting the history by the
    offset measured over the same recent days keeps every "vs normal" honest.
    """

    high: float
    low: float
    days: int


def calibrate(series: DailySeries, recent: DailySeries) -> Calibration | None:
    pairs = []
    for index, day in enumerate(recent.dates):
        forecast_index = series.index(day)
        if forecast_index is None:
            continue
        values = (
            series.get("temperature_2m_max", forecast_index),
            series.get("temperature_2m_min", forecast_index),
            recent.get("temperature_2m_max", index),
            recent.get("temperature_2m_min", index),
        )
        if None not in values:
            pairs.append(values)
    if len(pairs) < MIN_CALIBRATION_DAYS:
        return None
    return Calibration(
        high=mean(pair[0] - pair[2] for pair in pairs),  # type: ignore[operator]
        low=mean(pair[1] - pair[3] for pair in pairs),  # type: ignore[operator]
        days=len(pairs),
    )


def climate_facts(
    series: DailySeries,
    lead_index: int,
    climate: Climate,
    recent: DailySeries,
    config: WeatherConfig,
) -> dict[str, Any]:
    """How this week and the past month compare with 1991-2020 and with past years.

    Returns {} when the forecast and history can't be calibrated against each other.
    """
    calibration = calibrate(series, recent)
    if calibration is None:
        return {}
    unit = config.unit
    lead = series.dates[lead_index]
    facts: dict[str, Any] = {}

    normal_high = climate.normal(lead, HIGH)
    normal_low = climate.normal(lead, LOW)
    lead_high = series.get("temperature_2m_max", lead_index)
    if normal_high is not None and normal_low is not None:
        normal_high += calibration.high
        facts["normal_high"] = _temp(normal_high, unit)
        facts["normal_low"] = _temp(normal_low + calibration.low, unit)
        if lead_high is not None:
            facts["headline_high_vs_normal"] = _temp_delta(lead_high - normal_high, unit)

    last_year_high = climate.value(climate._same_day(lead.year - 1, lead), HIGH)
    if last_year_high is not None:
        facts["same_day_last_year_high"] = _temp(last_year_high + calibration.high, unit)

    # The week ahead against the same calendar week in every year on record.
    week_index = range(lead_index, min(lead_index + WEEK_DAYS, len(series.dates)))
    week_days = [series.dates[index] for index in week_index]
    week_highs = [series.get("temperature_2m_max", index) for index in week_index]
    if len(week_days) == WEEK_DAYS and None not in week_highs:
        week_mean = mean(week_highs)  # type: ignore[arg-type]
        week_normal = calibration.high + mean(
            value for value in (climate.normal(day, HIGH) for day in week_days)
            if value is not None
        )
        past = {
            year: value + calibration.high
            for year in climate.years
            if year < lead.year
            and (value := climate.same_days_mean(year, week_days, HIGH)) is not None
        }
        if past:
            warmer = sorted(year for year, value in past.items() if value > week_mean)
            cooler = sorted(year for year, value in past.items() if value < week_mean)
            facts["week_ahead"] = {
                "first_day": week_days[0].isoformat(),
                "last_day": week_days[-1].isoformat(),
                "average_high": _temp(week_mean, unit),
                "normal_average_high": _temp(week_normal, unit),
                "vs_normal": _temp_delta(week_mean - week_normal, unit),
                "years_compared": len(past),
                "first_year_compared": min(past),
                "warmth_rank": len(warmer) + 1,  # 1 = warmest of all years compared
                "most_recent_warmer_year": warmer[-1] if warmer else None,
                "most_recent_cooler_year": cooler[-1] if cooler else None,
            }

    # The latest 30 days of reanalysis against normals from the same reanalysis.
    rows = [
        (day, recent.get("temperature_2m_max", index), recent.get("temperature_2m_min", index),
         recent.get("precipitation_sum", index))
        for index, day in enumerate(recent.dates)
    ]
    rows = [row for row in rows if None not in row][-PAST_DAYS:]
    if len(rows) >= PAST_DAYS - 5:
        actual = mean((high + low) / 2 for _, high, low, _ in rows)  # type: ignore[operator]
        normals = [(climate.normal(day, HIGH), climate.normal(day, LOW)) for day, *_ in rows]
        stretch: dict[str, Any] = {
            "first_day": rows[0][0].isoformat(),
            "last_day": rows[-1][0].isoformat(),
        }
        if all(high is not None and low is not None for high, low in normals):
            normal = mean((high + low) / 2 for high, low in normals)  # type: ignore[operator]
            stretch["temp_vs_normal"] = _temp_delta(actual - normal, unit)
        total = sum(rain for *_, rain in rows)  # type: ignore[misc]
        normal_rain = sum(
            value for value in (climate.normal(day, RAIN) for day, *_ in rows)
            if value is not None
        )
        stretch["rain"] = _rain_amount(total, unit)
        stretch["normal_rain"] = _rain_amount(normal_rain, unit)
        if normal_rain >= 5:
            stretch["rain_pct_of_normal"] = round(100 * total / normal_rain)
        facts["latest_30_days"] = stretch
    return facts


def daylight_facts(series: DailySeries, lead_index: int) -> dict[str, Any]:
    daylight_now = series.get("daylight_duration", lead_index)
    if daylight_now is None:
        return {}
    minutes = round(daylight_now / 60)
    facts: dict[str, Any] = {"hours": minutes // 60, "minutes": minutes % 60}
    daylight_before = series.get("daylight_duration", lead_index - WEEK_DAYS)
    if daylight_before is not None:
        facts["change_vs_week_ago_minutes"] = round((daylight_now - daylight_before) / 60)
    return facts


@dataclass(frozen=True)
class WeatherData:
    report: WeatherReport  # what the template draws
    facts: dict[str, Any]  # what the editor writes from
    notes: list[str] = field(default_factory=list)  # diagnostics for logs, never published


def build_weather(
    config: WeatherConfig,
    reader_time: datetime,
    client: httpx.Client | None = None,
) -> WeatherData | None:
    """Fetch and assemble the weather. Returns None if not even a forecast is available."""
    own_client = client is None
    client = client or httpx.Client(
        headers={"User-Agent": "daily-brief newsletter (github.com/arieverett/daily-brief)"},
        follow_redirects=True,
    )
    try:
        featured_series = fetch_forecasts(client, config.featured, featured=True)
        try:
            around_series = fetch_forecasts(client, config.around, featured=False)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            print(f"  WARNING: weather for other cities unavailable: {exc}", flush=True)
            around_series = []

        featured: list[CityForecast] = []
        featured_facts: list[dict[str, Any]] = []
        notes: list[str] = []
        pending: list[tuple[Place, DailySeries, int, datetime, dict[str, Any]]] = []
        for place, series in zip(config.featured, featured_series, strict=True):
            built = city_forecast(place, series, reader_time, config, with_strip=True)
            if built is None:
                continue
            city, lead_index = built
            featured.append(city)
            local_now = reader_time.astimezone(ZoneInfo(series.timezone))
            fact: dict[str, Any] = {
                "city": place.name,
                "country": place.country,
                "headline_day": "today" if city.day_tag == "" else "tomorrow",
                "local_time_when_read": f"{local_now:%H:%M}",
                "forecast": _forecast_facts(series, lead_index, WEEK_DAYS, config),
            }
            daylight = daylight_facts(series, lead_index)
            if daylight:
                fact["daylight"] = daylight
            pending.append((place, series, lead_index, local_now, fact))
            featured_facts.append(fact)

        # Light requests first, then the (normally cached) heavy history downloads, so a
        # rate limit after a history download can only delay the next one.
        recents: dict[str, DailySeries | Exception] = {}
        for place, _, _, local_now, _ in pending:
            try:
                recents[place.name] = fetch_recent(client, place, local_now.date())
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                recents[place.name] = exc
        for place, series, lead_index, local_now, fact in pending:
            try:
                recent = recents[place.name]
                if isinstance(recent, Exception):
                    raise recent
                climate = Climate(load_history(client, place, local_now.year - 1))
                calibration = calibrate(series, recent)
                comparison = climate_facts(series, lead_index, climate, recent, config)
                if comparison:
                    fact["climate"] = comparison
                if calibration:
                    notes.append(
                        f"{place.name}: forecast model vs reanalysis over {calibration.days} days: "
                        f"highs {calibration.high:+.1f}°C, lows {calibration.low:+.1f}°C"
                    )
                else:
                    notes.append(f"{place.name}: too little overlap to calibrate; no comparisons")
            except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
                notes.append(f"{place.name}: climate history unavailable: {exc}")

        around: list[CityForecast] = []
        other_facts: list[dict[str, Any]] = []
        for place, series in zip(config.around, around_series, strict=False):
            built = city_forecast(place, series, reader_time, config, with_strip=False)
            if built is None:
                continue
            city, index = built
            around.append(city)
            other_facts.append(
                {
                    "city": place.name,
                    "country": place.country,
                    "day": "tomorrow" if city.day_tag else "today",
                    "condition": _condition(series.get("weather_code", index), "en")[2],
                    "high": city.high,
                    "low": city.low,
                    "rain_chance_pct": city.rain_chance,
                }
            )
    finally:
        if own_client:
            client.close()

    if not featured:
        return None
    # A per-row "tomorrow" tag only helps when rows differ; otherwise say it once.
    tags = {city.day_tag for city in around}
    around_note = ""
    if len(tags) == 1:
        around_note = next(iter(tags))
        around = [replace(city, day_tag="") for city in around]

    report = WeatherReport(
        unit=config.unit,
        summary="",
        featured=featured,
        around=around,
        around_note=around_note,
    )
    facts = {
        "temperature_unit": f"°{config.unit}",
        "rain_unit": "inches" if config.unit == "F" else "mm",
        "normals_period": "1991-2020",
        "featured_cities": featured_facts,
        "other_cities": other_facts,
    }
    for note in notes:
        print(f"  Weather: {note}", flush=True)
    return WeatherData(report=report, facts=facts, notes=notes)


# ---------------------------------------------------------------------------
# The editor's write-up

WEATHER_PROMPT_EN = """You write the weather section of a Morning Brew-style newsletter about Sweden and
Indonesia. Forecast widgets for the featured cities and a table of other cities appear right next to your
text, so don't recite them day by day: tell the reader what the numbers mean.

FACTS
- Use only the conditions and numbers in the weather data. Never invent a temperature, record, warning,
  season start, cause, or comparison. Every figure you write must appear in the data (as rounded there).
- "normal" means the 1991-2020 average for the same time of year. warmth_rank 1 means the warmest of all
  years compared; most_recent_warmer_year / most_recent_cooler_year support lines like "the warmest
  mid-October week since 2018". Only call something a record if warmth_rank is 1 or equals years_compared,
  and since week_ahead is a forecast, say it's "on track to be" or "set to be", never that it was.
  latest_30_days is the most recent month of observations (it ends a few days ago); call it "the past
  month" or name its dates. A city without "climate" has no comparisons: don't make any for it.
- headline_day says whether a city's headline is today or tomorrow (Indonesia is already evening when the
  email lands). Say "tomorrow" for those.
- Temperatures are in the data's unit; write them like 54°F. Rain amounts are in the data's rain unit.

SHAPE
- 3 short paragraphs, 60-110 words in total. Start each with a short bold run-in in double asterisks.
  1. The day ahead in the featured cities, with the most useful practical note (umbrella, layers).
  2. Context: the single most striking comparison (vs normal, vs past years, the past month, daylight).
  3. A contrast across the map or the world from the other cities.
- Morning Brew voice: wry, warm, useful. At most one light joke. Dangerous weather (storms, floods,
  extreme heat or cold) gets a plain, careful tone and no jokes.
- No other markdown, no emoji, no greeting, and never an em dash.
"""

WEATHER_PROMPT_ID = """Anda menulis bagian cuaca newsletter berbahasa Indonesia bergaya Morning Brew.
Widget prakiraan untuk kota utama dan tabel kota lain tampil tepat di samping teks Anda, jadi jangan
membacakannya hari demi hari: jelaskan arti angka-angkanya bagi pembaca.

FAKTA
- Gunakan hanya kondisi dan angka dalam data cuaca. Jangan mengarang suhu, rekor, peringatan, awal musim,
  penyebab, atau perbandingan. Setiap angka yang Anda tulis harus ada di data (dengan pembulatan yang sama).
- "normal" berarti rata-rata 1991-2020 untuk waktu yang sama dalam setahun. warmth_rank 1 berarti terhangat
  dari semua tahun yang dibandingkan; most_recent_warmer_year / most_recent_cooler_year mendukung kalimat
  seperti "minggu terhangat sejak 2018". Sebut rekor hanya bila warmth_rank 1 atau sama dengan years_compared,
  dan karena week_ahead adalah prakiraan, tulis "berpotensi menjadi" atau "diperkirakan", bukan "telah menjadi".
  latest_30_days adalah pengamatan sebulan terakhir (berakhir beberapa hari lalu); sebut "sebulan terakhir"
  atau tanggalnya. Kota tanpa "climate" tidak punya perbandingan: jangan membuat perbandingan untuknya.
- headline_day menunjukkan apakah prakiraan utama sebuah kota untuk hari ini atau besok (di Indonesia
  email ini tiba sore hari). Tulis "besok" untuk yang besok.
- Suhu dalam satuan data; tulis seperti 24°C. Curah hujan dalam mm.

BENTUK
- 3 paragraf pendek, total 60-110 kata. Awali setiap paragraf dengan pembuka tebal singkat dalam tanda
  bintang ganda.
  1. Cuaca kota utama untuk hari yang dibahas, dengan catatan praktis paling berguna (payung, jaket).
  2. Konteks: satu perbandingan paling menarik (dibanding normal, tahun-tahun lalu, sebulan terakhir).
  3. Perbandingan dengan kota lain di Indonesia atau dunia dari tabel.
- Suara Morning Brew: hangat, jenaka secukupnya, berguna. Paling banyak satu lelucon ringan. Cuaca
  berbahaya (badai, banjir, panas atau dingin ekstrem) ditulis lugas dan hati-hati tanpa lelucon.
- Tanpa markdown lain, tanpa emoji, tanpa salam, dan jangan pernah memakai em dash.
"""

WEATHER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {match.replace(",", ".") for match in NUMBER_RE.findall(text)}


def unsupported_numbers(summary: str, facts: dict[str, Any]) -> set[str]:
    """Numbers in the write-up that don't appear anywhere in the data it was given."""
    allowed = _numbers(json.dumps(facts, ensure_ascii=False))
    # Trailing zeros: the data may say 0.5 where prose says 0.50, or 3.0 vs 3.
    allowed |= {value.rstrip("0").rstrip(".") for value in allowed if "." in value}
    found = {value.rstrip("0").rstrip(".") if "." in value else value for value in _numbers(summary)}
    return {value for value in found if value not in allowed}


def write_weather_summary(
    data: WeatherData,
    *,
    api_key: str,
    model: str,
    language: str,
    day: date,
    attempts: int = 2,
) -> str:
    """The editor's write-up, or "" if it can't be produced or keeps citing unknown numbers."""
    from .editor import _generate_structured_output  # local import: editor imports models only
    from .editorial import strip_em_dashes

    instructions = WEATHER_PROMPT_ID if language == "id" else WEATHER_PROMPT_EN
    prompt = (
        f"Edition date: {day.isoformat()}. Weather data as JSON:\n\n"
        + json.dumps(data.facts, ensure_ascii=False)
    )
    for attempt in range(1, attempts + 1):
        try:
            payload = _generate_structured_output(
                api_key=api_key,
                model=model,
                instructions=instructions,
                prompt=prompt,
                schema=WEATHER_SCHEMA,
                schema_name="weather_summary",
                retries=2,
            )
        except Exception as exc:  # noqa: BLE001 - the weather must never block the send
            print(f"  WARNING: weather write-up failed: {type(exc).__name__}: {exc}")
            return ""
        summary = strip_em_dashes(str(payload.get("summary", "")).strip())
        unknown = unsupported_numbers(summary, data.facts)
        if summary and not unknown:
            return summary
        print(
            f"  Weather write-up attempt {attempt} cited numbers not in the data: "
            f"{sorted(unknown)}",
            flush=True,
        )
    return ""


# ---------------------------------------------------------------------------
# Command line: python -m briefing.weather config/sources.yml [...]
# Fetches everything (no write-up, no API key) and prints what a morning run would see.


def _check(paths: list[Path]) -> int:
    reader_time = datetime.now(ZoneInfo("America/New_York")).replace(
        hour=6, minute=0, second=0, microsecond=0
    )
    failed = False
    for path in paths:
        config = load_weather_config(path)
        if config is None:
            print(f"{path}: no weather block")
            continue
        try:
            data = build_weather(config, reader_time)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            print(f"::error title=Weather::{path}: {type(exc).__name__}: {exc}")
            failed = True
            continue
        if data is None:
            print(f"::error title=Weather::{path}: no forecast for any featured city")
            failed = True
            continue
        lines = [f"{path} (°{config.unit}, as read at 06:00 New York time)"]
        for city in data.report.featured:
            strip = "  ".join(f"{d.label} {d.high}/{d.low}" for d in city.days)
            lines.append(
                f"  {city.name} {city.day_label.lower()}: {city.condition}, "
                f"{city.high}/{city.low}, rain {city.rain_chance}% | {strip}"
            )
        for fact in data.facts["featured_cities"]:
            climate = fact.get("climate")
            lines.append(
                f"  {fact['city']} climate: "
                + (json.dumps(climate, ensure_ascii=False) if climate else "UNAVAILABLE")
            )
            if not climate:
                failed = True
        lines.extend(f"  {note}" for note in data.notes)
        for city in data.report.around:
            lines.append(f"  {city.name}: {city.condition}, {city.high}/{city.low}")
        print("::notice title=Weather::" + "%0A".join(lines))
        print("\n".join(lines))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the newsletter's weather data")
    parser.add_argument("paths", nargs="+", type=Path)
    return _check(parser.parse_args(argv).paths)


if __name__ == "__main__":
    sys.exit(main())
