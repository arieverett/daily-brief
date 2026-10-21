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
    "uv_index_max",
    "daylight_duration",
    "sunrise",
    "sunset",
)
FEATURED_DAILY = FORECAST_DAILY
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
    # Rotating special topics this edition may pick from (see weather_topics.py).
    topics: list[str] = field(default_factory=list)
    # What the newsletter is about, for the write-up prompt ("Phoenix and Denver").
    about: str = ""


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
        topics=[str(item) for item in raw.get("topics", [])],
        about=str(raw.get("about", "")),
    )


# ---------------------------------------------------------------------------
# Fetching


@dataclass(frozen=True)
class DailySeries:
    """One location's daily forecast arrays, in Celsius and millimetres."""

    timezone: str
    dates: list[date]
    values: dict[str, list[Any]]  # floats, or ISO strings for sunrise/sunset

    def index(self, day: date) -> int | None:
        try:
            return self.dates.index(day)
        except ValueError:
            return None

    def get(self, key: str, index: int) -> Any:
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
# Units. Everything is fetched in °C and mm; editions show both units, main one first.


def to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def temp_pair(celsius: float | None, unit: str) -> tuple[int, int] | None:
    """(main, other) rounded temperatures for an edition whose main unit is `unit`."""
    if celsius is None:
        return None
    f, c = round(to_f(celsius)), round(celsius)
    return (f, c) if unit == "F" else (c, f)


def both(celsius: float) -> dict[str, int]:
    """A temperature for the editor, in both units."""
    return {"°F": round(to_f(celsius)), "°C": round(celsius)}


def both_delta(celsius: float) -> dict[str, float]:
    return {"°F": round(celsius * 9 / 5, 1), "°C": round(celsius, 1)}


def rain_both(mm: float) -> dict[str, float]:
    return {"mm": round(mm), "in": round(mm / 25.4, 2)}


def _condition(code: Any, language: str) -> tuple[str, str, str]:
    theme, icon, english, indonesian = CONDITIONS.get(
        int(code) if code is not None else -1, UNKNOWN_CONDITION
    )
    return theme, icon, indonesian if language == "id" else english


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


# ---------------------------------------------------------------------------
# Building the report


@dataclass
class CityData:
    """Everything known about one city for this edition."""

    place: Place
    series: DailySeries
    index: int  # the lead day's position in `series`
    local_now: datetime
    forecast: CityForecast
    climate: Climate | None = None
    recent: DailySeries | None = None
    calibration: Calibration | None = None

    @property
    def lead(self) -> date:
        return self.series.dates[self.index]

    @property
    def is_tomorrow(self) -> bool:
        return self.lead != self.local_now.date()


def city_data(
    place: Place,
    series: DailySeries,
    reader_time: datetime,
    config: WeatherConfig,
    *,
    with_strip: bool,
) -> CityData | None:
    local_now = reader_time.astimezone(ZoneInfo(series.timezone))
    lead = lead_day(local_now)
    index = series.index(lead)
    if index is None:
        return None
    temps = (
        temp_pair(series.get("temperature_2m_max", index), config.unit),
        temp_pair(series.get("temperature_2m_min", index), config.unit),
    )
    if None in temps:
        return None
    (high, high_alt), (low, low_alt) = temps  # type: ignore[misc]
    language = config.language
    theme, icon, condition = _condition(series.get("weather_code", index), language)

    days: list[ForecastDay] = []
    for offset in range(STRIP_DAYS if with_strip else 0):
        day_index = index + offset
        if day_index >= len(series.dates):
            break
        day_high = temp_pair(series.get("temperature_2m_max", day_index), config.unit)
        day_low = temp_pair(series.get("temperature_2m_min", day_index), config.unit)
        if day_high is None or day_low is None:
            break
        day = series.dates[day_index]
        days.append(
            ForecastDay(
                date=day.isoformat(),
                label=_strip_label(day, offset, local_now.date(), language),
                icon=_condition(series.get("weather_code", day_index), language)[1],
                high=day_high[0],
                low=day_low[0],
                high_alt=day_high[1],
                low_alt=day_low[1],
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
        high_alt=high_alt,
        low_alt=low_alt,
        rain_chance=_pct(series.get("precipitation_probability_max", index)),
        days=days,
        day_tag="" if is_today else TOMORROW_SHORT[language],
    )
    return CityData(place, series, index, local_now, forecast)


def day_facts(city: CityData) -> dict[str, Any]:
    """The lead day for the editor, in both units, conditions in English."""
    series, index = city.series, city.index
    facts: dict[str, Any] = {
        "city": city.place.name,
        "country": city.place.country,
        "day": "tomorrow" if city.is_tomorrow else "today",
        "condition": _condition(series.get("weather_code", index), "en")[2],
        "high": both(series.get("temperature_2m_max", index)),
        "low": both(series.get("temperature_2m_min", index)),
        "rain_chance_pct": _pct(series.get("precipitation_probability_max", index)),
    }
    rain = series.get("precipitation_sum", index)
    if rain:
        facts["rain_amount"] = rain_both(rain)
    uv = series.get("uv_index_max", index)
    if uv is not None:
        facts["uv_index_max"] = round(uv)
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
        high=mean(pair[0] - pair[2] for pair in pairs),
        low=mean(pair[1] - pair[3] for pair in pairs),
        days=len(pairs),
    )


