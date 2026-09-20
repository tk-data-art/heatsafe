"""
weather.py

Async client for the free Open-Meteo API. Fetches real-time current
conditions and a 24-hour hourly forecast for a given latitude/longitude,
with a small set of preset coordinates for heat-vulnerable cities.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from services.config import AppConfig, load_config

logger = logging.getLogger("heatsafe.weather")

# Fallback defaults, also used as the AppConfig defaults (services/config.py).
# Kept here for backward compatibility with callers/tests that reference
# these constants directly; actual runtime behaviour is driven by AppConfig
# (see `fetch_weather`'s `config` parameter).
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 0.5

# ---------------------------------------------------------------------------
# Preset coordinates for heat-vulnerable cities
# ---------------------------------------------------------------------------

PRESET_CITIES: dict[str, dict[str, Any]] = {
    "dubai": {"name": "Dubai, UAE", "lat": 25.2048, "lon": 55.2708},
    "phoenix": {"name": "Phoenix, USA", "lat": 33.4484, "lon": -112.0740},
    "chennai": {"name": "Chennai, India", "lat": 13.0827, "lon": 80.2707},
    "las_vegas": {"name": "Las Vegas, USA", "lat": 36.1699, "lon": -115.1398},
    "delhi": {"name": "Delhi, India", "lat": 28.7041, "lon": 77.1025},
    "riyadh": {"name": "Riyadh, Saudi Arabia", "lat": 24.7136, "lon": 46.6753},
}

# Simple in-memory cache used as a fallback when the API is unreachable.
_last_good_cache: dict[str, WeatherSnapshot] = {}
_cache_fetched_at: dict[str, float] = {}


@dataclass
class HourlyPoint:
    time: str
    temperature_c: float
    relative_humidity: float


@dataclass
class WeatherSnapshot:
    latitude: float
    longitude: float
    current_temperature_c: float
    current_relative_humidity: float
    current_wind_speed_kmh: float
    hourly: list[HourlyPoint]
    stale: bool = False


def get_city_coordinates(city_key: str) -> dict[str, Any] | None:
    """Look up preset coordinates by city key (case-insensitive)."""
    return PRESET_CITIES.get(city_key.lower())


async def fetch_weather(
    latitude: float, longitude: float, config: AppConfig | None = None
) -> WeatherSnapshot:
    """
    Fetch current conditions + next 24h hourly forecast from Open-Meteo.

    Retries up to `config.max_retries` times with exponential backoff
    (base `config.base_backoff_seconds`) on network failure. Falls back to
    the last successfully cached snapshot for these coordinates if all
    retries fail; `config.cache_ttl_seconds` is used only to decide whether
    to log an extra staleness warning (cached data is always preferred over
    no data at all).

    If `config` is not supplied, configuration is loaded fresh via
    `services.config.load_config()` (environment variables with safe
    defaults).
    """
    cfg = config if config is not None else load_config()
    cache_key = f"{round(latitude, 4)},{round(longitude, 4)}"

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "hourly": "temperature_2m,relative_humidity_2m",
        "forecast_days": 2,
        "timezone": "auto",
    }

    last_error: Exception | None = None

    for attempt in range(cfg.max_retries):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(cfg.open_meteo_url, params=params)
                response.raise_for_status()
                data = response.json()
                snapshot = _parse_snapshot(data)
                _last_good_cache[cache_key] = snapshot
                _cache_fetched_at[cache_key] = time.monotonic()
                return snapshot
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            last_error = exc
            logger.warning(
                "Open-Meteo fetch failed (attempt %d/%d): %s",
                attempt + 1,
                cfg.max_retries,
                exc,
            )
            if attempt < cfg.max_retries - 1:
                await asyncio.sleep(cfg.base_backoff_seconds * (2**attempt))

    logger.error("Open-Meteo fetch exhausted retries: %s", last_error)

    cached = _last_good_cache.get(cache_key)
    if cached is not None:
        cached.stale = True
        age_seconds = time.monotonic() - _cache_fetched_at.get(cache_key, 0.0)
        if age_seconds > cfg.cache_ttl_seconds:
            logger.warning(
                "Serving weather cache older than TTL for %s (%.0fs > %ds)",
                cache_key,
                age_seconds,
                cfg.cache_ttl_seconds,
            )
        return cached

    raise RuntimeError(
        f"Unable to retrieve weather data and no cached data available: {last_error}"
    )


def _parse_snapshot(data: dict[str, Any]) -> WeatherSnapshot:
    """Parse raw Open-Meteo JSON into a WeatherSnapshot, keeping next 24h hourly points."""
    current = data["current"]
    hourly_raw = data["hourly"]

    times: list[str] = hourly_raw["time"]
    temps: list[float] = hourly_raw["temperature_2m"]
    rhs: list[float] = hourly_raw["relative_humidity_2m"]

    current_time = current["time"]
    start_index = 0
    for i, t in enumerate(times):
        if t >= current_time:
            start_index = i
            break

    end_index = min(start_index + 24, len(times))

    hourly = [
        HourlyPoint(time=times[i], temperature_c=temps[i], relative_humidity=rhs[i])
        for i in range(start_index, end_index)
    ]

    return WeatherSnapshot(
        latitude=data.get("latitude", 0.0),
        longitude=data.get("longitude", 0.0),
        current_temperature_c=current["temperature_2m"],
        current_relative_humidity=current["relative_humidity_2m"],
        current_wind_speed_kmh=current.get("wind_speed_10m", 0.0),
        hourly=hourly,
        stale=False,
    )
