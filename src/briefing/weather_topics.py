"""The weather write-up's rotating special topic.

Each day the write-up covers the day ahead plus ONE special topic, picked at random (but
deterministically per edition and date, and never the same as yesterday's pick) from the
topics whose data is available. Topics that are newsworthy today ("notable", e.g. an
active El Niño or an unusually hot week) are three times as likely to be picked.

Every topic hands the editor two things: `facts` (numbers measured today) and
`background` (short, vetted general-knowledge lines). The editor may use only those, and
every number in the write-up must appear in them (checked in weather.py).

Adding a topic: write a gatherer that returns a Topic or None, add it to GATHERERS, and
list its key under `weather: topics:` in the edition's sources file.
"""

from __future__ import annotations

import html
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from statistics import mean
from typing import Any

import httpx

from .weather import (
    REQUEST_TIMEOUT,
    CityData,
    WeatherConfig,
    _get,
    climate_context_facts,
    day_facts,
    daylight_facts,
    on_this_day_facts,
    rain_check_facts,
)

AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
KP_FORECAST_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
ENSO_DISCUSSION_URL = (
    "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"
)
ENSO_WEEKLY_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"
NOTABLE_WEIGHT = 3


@dataclass(frozen=True)
class Topic:
    angle: str  # what the write-up should focus on
    facts: dict[str, Any]
    background: list[str] = field(default_factory=list)
    notable: bool = False


@dataclass
class TopicContext:
    client: httpx.Client
    config: WeatherConfig
    featured: list[CityData]
    around: list[CityData]
    reader_time: datetime
    notes: list[str] = field(default_factory=list)

    @property
    def cities(self) -> list[CityData]:
        return [*self.featured, *self.around]


# ---------------------------------------------------------------------------
# Topics built from data the weather step already has


def climate_context(ctx: TopicContext) -> Topic | None:
    facts = [found for city in ctx.featured if (found := climate_context_facts(city))]
    if not facts:
        return None
    notable = False
    for city in facts:
        week = city.get("week_ahead") or {}
        rank, years = week.get("warmth_rank"), week.get("years_compared")
        if rank and years and (rank <= 3 or rank >= years - 2):
            notable = True
        if abs((city.get("high_vs_normal") or {}).get("°C", 0)) >= 4:
            notable = True
    return Topic(
        angle="How the day and the week ahead compare with normal and with past years.",
        facts={"cities": facts},
        background=[
            "Normals are 1991-2020 averages for the same time of year.",
            "warmth_rank 1 means the warmest of all years compared for that calendar week.",
        ],
        notable=notable,
    )


def rain_check(ctx: TopicContext) -> Topic | None:
    facts = [found for city in ctx.featured if (found := rain_check_facts(city))]
    if not facts:
        return None
    notable = any(
        (pct := city.get("rain_pct_of_normal")) is not None and (pct < 50 or pct > 175)
        for city in facts
    )
    return Topic(
        angle="How wet or dry the past month has been compared with normal, and what that means.",
        facts={"cities": facts},
        background=["Rain normals are 1991-2020 averages for the same dates."],
        notable=notable,
    )


def on_this_day(ctx: TopicContext) -> Topic | None:
    facts = [found for city in ctx.featured if (found := on_this_day_facts(city))]
    if not facts:
        return None
    notable = any(
        abs(city["forecast_high"]["°C"] - city["warmest_high"]["°C"]) <= 1
        or abs(city["forecast_high"]["°C"] - city["coolest_high"]["°C"]) <= 1
        for city in facts
    )
    return Topic(
        angle="A fun look back: the warmest, coolest and wettest this calendar date has been, "
        "and how the forecast stacks up.",
        facts={"cities": facts},
        background=["Records here are from the ERA5 reanalysis, which starts in this data in 1991."],
        notable=notable,
    )