def climate_context_facts(city: CityData) -> dict[str, Any]:
    """The lead day and week ahead vs 1991-2020 normals and every year since 1991."""
    climate, cal, series = city.climate, city.calibration, city.series
    if climate is None or cal is None:
        return {}
    lead = city.lead
    facts: dict[str, Any] = {"city": city.place.name}
    normal_high = climate.normal(lead, HIGH)
    normal_low = climate.normal(lead, LOW)
    lead_high = series.get("temperature_2m_max", city.index)
    if normal_high is not None and normal_low is not None:
        facts["normal_high"] = both(normal_high + cal.high)
        facts["normal_low"] = both(normal_low + cal.low)
        if lead_high is not None:
            facts["high_vs_normal"] = both_delta(lead_high - normal_high - cal.high)
    last_year = climate.value(climate._same_day(lead.year - 1, lead), HIGH)
    if last_year is not None:
        facts["same_day_last_year_high"] = both(last_year + cal.high)

    week_index = range(city.index, min(city.index + WEEK_DAYS, len(series.dates)))
    week_days = [series.dates[index] for index in week_index]
    week_highs = [series.get("temperature_2m_max", index) for index in week_index]
    if len(week_days) == WEEK_DAYS and None not in week_highs:
        week_mean = mean(week_highs)
        normals = [climate.normal(day, HIGH) for day in week_days]
        past = {
            year: value + cal.high
            for year in climate.years
            if year < lead.year
            and (value := climate.same_days_mean(year, week_days, HIGH)) is not None
        }
        if past and None not in normals:
            week_normal = mean(normals) + cal.high  # type: ignore[arg-type]
            warmer = sorted(year for year, value in past.items() if value > week_mean)
            cooler = sorted(year for year, value in past.items() if value < week_mean)
            facts["week_ahead"] = {
                "first_day": week_days[0].isoformat(),
                "last_day": week_days[-1].isoformat(),
                "average_high": both(week_mean),
                "normal_average_high": both(week_normal),
                "vs_normal": both_delta(week_mean - week_normal),
                "years_compared": len(past),
                "first_year_compared": min(past),
                "warmth_rank": len(warmer) + 1,  # 1 = warmest of all years compared
                "most_recent_warmer_year": warmer[-1] if warmer else None,
                "most_recent_cooler_year": cooler[-1] if cooler else None,
            }
    return facts if len(facts) > 1 else {}


