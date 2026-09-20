"""
tests/test_properties.py

Property-based tests (Hypothesis) for services/config.py, per design.md
"Correctness Properties" section.

Property 5: Advisory data serialisation round-trip
Property 6: Configuration serialisation round-trip
Property 7: City config parse round-trip
"""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from services.config import (
    AppConfig,
    CityConfig,
    deserialize_advisory,
    deserialize_config,
    parse_city_config,
    serialize_advisory,
    serialize_config,
)
from services.heat_index import HeatAdvisory

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

_SAFE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=1,
    max_size=60,
)

_SAFE_FLOAT = st.floats(allow_nan=False, allow_infinity=False, width=64)


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
