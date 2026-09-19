"""
weather.py

Async client for the free Open-Meteo API. Fetches real-time current
conditions and a 24-hour hourly forecast for a given latitude/longitude,
with a small set of preset coordinates for heat-vulnerable cities.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger("heatsafe.weather")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

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

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 0.5

# Simple in-memory cache used as a fallback when the API is unreachable.
_last_good_cache: dict[str, "WeatherSnapshot"] = {}


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


def get_city_coordinates(city_key: str) -> Optional[dict[str, Any]]:
    """Look up preset coordinates by city key (case-insensitive)."""
    return PRESET_CITIES.get(city_key.lower())


async def fetch_weather(latitude: float, longitude: float) -> WeatherSnapshot:
    """
    Fetch current conditions + next 24h hourly forecast from Open-Meteo.

    Retries up to MAX_RETRIES times with exponential backoff on network
    failure. Falls back to the last successfully cached snapshot for these
    coordinates if all retries fail.
    """
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

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(OPEN_METEO_URL, params=params)
                response.raise_for_status()
                data = response.json()
                snapshot = _parse_snapshot(data)
                _last_good_cache[cache_key] = snapshot
                return snapshot
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            last_error = exc
            logger.warning(
                "Open-Meteo fetch failed (attempt %d/%d): %s",
                attempt + 1,
                MAX_RETRIES,
                exc,
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BASE_BACKOFF_SECONDS * (2**attempt))

    logger.error("Open-Meteo fetch exhausted retries: %s", last_error)

    cached = _last_good_cache.get(cache_key)
    if cached is not None:
        cached.stale = True
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