def rain_check_facts(city: CityData) -> dict[str, Any]:
    """The latest 30 days of reanalysis against normals from the same reanalysis."""
    climate, recent = city.climate, city.recent
    if climate is None or recent is None:
        return {}
    rows = [
        (day, recent.get("temperature_2m_max", index), recent.get("temperature_2m_min", index),
         recent.get("precipitation_sum", index))
        for index, day in enumerate(recent.dates)
    ]
    rows = [row for row in rows if None not in row][-PAST_DAYS:]
    if len(rows) < PAST_DAYS - 5:
        return {}
    normals = [(climate.normal(day, HIGH), climate.normal(day, LOW)) for day, *_ in rows]
    normal_rain = sum(
        value for value in (climate.normal(day, RAIN) for day, *_ in rows) if value is not None
    )
    total = sum(rain for *_, rain in rows)
    facts: dict[str, Any] = {
        "city": city.place.name,
        "first_day": rows[0][0].isoformat(),
        "last_day": rows[-1][0].isoformat(),
        "rain": rain_both(total),
        "normal_rain": rain_both(normal_rain),
        "rainy_days": sum(1 for *_, rain in rows if rain >= 1),
    }
    if normal_rain >= 5:
        facts["rain_pct_of_normal"] = round(100 * total / normal_rain)
    if all(high is not None and low is not None for high, low in normals):
        actual = mean((high + low) / 2 for _, high, low, _ in rows)
        normal = mean((high + low) / 2 for high, low in normals)  # type: ignore[operator]
        facts["temperature_vs_normal"] = both_delta(actual - normal)
    return facts


def on_this_day_facts(city: CityData) -> dict[str, Any]:
    """Extremes on the lead day's calendar date since 1991, calibrated like the forecast."""
    climate, cal = city.climate, city.calibration
    if climate is None or cal is None:
        return {}
    lead = city.lead
    rows = []
    for year in climate.years:
        if year >= lead.year:
            continue
        day = climate._same_day(year, lead)
        high, rain = climate.value(day, HIGH), climate.value(day, RAIN)
        if high is not None and rain is not None:
            rows.append((year, high + cal.high, rain))
    if len(rows) < 20:
        return {}
    warmest = max(rows, key=lambda row: row[1])
    coolest = min(rows, key=lambda row: row[1])
    wettest = max(rows, key=lambda row: row[2])
    forecast_high = city.series.get("temperature_2m_max", city.index)
    facts: dict[str, Any] = {
        "city": city.place.name,
        "date": lead.isoformat(),
        "years_on_record": len(rows),
        "first_year": rows[0][0],
        "forecast_high": both(forecast_high),
        "warmest_high": {"year": warmest[0], **both(warmest[1])},
        "coolest_high": {"year": coolest[0], **both(coolest[1])},
        "wettest": {"year": wettest[0], **rain_both(wettest[2])},
    }
    return facts


