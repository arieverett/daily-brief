from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from urllib.parse import parse_qs, urlparse

import httpx
from test_weather import CONFIG, READER_TIME, FakeOpenMeteo

from briefing import weather_topics
from briefing.weather import build_weather
from briefing.weather_topics import (
    Topic,
    TopicContext,
    _parse_kp,
    air_quality,
    aurora,
    choose_topic,
    parse_enso_discussion,
    parse_enso_weekly,
)


def _topics(*notable: str) -> dict[str, Topic]:
    keys = ["climate_context", "rain_check", "on_this_day", "daylight", "uv", "el_nino"]
    return {key: Topic(angle=key, facts={}, notable=key in notable) for key in keys}


def test_topic_rotates_daily_is_repeatable_and_never_repeats_yesterday():
    topics = _topics()
    start = date(2026, 1, 1)
    picks = [choose_topic(topics, language="en", day=start + timedelta(days=n)) for n in range(120)]
    assert all(today != yesterday for yesterday, today in pairwise(picks))
    assert set(picks) == set(topics)  # everything comes up
    assert picks == [
        choose_topic(topics, language="en", day=start + timedelta(days=n)) for n in range(120)
    ]
    # The two editions don't move in lockstep.
    indonesian = [
        choose_topic(topics, language="id", day=start + timedelta(days=n)) for n in range(120)
    ]
    assert indonesian != picks


def test_notable_topics_come_up_more_often_but_not_every_day():
    start = date(2026, 1, 1)
    counts = Counter(
        choose_topic(_topics("el_nino"), language="en", day=start + timedelta(days=n))
        for n in range(365)
    )
    assert counts["el_nino"] > 1.8 * max(count for key, count in counts.items() if key != "el_nino")
    assert counts["el_nino"] < 365 / 2


def test_no_topics_means_no_special_topic():
    assert choose_topic({}, language="en", day=date(2026, 10, 16)) is None


ENSO_PAGE = """<html><body><h1>EL NIÑO/SOUTHERN OSCILLATION (ENSO) DIAGNOSTIC DISCUSSION</h1>
<p>issued by CLIMATE PREDICTION CENTER/NCEP/NWS and the International Research Institute
for Climate and Society</p><p>8 October 2026</p>
<p><b>ENSO Alert System Status:</b> El Ni&ntilde;o Advisory&nbsp;</p>
<p><b>Synopsis:</b> El Niño continues to strengthen, with a strong-to-very strong El Niño likely
through January-March 2027. More text follows here.</p></body></html>"""

ENSO_WEEKLY = """ Weekly SST data starts week centered on 3Jan1990
                Nino1+2      Nino3        Nino34        Nino4
 Week          SST SSTA     SST SSTA     SST SSTA     SST SSTA
 24SEP2026     24.5 3.8     28.1 2.9     28.8 2.0     30.1 1.1
 01OCT2026     24.6 3.9     28.2 3.0     28.9 2.1     30.0 1.0
 08OCT2026     22.1-0.4     27.0 3.1     29.0 2.2     29.9 0.9
"""


def test_enso_parsers_read_noaa_status_and_latest_weekly_index():
    found = parse_enso_discussion(ENSO_PAGE)
    assert found["noaa_status"] == "El Niño Advisory"
    assert found["noaa_synopsis"].startswith("El Niño continues to strengthen")
    assert found["noaa_synopsis"].endswith("January-March 2027.")
    assert found["issued"] == "8 October 2026"
    # Negative anomalies run into the SST column (22.1-0.4); the Niño-3.4 pair is 5th/6th.
    assert parse_enso_weekly(ENSO_WEEKLY) == {"week_of": "2026-10-08", "nino34_anomaly_c": 2.2}
    assert parse_enso_weekly("no table here") == {}


def _context(handler) -> TopicContext:
    data_client = httpx.Client(transport=httpx.MockTransport(FakeOpenMeteo()))
    data = build_weather(replace(CONFIG, topics=[]), READER_TIME, client=data_client)
    assert data is not None
    # Rebuild the city data the topics see (build_weather keeps it internal).
    from briefing.weather import city_data, fetch_forecasts

    featured = [
        city_data(place, series, READER_TIME, CONFIG, with_strip=True)
        for place, series in zip(
            CONFIG.featured, fetch_forecasts(data_client, CONFIG.featured, featured=True),
            strict=True,
        )
    ]
    around = [
        city_data(place, series, READER_TIME, CONFIG, with_strip=False)
        for place, series in zip(
            CONFIG.around, fetch_forecasts(data_client, CONFIG.around, featured=False),
            strict=True,
        )
    ]
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TopicContext(client, CONFIG, featured, around, READER_TIME)  # type: ignore[arg-type]


def test_el_nino_topic_is_notable_during_an_advisory():
    def noaa(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("wksst9120.for"):
            return httpx.Response(200, text=ENSO_WEEKLY)
        return httpx.Response(200, text=ENSO_PAGE)

    topic = weather_topics.el_nino(_context(noaa))
    assert topic is not None and topic.notable
    assert topic.facts["nino34_anomaly_c"] == 2.2
    assert any("Indonesia" in line for line in topic.background)


def test_air_quality_takes_each_citys_worst_hour_on_its_day():
    def open_meteo(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        items = []
        for _ in query["latitude"][0].split(","):
            times = [f"2026-10-{day}T{hour:02d}:00" for day in (16, 17) for hour in range(24)]
            aqi = [40 + index for index in range(48)]  # worsens through both days
            items.append({"hourly": {"time": times, "us_aqi": aqi, "pm2_5": [12.0] * 48}})
        return httpx.Response(200, json=items)

    topic = air_quality(_context(open_meteo))
    assert topic is not None
    by_city = {city["city"]: city for city in topic.facts["cities"]}
    assert by_city["Stockholm"]["max_us_aqi"] == 63  # today: hours 0-23
    assert by_city["Jakarta"]["max_us_aqi"] == 87  # tomorrow: hours 24-47
    assert topic.notable is False


def test_kp_forecast_parses_both_swpc_formats_and_skips_summer():
    lists = [["time_tag", "kp", "observed", "noaa_scale"], ["2026-10-16 18:00:00", "5.67", "predicted", "G2"]]
    dicts = [{"time_tag": "2026-10-16T18:00:00", "kp": 5.67, "observed": "predicted"}]
    expected = [(datetime(2026, 10, 16, 18, tzinfo=UTC), 5.67, "predicted")]
    assert _parse_kp(lists) == expected and _parse_kp(dicts) == expected

    def swpc(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=lists)

    context = _context(swpc)
    context.around.append(replace(context.featured[0]))  # a Swedish city is enough
    topic = aurora(context)
    assert topic is not None and topic.notable and topic.facts["max_kp_next_36_hours"] == 5.7
    assert "Swedish time" in topic.facts["peak_timezone"]

    context.reader_time = READER_TIME.replace(month=6)
    assert aurora(context) is None
