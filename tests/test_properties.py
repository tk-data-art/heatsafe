"""
tests/test_properties.py

Property-based tests (Hypothesis) per design.md "Correctness Properties":

Property 1: Heat index classification is consistent with thresholds
Property 2: Heat index calculation is deterministic
Property 3: OSHA work/rest ratio matches classification
Property 4: Role-specific advisory is non-empty for all valid inputs
Property 5: Advisory data serialisation round-trip
Property 6: Configuration serialisation round-trip
Property 7: City config parse round-trip
Property 8: Coordinate passthrough to API request
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from services import weather
from services.config import (
    AppConfig,
    CityConfig,
    deserialize_advisory,
    deserialize_config,
    parse_city_config,
    serialize_advisory,
    serialize_config,
)
from services.heat_index import (
    HeatAdvisory,
    Role,
    build_advisory,
    calculate_heat_index_f,
    celsius_to_fahrenheit,
    classify_heat_index,
)

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_SAFE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=1,
    max_size=60,
)


@st.composite
def heat_advisory_strategy(draw: st.DrawFn) -> HeatAdvisory:
    """Generate valid HeatAdvisory objects for round-trip testing."""
    return HeatAdvisory(
        temperature_c=draw(st.floats(min_value=-50, max_value=60, allow_nan=False, allow_infinity=False)),
        relative_humidity=draw(st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False)),
        heat_index_c=draw(st.floats(min_value=-50, max_value=100, allow_nan=False, allow_infinity=False)),
        heat_index_f=draw(st.floats(min_value=-58, max_value=212, allow_nan=False, allow_infinity=False)),
        band=draw(st.sampled_from(["Normal", "Caution", "Extreme Caution", "Danger", "Extreme Danger"])),
        risk_level=draw(st.sampled_from(["Low", "Moderate", "High", "Extreme"])),
        color=draw(st.sampled_from(["green", "yellow", "orange", "red", "darkred"])),
        work_rest_ratio=draw(_SAFE_TEXT),
        hydration_l_per_hr=draw(st.floats(min_value=0, max_value=5, allow_nan=False, allow_infinity=False)),
        micro_advisory=draw(st.lists(_SAFE_TEXT, min_size=1, max_size=5)),
    )


@st.composite
def app_config_strategy(draw: st.DrawFn) -> AppConfig:
    """Generate valid AppConfig objects for round-trip testing."""
    return AppConfig(
        open_meteo_url=draw(_SAFE_TEXT),
        max_retries=draw(st.integers(min_value=0, max_value=10)),
        base_backoff_seconds=draw(st.floats(min_value=0, max_value=10, allow_nan=False, allow_infinity=False)),
        cache_ttl_seconds=draw(st.integers(min_value=0, max_value=100_000)),
        history_file_path=draw(_SAFE_TEXT),
        city_config_path=draw(st.one_of(st.just(""), _SAFE_TEXT)),
    )


@st.composite
def city_config_strategy(draw: st.DrawFn) -> CityConfig:
    """Generate valid CityConfig objects for round-trip testing."""
    return CityConfig(
        key=draw(_SAFE_TEXT),
        name=draw(_SAFE_TEXT),
        lat=draw(st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False)),
        lon=draw(st.floats(min_value=-180, max_value=180, allow_nan=False, allow_infinity=False)),
    )


# ---------------------------------------------------------------------------
# Property 1: Heat index classification is consistent with thresholds
# ---------------------------------------------------------------------------

# Feature: heat-safe, Property 1: classification consistent with thresholds
@settings(max_examples=100)
@given(
    temp_c=st.floats(min_value=-20, max_value=60, allow_nan=False, allow_infinity=False),
    rh=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
)
def test_property_1_classification_consistent_with_thresholds(temp_c: float, rh: float) -> None:
    hi_f = calculate_heat_index_f(celsius_to_fahrenheit(temp_c), rh)
    band = classify_heat_index(hi_f)

    if hi_f < 80.0:
        expected_risk_level = "Low"
    elif hi_f < 91.0:
        expected_risk_level = "Moderate"
    elif hi_f < 103.0:
        expected_risk_level = "High"
    else:
        expected_risk_level = "Extreme"

    assert band.risk_level == expected_risk_level


# ---------------------------------------------------------------------------
# Property 2: Heat index calculation is deterministic
# ---------------------------------------------------------------------------

# Feature: heat-safe, Property 2: heat index is deterministic
@settings(max_examples=100)
@given(
    temp_f=st.floats(min_value=0, max_value=150, allow_nan=False, allow_infinity=False),
    rh=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
)
def test_property_2_heat_index_deterministic(temp_f: float, rh: float) -> None:
    assert calculate_heat_index_f(temp_f, rh) == calculate_heat_index_f(temp_f, rh)


# ---------------------------------------------------------------------------
# Property 3: OSHA work/rest ratio matches classification
# ---------------------------------------------------------------------------

_EXPECTED_RATIO_BY_RISK_LEVEL: dict[str, str] = {
    "Low": "75% work / 25% rest per hour",
    "Moderate": "50% work / 50% rest per hour",
    "High": "25% work / 75% rest per hour",
}


# Feature: heat-safe, Property 3: OSHA ratio matches classification
@settings(max_examples=100)
@given(
    temp_c=st.floats(min_value=-20, max_value=60, allow_nan=False, allow_infinity=False),
    rh=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
)
def test_property_3_osha_ratio_matches_classification(temp_c: float, rh: float) -> None:
    advisory = build_advisory(temp_c, rh, "general_public")

    if advisory.risk_level == "Extreme":
        lowered_ratio = advisory.work_rest_ratio.lower()
        assert "shelter" in lowered_ratio or "cease" in lowered_ratio
    else:
        assert advisory.work_rest_ratio == _EXPECTED_RATIO_BY_RISK_LEVEL[advisory.risk_level]


# ---------------------------------------------------------------------------
# Property 4: Role-specific advisory is non-empty for all valid inputs
# ---------------------------------------------------------------------------

# Feature: heat-safe, Property 4: advisory is non-empty for all inputs
@settings(max_examples=100)
@given(
    temp_c=st.floats(min_value=-20, max_value=60, allow_nan=False, allow_infinity=False),
    rh=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
    role=st.sampled_from(["outdoor_worker", "general_public"]),
)
def test_property_4_advisory_non_empty(temp_c: float, rh: float, role: Role) -> None:
    advisory = build_advisory(temp_c, rh, role)
    assert len(advisory.micro_advisory) >= 1


# ---------------------------------------------------------------------------
# Property 5: Advisory data serialisation round-trip
# ---------------------------------------------------------------------------

# Feature: heat-safe, Property 5: advisory serialisation round-trip
@settings(max_examples=100)
@given(advisory=heat_advisory_strategy())
def test_property_5_advisory_round_trip(advisory: HeatAdvisory) -> None:
    result = deserialize_advisory(serialize_advisory(advisory))
    assert result == advisory


# ---------------------------------------------------------------------------
# Property 6: Configuration serialisation round-trip
# ---------------------------------------------------------------------------

# Feature: heat-safe, Property 6: config serialisation round-trip
@settings(max_examples=100)
@given(config=app_config_strategy())
def test_property_6_config_round_trip(config: AppConfig) -> None:
    json_str = json.dumps(serialize_config(config))
    result = deserialize_config(json.loads(json_str))
    assert result == config


# ---------------------------------------------------------------------------
# Property 7: City config parse round-trip
# ---------------------------------------------------------------------------

# Feature: heat-safe, Property 7: city config parse round-trip
@settings(max_examples=100)
@given(cities=st.lists(city_config_strategy(), min_size=1, max_size=10))
def test_property_7_city_config_round_trip(cities: list[CityConfig]) -> None:
    json_str = json.dumps(
        [{"key": city.key, "name": city.name, "lat": city.lat, "lon": city.lon} for city in cities]
    )
    result = parse_city_config(json_str)
    assert result == cities


# ---------------------------------------------------------------------------
# Property 8: Coordinate passthrough to API request
# ---------------------------------------------------------------------------

def _fake_weather_response(payload: dict[str, Any]) -> httpx.Response:
    request = httpx.Request("GET", weather.OPEN_METEO_URL)
    return httpx.Response(200, json=payload, request=request)


def _minimal_weather_payload() -> dict[str, Any]:
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    times = [(base_time + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(26)]
    return {
        "latitude": 0.0,
        "longitude": 0.0,
        "current": {
            "time": times[0],
            "temperature_2m": 25.0,
            "relative_humidity_2m": 50.0,
            "wind_speed_10m": 5.0,
        },
        "hourly": {
            "time": times,
            "temperature_2m": [25.0] * len(times),
            "relative_humidity_2m": [50.0] * len(times),
        },
    }


# Feature: heat-safe, Property 8: coordinate passthrough
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    lat=st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False),
    lon=st.floats(min_value=-180, max_value=180, allow_nan=False, allow_infinity=False),
)
async def test_property_8_coordinate_passthrough(lat: float, lon: float, monkeypatch: pytest.MonkeyPatch) -> None:
    weather._last_good_cache.clear()
    captured: dict[str, Any] = {}

    async def _instant_sleep(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(weather.asyncio, "sleep", _instant_sleep)

    async def fake_get(
        self: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> httpx.Response:
        # NB: a plain function assigned as a class attribute is a descriptor,
        # so accessing it via an instance (client.get(...)) auto-binds `self`.
        assert params is not None
        captured["latitude"] = params["latitude"]
        captured["longitude"] = params["longitude"]
        return _fake_weather_response(_minimal_weather_payload())

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    await weather.fetch_weather(lat, lon)

    assert captured["latitude"] == lat
    assert captured["longitude"] == lon