def daylight_facts(city: CityData) -> dict[str, Any]:
    series, index = city.series, city.index
    daylight = series.get("daylight_duration", index)
    if daylight is None:
        return {}
    minutes = round(daylight / 60)
    facts: dict[str, Any] = {
        "city": city.place.name,
        "day": "tomorrow" if city.is_tomorrow else "today",
        "daylight": {"hours": minutes // 60, "minutes": minutes % 60},
    }
    for key in ("sunrise", "sunset"):
        value = series.get(key, index)
        if isinstance(value, str) and "T" in value:
            facts[key] = value.split("T", 1)[1][:5]
    before = series.get("daylight_duration", index - WEEK_DAYS) if index >= WEEK_DAYS else None
    if before is not None:
        facts["change_vs_week_ago_minutes"] = round((daylight - before) / 60)
    return facts


@dataclass(frozen=True)
class WeatherData:
    report: WeatherReport  # what the template draws
    facts: dict[str, Any]  # what the editor writes from
    notes: list[str] = field(default_factory=list)  # diagnostics for logs, never published
    topics: dict[str, Any] = field(default_factory=dict)  # every available topic (for checks)


def build_weather(
    config: WeatherConfig,
    reader_time: datetime,
    client: httpx.Client | None = None,
) -> WeatherData | None:
    """Fetch and assemble the weather. Returns None if not even a forecast is available."""
    from .weather_topics import TopicContext, choose_topic, gather_topics

    own_client = client is None
    client = client or httpx.Client(
        headers={"User-Agent": "daily-brief newsletter (github.com/arieverett/daily-brief)"},
        follow_redirects=True,
    )
    notes: list[str] = []
    try:
        featured_series = fetch_forecasts(client, config.featured, featured=True)
        try:
            around_series = fetch_forecasts(client, config.around, featured=False)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            notes.append(f"other cities unavailable: {exc}")
            around_series = []

        featured = [
            city
            for place, series in zip(config.featured, featured_series, strict=True)
            if (city := city_data(place, series, reader_time, config, with_strip=True))
        ]
        around = [
            city
            for place, series in zip(config.around, around_series, strict=False)
            if (city := city_data(place, series, reader_time, config, with_strip=False))
        ]

        # Light requests first, then the (normally cached) heavy history downloads, so a
        # rate limit after a history download can only delay the next one.
        for city in featured:
            try:
                city.recent = fetch_recent(client, city.place, city.local_now.date())
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                notes.append(f"{city.place.name}: recent observations unavailable: {exc}")
        for city in featured:
            if city.recent is None:
                continue
            try:
                city.climate = Climate(load_history(client, city.place, city.local_now.year - 1))
            except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
                notes.append(f"{city.place.name}: climate history unavailable: {exc}")
                continue
            city.calibration = calibrate(city.series, city.recent)
            if city.calibration:
                notes.append(
                    f"{city.place.name}: forecast model vs reanalysis over "
                    f"{city.calibration.days} days: highs {city.calibration.high:+.1f}°C, "
                    f"lows {city.calibration.low:+.1f}°C"
                )
            else:
                notes.append(f"{city.place.name}: too little overlap to calibrate")

        if not featured:
            return None
        context = TopicContext(
            client=client, config=config, featured=featured, around=around,
            reader_time=reader_time, notes=notes,
        )
        topics = gather_topics(context)
    finally:
        if own_client:
            client.close()

    topic = choose_topic(topics, language=config.language, day=reader_time.date())
    notes.append(
        f"topics available: {', '.join(sorted(topics)) or 'none'}; "
        f"notable: {', '.join(sorted(k for k, t in topics.items() if t.notable)) or 'none'}; "
        f"chosen: {topic or 'none'}"
    )

    # A per-cell "tomorrow" tag only helps when cells differ; otherwise say it once.
    cells = [city.forecast for city in around]
    tags = {cell.day_tag for cell in cells}
    around_note = ""
    if len(tags) == 1:
        around_note = next(iter(tags))
        cells = [replace(cell, day_tag="") for cell in cells]

    report = WeatherReport(
        unit=config.unit,
        summary="",
        featured=[city.forecast for city in featured],
        around=cells,
        around_note=around_note,
        topic=topic or "",
    )
    facts: dict[str, Any] = {
        # The widgets show both units; the prose uses only the edition's own.
        "temperature_style": "°F only, e.g. 55°F" if config.unit == "F" else "°C only, e.g. 13°C",
        "day_ahead": [day_facts(city) for city in featured],
    }
    if topic:
        chosen = topics[topic]
        facts["special_topic"] = {
            "topic": topic,
            "angle": chosen.angle,
            "facts": chosen.facts,
            "background": chosen.background,
        }
    for note in notes:
        print(f"  Weather: {note}", flush=True)
    return WeatherData(report=report, facts=facts, notes=notes, topics=topics)


# ---------------------------------------------------------------------------
# The editor's write-up

WEATHER_PROMPT_EN = """You write the short weather blurb in a Morning Brew-style newsletter about
Sweden and Indonesia. It sits inside a weather card, right under forecast widgets and a table of
cities the reader has just looked at, so never recite forecasts day by day.

Write exactly two short paragraphs separated by a blank line, 35-70 words in total, each starting
with a short bold run-in in double asterisks:
1. The day ahead: ONE sentence, at most 25 words. Give each featured city its high and at most one
   other detail, plus a practical nudge (umbrella, sunscreen, layers) only when the data supports it.
2. Today's special topic: 2-3 sentences, at most 45 words, following its angle with its facts and
   background. Pick the one or two most striking figures and say why they matter to the reader.
   Never list more than three numbers. Give it a playful run-in that names it (for example
   **El Niño watch:**, **Daylight check:**, **On this day:**).

FACTS
- Use only the data. Every number you write must appear in it. Background lines are vetted general
  knowledge: use them as written, never stretch them into new claims.
- Temperatures follow temperature_style: only one unit, never the other in parentheses. Use each city's "day" (today/tomorrow)
  and never mention the time of day.
- Never blame a single day's weather on El Niño or climate change. You may say a pattern is in line
  with what El Niño typically brings when the background says so.
- A forecast can be "on track" for a record but has not set one. Only call something a record when
  the data's rank or extremes show it.

VOICE
- Morning Brew: wry, warm, useful. At most one light joke. Dangerous weather (storms, floods, extreme
  heat or cold) gets a plain, careful tone and no jokes.
- Short sentences (under 20 words on average) and natural contractions (it's, don't, you'll).
- No other markdown, no emoji, no greeting, and never an em dash.
"""

WEATHER_PROMPT_ID = """Anda menulis blurb cuaca singkat untuk newsletter berbahasa Indonesia bergaya
Morning Brew. Blurb ini ada di dalam kartu cuaca, tepat di bawah widget prakiraan dan tabel kota yang
baru dilihat pembaca, jadi jangan membacakan prakiraan hari demi hari.

Tulis tepat dua paragraf pendek yang dipisahkan satu baris kosong, total 35-70 kata, masing-masing
diawali pembuka tebal singkat dalam tanda bintang ganda:
1. Hari yang dibahas: SATU kalimat, maksimal 25 kata. Untuk tiap kota utama sebut suhu tertinggi dan
   paling banyak satu detail lain, plus saran praktis (payung, tabir surya, jaket) hanya bila datanya
   mendukung.
2. Topik khusus hari ini: 2-3 kalimat, maksimal 45 kata, mengikuti angle-nya dengan facts dan
   background-nya. Pilih satu atau dua angka paling mencolok dan jelaskan artinya bagi pembaca. Jangan
   mendaftar lebih dari tiga angka. Beri pembuka yang jenaka dan menyebut topiknya (misalnya
   **Kabar El Niño:**, **Cek siang hari:**, **Hari ini dulu:**).

FAKTA
- Gunakan hanya data. Setiap angka yang Anda tulis harus ada di data. Baris background adalah
  pengetahuan umum yang sudah diperiksa: pakai sesuai isinya, jangan dikembangkan menjadi klaim baru.
- Suhu mengikuti temperature_style: satu satuan saja, tanpa satuan lain dalam kurung. Pakai "day" setiap kota (today = hari ini,
  tomorrow = besok) dan jangan menyebut waktu dalam sehari.
- Jangan menyalahkan cuaca satu hari pada El Niño atau perubahan iklim. Boleh menyebut suatu pola
  sejalan dengan dampak El Niño yang biasa terjadi bila background menyebutkannya.
- Prakiraan bisa "berpotensi" memecahkan rekor tetapi belum memecahkannya. Sebut rekor hanya bila
  peringkat atau ekstrem di data menunjukkannya.

SUARA
- Morning Brew: hangat, jenaka secukupnya, berguna. Paling banyak satu lelucon ringan. Cuaca berbahaya
  (badai, banjir, panas atau dingin ekstrem) ditulis lugas dan hati-hati tanpa lelucon.
- Kalimat pendek (rata-rata di bawah 20 kata).
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


def _trim(value: str) -> str:
    return value.rstrip("0").rstrip(".") if "." in value else value


def unsupported_numbers(summary: str, facts: dict[str, Any]) -> set[str]:
    """Numbers in the write-up that don't appear anywhere in the data it was given."""
    allowed = {_trim(value) for value in _numbers(json.dumps(facts, ensure_ascii=False))}
    return {value for value in map(_trim, _numbers(summary)) if value not in allowed}


def write_weather_summary(
    data: WeatherData,
    *,
    api_key: str,
    model: str,
    language: str,
    day: date,
    attempts: int = 2,
    about: str = "",
) -> str:
    """The editor's write-up, or "" if it can't be produced or keeps citing unknown numbers."""
    from .editor import _generate_structured_output  # local import: editor imports models only
    from .editorial import strip_em_dashes

    instructions = WEATHER_PROMPT_ID if language == "id" else WEATHER_PROMPT_EN
    if about and language != "id":
        instructions = instructions.replace("newsletter about\nSweden and Indonesia.", f"newsletter about\n{about}.")
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
        summary = normalize_summary(strip_em_dashes(str(payload.get("summary", ""))))
        unknown = unsupported_numbers(summary, data.facts)
        if not summary or unknown:
            print(
                f"  Weather write-up attempt {attempt} cited numbers not in the data: "
                f"{sorted(unknown)}",
                flush=True,
            )
            continue
        shape = summary_shape_problems(summary)
        if not shape or attempt == attempts:
            # A slightly long or oddly split write-up beats none; invented numbers never pass.
            return summary
        print(f"  Weather write-up attempt {attempt} off-shape: {'; '.join(shape)}", flush=True)
    return ""


SUMMARY_MAX_WORDS = 80


OTHER_UNIT_RE = re.compile(r"\s*\(\s*-?\d+(?:[.,]\d+)?\s*°\s*[CF]\s*\)")


def normalize_summary(summary: str) -> str:
    """Tidy the editor's write-up.

    - Paragraphs split by a single (markdown) line break become blank-line paragraphs.
    - A second unit in parentheses, e.g. "55°F (13°C)", is dropped: the widgets above show both.
    """
    summary = re.sub(r"[ \t]*\n\s*(?=\*\*)", "\n\n", summary.strip())
    summary = OTHER_UNIT_RE.sub("", summary)
    return re.sub(r"[ \t]+\n", "\n", summary)


def summary_shape_problems(summary: str) -> list[str]:
    parts = [part for part in re.split(r"\n\s*\n", summary) if part.strip()]
    words = len(re.findall(r"[\w'’°%.-]+", summary.replace("**", "")))
    problems = []
    if len(parts) != 2:
        problems.append(f"{len(parts)} paragraphs, want 2")
    if words > SUMMARY_MAX_WORDS:
        problems.append(f"{words} words, want at most {SUMMARY_MAX_WORDS}")
    return problems


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
        unit, alt = config.unit, data.report.alt_unit
        lines = [f"{path} (°{unit} then °{alt}, as read at 06:00 New York time)"]
        for city in data.report.featured:
            strip = "  ".join(f"{d.label} {d.high}/{d.low}" for d in city.days)
            lines.append(
                f"  {city.name} {city.day_label.lower()}: {city.condition}, "
                f"{city.high}°{unit}/{city.high_alt}°{alt}, low {city.low}/{city.low_alt}, "
                f"rain {city.rain_chance}% | {strip}"
            )
        lines.append(
            "  Others: " + ", ".join(f"{c.name} {c.high}/{c.low}" for c in data.report.around)
        )
        lines.extend(f"  {note}" for note in data.notes)
        for key, topic in sorted(data.topics.items()):
            lines.append(
                f"  topic {key}{' (notable)' if topic.notable else ''}: "
                + json.dumps(topic.facts, ensure_ascii=False)[:600]
            )
        expected = set(config.topics)
        missing = expected - set(data.topics)
        if {"climate_context", "rain_check"} & missing:
            failed = True  # climate history or calibration is broken
        lines.append(f"  topics unavailable today: {', '.join(sorted(missing)) or 'none'}")
        print("::notice title=Weather::" + "%0A".join(lines))
        print("\n".join(lines))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the newsletter's weather data")
    parser.add_argument("paths", nargs="+", type=Path)
    return _check(parser.parse_args(argv).paths)


if __name__ == "__main__":
    sys.exit(main())
