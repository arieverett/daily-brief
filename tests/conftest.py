import pytest

from briefing import weather


@pytest.fixture(autouse=True)
def isolated_weather_cache(tmp_path, monkeypatch):
    """No test may read or write the real weather cache, or wait on retry back-offs."""
    monkeypatch.setenv(weather.CACHE_ENV, str(tmp_path / "weather-cache"))
    monkeypatch.setattr(weather.time, "sleep", lambda seconds: None)