def around_the_world(ctx: TopicContext) -> Topic | None:
    if not ctx.around:
        return None
    return Topic(
        angle="A quick, fun contrast across the map: who's sweating, who's shivering, who's "
        "getting soaked, compared with the featured cities.",
        facts={"cities": [day_facts(city) for city in ctx.cities]},
    )


def daylight(ctx: TopicContext) -> Topic | None:
    facts = [found for city in ctx.cities if (found := daylight_facts(city))]
    if len(facts) < 2:
        return None
    day = ctx.reader_time.date()
    near_turning_point = any(
        abs((day - date(day.year, month, dom)).days) <= 3
        for month, dom in ((3, 20), (6, 21), (9, 22), (12, 21))
    )
    return Topic(
        angle="Daylight: how long the day is in different cities and how fast it's changing.",
        facts={"cities": facts},
        background=[
            "Day length changes fastest around the equinoxes and barely changes near the equator.",
            ("North of the Arctic Circle, as in Kiruna, the sun stays below the horizon for a few "
            "weeks around the December solstice (polar night)."),
        ],
        notable=near_turning_point,
    )


def uv(ctx: TopicContext) -> Topic | None:
    facts = [
        {"city": city.place.name, "day": found["day"], "uv_index_max": found["uv_index_max"]}
        for city in ctx.cities
        if "uv_index_max" in (found := day_facts(city))
    ]
    if len(facts) < 2:
        return None
    return Topic(
        angle="Sun strength: the UV index across cities and practical sun sense.",
        facts={"cities": facts},
        background=[
            ("WHO UV index scale: 0-2 low, 3-5 moderate, 6-7 high, 8-10 very high, 11 and up "
            "extreme."),
            "Near the equator the midday sun is high all year, so UV stays high year-round.",
        ],
    )


# ---------------------------------------------------------------------------
# Topics that need one more small request


def _iso_hours(item: dict[str, Any], key: str) -> list[tuple[str, Any]]:
    hourly = item.get("hourly") or {}
    return list(zip(hourly.get("time") or [], hourly.get(key) or [], strict=False))


