"""
tests/test_weather.py

Unit (example-based) tests for services/weather.py:
- Successful fetch parsing (24-hour hourly slice)
- Retry-then-succeed logic
- Stale-cache fallback when all retries fail
- RuntimeError when all retries fail and no cache is available
- Preset city coordinate lookups
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from services import weather


@pytest.fixture(autouse=True)
def _clear_weather_cache() -> Iterator[None]:
    """Ensure the in-memory fallback cache never leaks between tests."""
    weather._last_good_cache.clear()
    yield
    weather._last_good_cache.clear()


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip real exponential-backoff sleeps so retry tests run instantly."""

    async def _instant_sleep(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(weather.asyncio, "sleep", _instant_sleep)


def _fake_response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    request = httpx.Request("GET", weather.OPEN_METEO_URL)
    return httpx.Response(status_code, json=payload, request=request)


def _build_payload(
    current_temp: float = 38.3,
    current_rh: float = 19.0,
    current_wind: float = 8.4,
    num_hours: int = 48,
    current_hour_index: int = 5,
    latitude: float = 33.44,
    longitude: float = -112.07,
) -> dict[str, Any]:
    base_time = datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc)
    times = [(base_time + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(num_hours)]
    temps = [30.0 + i * 0.1 for i in range(num_hours)]
    rhs = [20.0 for _ in range(num_hours)]

    return {
        "latitude": latitude,
        "longitude": longitude,
        "current": {
            "time": times[current_hour_index],
            "temperature_2m": current_temp,
            "relative_humidity_2m": current_rh,
            "wind_speed_10m": current_wind,
        },
        "hourly": {
            "time": times,
            "temperature_2m": temps,
            "relative_humidity_2m": rhs,
        },
    }


# ---------------------------------------------------------------------------
# get_city_coordinates
# ---------------------------------------------------------------------------

class TestGetCityCoordinates:
    @pytest.mark.parametrize("city_key", list(weather.PRESET_CITIES.keys()))
    def test_returns_coordinates_for_every_preset_key(self, city_key: str) -> None:
        result = weather.get_city_coordinates(city_key)
        assert result == weather.PRESET_CITIES[city_key]

    def test_lookup_is_case_insensitive(self) -> None:
        assert weather.get_city_coordinates("DUBAI") == weather.PRESET_CITIES["dubai"]
        assert weather.get_city_coordinates("Phoenix") == weather.PRESET_CITIES["phoenix"]

    def test_returns_none_for_unknown_key(self) -> None:
        assert weather.get_city_coordinates("atlantis") is None


# ---------------------------------------------------------------------------
# fetch_weather - successful fetch
# ---------------------------------------------------------------------------

class TestFetchWeatherSuccess:
    async def test_successful_fetch_parses_snapshot_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        mock_get = AsyncMock(return_value=_fake_response(payload))
        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

        snapshot = await weather.fetch_weather(33.44, -112.07)

        assert snapshot.current_temperature_c == 38.3
        assert snapshot.current_relative_humidity == 19.0
        assert snapshot.current_wind_speed_kmh == 8.4
        assert snapshot.stale is False
        mock_get.assert_awaited_once()

    async def test_hourly_slice_is_exactly_24_points_from_current_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload(current_hour_index=5)
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        snapshot = await weather.fetch_weather(33.44, -112.07)

        assert len(snapshot.hourly) == 24
        assert snapshot.hourly[0].time == payload["hourly"]["time"][5]
        assert snapshot.hourly[-1].time == payload["hourly"]["time"][28]

    async def test_hourly_slice_truncated_when_fewer_than_24_hours_remain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Only 10 hours remain after the current hour.
        payload = _build_payload(num_hours=15, current_hour_index=5)
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        snapshot = await weather.fetch_weather(33.44, -112.07)

        assert len(snapshot.hourly) == 10


# ---------------------------------------------------------------------------
# fetch_weather - retry logic
# ---------------------------------------------------------------------------

class TestFetchWeatherRetry:
    async def test_succeeds_on_third_attempt_after_two_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        mock_get = AsyncMock(
            side_effect=[
                httpx.ConnectError("simulated network failure"),
                httpx.ConnectError("simulated network failure"),
                _fake_response(payload),
            ]
        )
        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

        snapshot = await weather.fetch_weather(33.44, -112.07)

        assert snapshot.stale is False
        assert snapshot.current_temperature_c == 38.3
        assert mock_get.await_count == 3

    async def test_retries_on_http_status_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        error_response = _fake_response({}, status_code=500)
        mock_get = AsyncMock(side_effect=[error_response, _fake_response(payload)])
        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

        snapshot = await weather.fetch_weather(33.44, -112.07)

        assert snapshot.stale is False
        assert mock_get.await_count == 2


# ---------------------------------------------------------------------------
# fetch_weather - cache fallback / error propagation
# ---------------------------------------------------------------------------

class TestFetchWeatherCacheFallback:
    async def test_returns_stale_cached_snapshot_when_all_retries_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        mock_get = AsyncMock(return_value=_fake_response(payload))
        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

        # Prime the cache with one successful fetch.
        first_snapshot = await weather.fetch_weather(33.44, -112.07)
        assert first_snapshot.stale is False

        # Now force every subsequent attempt to fail.
        mock_get.side_effect = httpx.ConnectError("persistent failure")

        second_snapshot = await weather.fetch_weather(33.44, -112.07)

        assert second_snapshot.stale is True
        assert second_snapshot.current_temperature_c == 38.3

    async def test_raises_runtime_error_when_no_cache_and_all_retries_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_get = AsyncMock(side_effect=httpx.ConnectError("persistent failure"))
        monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

        with pytest.raises(RuntimeError):
            await weather.fetch_weather(10.0, 20.0)

        assert mock_get.await_count == weather.MAX_RETRIES
