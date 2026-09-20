"""
config.py

Centralised configuration loading and data serialisation for HeatSafe
(design.md, Component 4 / Requirement 9).

Responsibilities:
- Load application configuration from environment variables with safe defaults.
- Parse JSON city-preset configuration.
- Serialise/deserialise `HeatAdvisory` objects for JSON API responses.
- Serialise/deserialise `AppConfig` objects (used for round-trip verification).
- Format advisory/history data as CSV for historical export.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any

from services.heat_index import HeatAdvisory

logger = logging.getLogger("heatsafe.config")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_BACKOFF_SECONDS = 0.5
DEFAULT_CACHE_TTL_SECONDS = 300
DEFAULT_HISTORY_FILE_PATH = "./history.csv"
DEFAULT_CITY_CONFIG_PATH = ""

CSV_HEADER: tuple[str, ...] = (
    "timestamp",
    "latitude",
    "longitude",
    "location_name",
    "temperature_c",
    "relative_humidity",
    "heat_index_c",
    "risk_level",
    "band",
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    open_meteo_url: str
    max_retries: int
    base_backoff_seconds: float
    cache_ttl_seconds: int
    history_file_path: str
    city_config_path: str


@dataclass
class CityConfig:
    key: str
    name: str
    lat: float
    lon: float


@dataclass
class HistoryRecord:
    timestamp: str
    latitude: float
    longitude: float
    location_name: str
    temperature_c: float
    relative_humidity: float
    heat_index_c: float
    risk_level: str
    band: str


# ---------------------------------------------------------------------------
# Environment variable helpers
# ---------------------------------------------------------------------------

def _read_str_env(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None:
        logger.info("Environment variable %s not set; using default: %r", name, default)
        return default
    return value


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        logger.info("Environment variable %s not set; using default: %r", name, default)
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.error(
            "Invalid value for environment variable %s=%r (expected an integer); using default: %r",
            name,
            raw,
            default,
        )
        return default


def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        logger.info("Environment variable %s not set; using default: %r", name, default)
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.error(
            "Invalid value for environment variable %s=%r (expected a float); using default: %r",
            name,
            raw,
            default,
        )
        return default


def load_config() -> AppConfig:
    """
    Load application configuration from environment variables, falling back
    to safe hard-coded defaults for anything missing or malformed.

    Priority order: environment variables > hard-coded defaults.
    (City JSON config file loading, if `city_config_path` is set, is the
    caller's responsibility via `parse_city_config`.)
    """
    return AppConfig(
        open_meteo_url=_read_str_env("OPEN_METEO_URL", DEFAULT_OPEN_METEO_URL),
        max_retries=_read_int_env("MAX_RETRIES", DEFAULT_MAX_RETRIES),
        base_backoff_seconds=_read_float_env("BASE_BACKOFF_SECONDS", DEFAULT_BASE_BACKOFF_SECONDS),
        cache_ttl_seconds=_read_int_env("CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS),
        history_file_path=_read_str_env("HISTORY_FILE_PATH", DEFAULT_HISTORY_FILE_PATH),
        city_config_path=_read_str_env("CITY_CONFIG_PATH", DEFAULT_CITY_CONFIG_PATH),
    )


# ---------------------------------------------------------------------------
# AppConfig serialisation (supports Property 6: config round-trip)
# ---------------------------------------------------------------------------

def serialize_config(config: AppConfig) -> dict[str, Any]:
    """Serialise an AppConfig to a JSON-serialisable dict."""
    return asdict(config)


def deserialize_config(data: dict[str, Any]) -> AppConfig:
    """
    Deserialise a dict (as produced by `serialize_config`, or parsed from JSON)
    back into an AppConfig.

    Raises ValueError identifying any missing or invalid field.
    """
    required_fields = (
        "open_meteo_url",
        "max_retries",
        "base_backoff_seconds",
        "cache_ttl_seconds",
        "history_file_path",
        "city_config_path",
    )
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Config data is missing required field(s): {', '.join(missing)}")

    try:
        return AppConfig(
            open_meteo_url=str(data["open_meteo_url"]),
            max_retries=int(data["max_retries"]),
            base_backoff_seconds=float(data["base_backoff_seconds"]),
            cache_ttl_seconds=int(data["cache_ttl_seconds"]),
            history_file_path=str(data["history_file_path"]),
            city_config_path=str(data["city_config_path"]),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Config data has invalid field type(s): {exc}") from exc


# ---------------------------------------------------------------------------
# City configuration parsing
# ---------------------------------------------------------------------------

def parse_city_config(json_str: str) -> list[CityConfig]:
    """
    Parse a JSON array of city preset definitions into a list of CityConfig.

    Expected format:
        [{"key": "dubai", "name": "Dubai, UAE", "lat": 25.2048, "lon": 55.2708}, ...]

    Raises ValueError with a descriptive message on malformed JSON, a
    non-list top-level structure, missing required fields, or invalid field
    types.
    """
    try:
        raw = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"City config is not valid JSON: {exc}") from exc

    if not isinstance(raw, list):
        raise ValueError("City config JSON must be a list of city objects")

    required_fields = ("key", "name", "lat", "lon")
    cities: list[CityConfig] = []

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"City config entry at index {index} must be a JSON object")

        missing = [f for f in required_fields if f not in item]
        if missing:
            raise ValueError(
                f"City config entry at index {index} is missing required field(s): {', '.join(missing)}"
            )

        try:
            cities.append(
                CityConfig(
                    key=str(item["key"]),
                    name=str(item["name"]),
                    lat=float(item["lat"]),
                    lon=float(item["lon"]),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"City config entry at index {index} has invalid field type(s): {exc}") from exc

    return cities


# ---------------------------------------------------------------------------
# HeatAdvisory serialisation (supports Property 5: advisory round-trip)
# ---------------------------------------------------------------------------

_ADVISORY_FIELDS: tuple[str, ...] = (
    "temperature_c",
    "relative_humidity",
    "heat_index_c",
    "heat_index_f",
    "band",
    "risk_level",
    "color",
    "work_rest_ratio",
    "hydration_l_per_hr",
    "micro_advisory",
)


def serialize_advisory(advisory: HeatAdvisory) -> dict[str, Any]:
    """Serialise a HeatAdvisory to a JSON-serialisable dict (UTF-8 compatible types only)."""
    return {
        "temperature_c": advisory.temperature_c,
        "relative_humidity": advisory.relative_humidity,
        "heat_index_c": advisory.heat_index_c,
        "heat_index_f": advisory.heat_index_f,
        "band": advisory.band,
        "risk_level": advisory.risk_level,
        "color": advisory.color,
        "work_rest_ratio": advisory.work_rest_ratio,
        "hydration_l_per_hr": advisory.hydration_l_per_hr,
        "micro_advisory": list(advisory.micro_advisory),
    }


def deserialize_advisory(data: dict[str, Any]) -> HeatAdvisory:
    """
    Deserialise a dict (as produced by `serialize_advisory`, or parsed from
    JSON) back into a HeatAdvisory.

    Raises ValueError identifying any missing or invalid field.
    """
    missing = [f for f in _ADVISORY_FIELDS if f not in data]
    if missing:
        raise ValueError(f"Advisory data is missing required field(s): {', '.join(missing)}")

    try:
        return HeatAdvisory(
            temperature_c=float(data["temperature_c"]),
            relative_humidity=float(data["relative_humidity"]),
            heat_index_c=float(data["heat_index_c"]),
            heat_index_f=float(data["heat_index_f"]),
            band=str(data["band"]),
            risk_level=str(data["risk_level"]),
            color=str(data["color"]),
            work_rest_ratio=str(data["work_rest_ratio"]),
            hydration_l_per_hr=float(data["hydration_l_per_hr"]),
            micro_advisory=list(data["micro_advisory"]),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Advisory data has invalid field type(s): {exc}") from exc


# ---------------------------------------------------------------------------
# CSV formatting
# ---------------------------------------------------------------------------

def advisory_to_csv_row(advisory: HeatAdvisory, timestamp: str) -> str:
    """
    Format a single HeatAdvisory + timestamp as one CSV row (no trailing
    newline), with columns in the same order as the full history CSV header:

        timestamp,latitude,longitude,location_name,temperature_c,
        relative_humidity,heat_index_c,risk_level,band

    A `HeatAdvisory` does not carry location context, so the
    `latitude`/`longitude`/`location_name` columns are emitted as empty
    strings. Use `advisories_to_csv` with `HistoryRecord` objects for a
    fully location-aware export.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow(
        [
            timestamp,
            "",
            "",
            "",
            advisory.temperature_c,
            advisory.relative_humidity,
            advisory.heat_index_c,
            advisory.risk_level,
            advisory.band,
        ]
    )
    return buffer.getvalue()


def advisories_to_csv(records: list[HistoryRecord]) -> str:
    """
    Format a list of HistoryRecord objects as a complete CSV string
    (UTF-8 encoded text), always beginning with the header row. An empty
    `records` list produces a header-only CSV string.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for record in records:
        writer.writerow(
            [
                record.timestamp,
                record.latitude,
                record.longitude,
                record.location_name,
                record.temperature_c,
                record.relative_humidity,
                record.heat_index_c,
                record.risk_level,
                record.band,
            ]
        )
    return buffer.getvalue()