def air_quality(ctx: TopicContext) -> Topic | None:
    cities = ctx.cities
    params = {
        "latitude": ",".join(f"{city.place.latitude:.4f}" for city in cities),
        "longitude": ",".join(f"{city.place.longitude:.4f}" for city in cities),
        "hourly": "us_aqi,pm2_5",
        "timezone": "auto",
        "forecast_days": 3,
    }
    response = _get(ctx.client, AIR_QUALITY_URL, params, REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    items = payload if isinstance(payload, list) else [payload]
    facts = []
    for city, item in zip(cities, items, strict=False):
        day = city.lead.isoformat()
        aqi = [value for time, value in _iso_hours(item, "us_aqi") if time.startswith(day) and value is not None]
        pm = [value for time, value in _iso_hours(item, "pm2_5") if time.startswith(day) and value is not None]
        if aqi:
            facts.append(
                {
                    "city": city.place.name,
                    "day": "tomorrow" if city.is_tomorrow else "today",
                    "max_us_aqi": round(max(aqi)),
                    "average_pm2_5_ug_m3": round(mean(pm)) if pm else None,
                }
            )
    if len(facts) < 2:
        return None
    featured = {city.place.name for city in ctx.featured}
    notable = any(city["max_us_aqi"] >= 101 for city in facts if city["city"] in featured)
    return Topic(
        angle="Air quality: who's breathing easy and who isn't, with practical health context.",
        facts={"cities": facts},
        background=[
            ("US AQI: 0-50 good, 51-100 moderate, 101-150 unhealthy for sensitive groups, 151-200 "
            "unhealthy, 201-300 very unhealthy, 301 and up hazardous."),
            "These are model forecasts (Copernicus CAMS via Open-Meteo), not station readings.",
        ],
        notable=notable,
    )


def _parse_kp(payload: Any) -> list[tuple[datetime, float, str]]:
    """SWPC's Kp forecast as (UTC time, Kp, 'observed'/'estimated'/'predicted')."""
    rows: list[dict[str, Any]]
    if payload and isinstance(payload[0], list):
        header = [str(name) for name in payload[0]]
        rows = [dict(zip(header, row, strict=False)) for row in payload[1:]]
    else:
        rows = list(payload or [])
    parsed = []
    for row in rows:
        try:
            when = datetime.fromisoformat(str(row["time_tag"]).replace(" ", "T").rstrip("Z"))
            kp = float(row.get("kp", row.get("Kp")))
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append((when.replace(tzinfo=UTC), kp, str(row.get("observed", ""))))
    return parsed


def aurora(ctx: TopicContext) -> Topic | None:
    sweden = [city for city in ctx.cities if city.place.country == "Sweden"]
    month = ctx.reader_time.month
    if not sweden or month in (4, 5, 6, 7, 8):  # too light at night for auroras
        return None
    response = _get(ctx.client, KP_FORECAST_URL, {}, REQUEST_TIMEOUT)
    response.raise_for_status()
    start = ctx.reader_time.astimezone(UTC)
    window = [
        (when, kp) for when, kp, _ in _parse_kp(response.json())
        if start <= when < start + timedelta(hours=36)
    ]
    if not window:
        return None
    peak_time, peak = max(window, key=lambda row: row[1])
    local = peak_time.astimezone(sweden[0].local_now.tzinfo)
    return Topic(
        angle="Northern lights odds over Sweden in the next day and a half.",
        facts={
            "max_kp_next_36_hours": round(peak, 1),
            "peak_window_starts_local_time": f"{local:%A %H:%M}",
            "peak_timezone": "Swedish time",
            "cities": [city.place.name for city in sweden],
        },
        background=[
            ("The Kp index runs from 0 to 9; Kp 5 or higher is a geomagnetic storm (NOAA G1 or "
            "stronger)."),
            "Auroras need dark, clear skies.",
            ("Kiruna sits under the auroral oval and often sees auroras on clear, dark nights even "
            "at modest Kp; Stockholm usually needs a geomagnetic storm."),
        ],
        notable=peak >= 5,
    )


MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)


def parse_enso_discussion(page: str) -> dict[str, str]:
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())
    found: dict[str, str] = {}
    status = re.search(r"ENSO Alert System Status:\s*(.+?)\s*(?:Synopsis:|$)", text)
    if status:
        found["noaa_status"] = status.group(1)[:80].strip()
    synopsis = re.search(r"Synopsis:\s*(.+?\.)(?:\s|$)", text)
    if synopsis:
        found["noaa_synopsis"] = synopsis.group(1)[:300]
    issued = re.search(rf"\b(\d{{1,2}} (?:{MONTHS}) 20\d\d)\b", text)
    if issued:
        found["issued"] = issued.group(1)
    return found


def parse_enso_weekly(text: str) -> dict[str, Any]:
    """Last line of CPC's weekly Niño table: date, then SST/anomaly pairs per region."""
    for line in reversed([line for line in text.splitlines() if line.strip()]):
        parts = line.split(maxsplit=1)
        numbers = re.findall(r"-?\d+\.\d", parts[1]) if len(parts) == 2 else []
        if len(numbers) >= 8 and re.fullmatch(r"\d{2}[A-Z]{3}\d{4}", parts[0]):
            week = datetime.strptime(parts[0].title() + " +0000", "%d%b%Y %z").date()
            return {"week_of": week.isoformat(), "nino34_anomaly_c": float(numbers[5])}
    return {}


def el_nino(ctx: TopicContext) -> Topic | None:
    response = _get(ctx.client, ENSO_DISCUSSION_URL, {}, REQUEST_TIMEOUT)
    response.raise_for_status()
    facts: dict[str, Any] = parse_enso_discussion(response.text)
    if "noaa_status" not in facts:
        return None
    try:
        weekly = _get(ctx.client, ENSO_WEEKLY_URL, {}, REQUEST_TIMEOUT)
        weekly.raise_for_status()
        facts.update(parse_enso_weekly(weekly.text))
        lines = [line for line in weekly.text.splitlines() if line.strip()]
        ctx.notes.append("ENSO weekly table: " + " | ".join(lines[:3] + lines[-2:]))
    except httpx.HTTPError as exc:
        ctx.notes.append(f"El Niño weekly index unavailable: {exc}")
    # Bandung or Jakarta's dry/wet month, if we have it, so the editor can tie it in.
    local = [found for city in ctx.featured if city.place.country == "Indonesia"
             if (found := rain_check_facts(city))]
    if local:
        facts["indonesia_past_month"] = local
    status = facts["noaa_status"].lower()
    return Topic(
        angle="The bigger picture: the current El Niño/La Niña state, what it typically means "
        "for Indonesia and for global temperatures, and (lightly) for Sweden.",
        facts=facts,
        background=[
            ("El Niño is a periodic warming of the central and eastern tropical Pacific that "
            "shifts rainfall and temperature patterns around the world."),
            ("Niño-3.4 is the patch of the Pacific NOAA uses to measure it; the anomaly is how far "
            "its sea surface runs above (or below) normal."),
            ("El Niño typically brings drier-than-normal conditions to much of Indonesia, "
            "especially from about June to November, raising drought and wildfire-haze risk."),
            ("El Niño's influence on weather in Sweden and northern Europe is weak and "
            "inconsistent."),
            ("Global average temperatures tend to run higher during and just after strong El "
            "Niños: 2016 and 2024 set global heat records after the 2015-16 and 2023-24 events."),
            ("Forecasters usually call an El Niño 'strong' when Niño-3.4 runs about 1.5°C or more "
            "above normal, and 'very strong' (popularly 'super') at about 2.0°C or more."),
            "The 1997-98 and 2015-16 El Niños are the usual benchmarks for 'super' events.",
        ],
        notable="advisory" in status,
    )


GATHERERS: dict[str, Callable[[TopicContext], Topic | None]] = {
    "climate_context": climate_context,
    "rain_check": rain_check,
    "on_this_day": on_this_day,
    "around_the_world": around_the_world,
    "daylight": daylight,
    "uv": uv,
    "air_quality": air_quality,
    "el_nino": el_nino,
    "aurora": aurora,
}


def gather_topics(ctx: TopicContext) -> dict[str, Topic]:
    """Every configured topic that has data today. Each is best-effort."""
    found: dict[str, Topic] = {}
    for key in ctx.config.topics:
        gather = GATHERERS.get(key)
        if gather is None:
            ctx.notes.append(f"unknown weather topic {key!r} in config")
            continue
        try:
            topic = gather(ctx)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
            ctx.notes.append(f"topic {key} unavailable: {type(exc).__name__}: {exc}")
            continue
        if topic is not None:
            found[key] = topic
    return found


def _pick(weights: dict[str, int], seed: str) -> str | None:
    if not weights:
        return None
    keys = sorted(weights)
    return random.Random(seed).choices(keys, weights=[weights[key] for key in keys])[0]


ROTATION_START = date(2026, 1, 1)


def choose_topic(topics: dict[str, Topic], *, language: str, day: date) -> str | None:
    """Weighted, repeatable per edition and day, and never yesterday's pick.

    The sequence is replayed from a fixed start date with today's topics, so "yesterday's
    pick" is the one yesterday actually made whenever the same topics were available.
    """
    weights = {key: NOTABLE_WEIGHT if topic.notable else 1 for key, topic in topics.items()}
    if not weights:
        return None
    start = min(ROTATION_START, day)
    previous: str | None = None
    for offset in range((day - start).days + 1):
        current = start + timedelta(days=offset)
        allowed = {key: weight for key, weight in weights.items() if key != previous} or weights
        previous = _pick(allowed, f"{language}:{current.isoformat()}")
    return previous
