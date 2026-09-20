"""
tests/test_config.py

Unit (example-based) tests for services/config.py:
- load_config() environment variable parsing, defaults, and error handling
- parse_city_config() JSON parsing and validation
- serialize_advisory / deserialize_advisory round-trip and error handling
- serialize_config / deserialize_config round-trip
- advisory_to_csv_row / advisories_to_csv formatting
"""

from __future__ import annotations

import csv
import io
import json
import logging

import pytest

from services import config as config_module
from services.config import (
    AppConfig,
    CityConfig,
    HistoryRecord,
    advisories_to_csv,
    advisory_to_csv_row,
    deserialize_advisory,
    deserialize_config,
    load_config,
    parse_city_config,
    serialize_advisory,
    serialize_config,
)
from services.heat_index import HeatAdvisory

ALL_CONFIG_ENV_VARS = (
    "OPEN_METEO_URL",
    "MAX_RETRIES",
    "BASE_BACKOFF_SECONDS",
    "CACHE_TTL_SECONDS",
    "HISTORY_FILE_PATH",
    "CITY_CONFIG_PATH",
)


@pytest.fixture(autouse=True)
def _clear_config_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no host environment variables leak into config tests."""
    for var in ALL_CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _sample_advisory() -> HeatAdvisory:
    return HeatAdvisory(
        temperature_c=35.0,
        relative_humidity=60.0,
        heat_index_c=45.1,
        heat_index_f=113.1,
        band="Danger",
        risk_level="Extreme",
        color="red",
        work_rest_ratio="Cease strenuous work / seek shelter",
        hydration_l_per_hr=1.0,
        micro_advisory=["Drink at least 1 L of water per hour.", "Suspend non-essential outdoor work."],
    )


# ---------------------------------------------------------------------------
# load_config()
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_defaults_when_no_env_vars_set(self) -> None:
        result = load_config()

        assert result == AppConfig(
            open_meteo_url=config_module.DEFAULT_OPEN_METEO_URL,
            max_retries=config_module.DEFAULT_MAX_RETRIES,
            base_backoff_seconds=config_module.DEFAULT_BASE_BACKOFF_SECONDS,
            cache_ttl_seconds=config_module.DEFAULT_CACHE_TTL_SECONDS,
            history_file_path=config_module.DEFAULT_HISTORY_FILE_PATH,
            city_config_path=config_module.DEFAULT_CITY_CONFIG_PATH,
        )

    def test_valid_env_vars_parsed_to_correct_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPEN_METEO_URL", "https://example.test/forecast")
        monkeypatch.setenv("MAX_RETRIES", "5")
        monkeypatch.setenv("BASE_BACKOFF_SECONDS", "1.5")
        monkeypatch.setenv("CACHE_TTL_SECONDS", "600")
        monkeypatch.setenv("HISTORY_FILE_PATH", "/tmp/history.csv")
        monkeypatch.setenv("CITY_CONFIG_PATH", "/tmp/cities.json")

        result = load_config()

        assert result.open_meteo_url == "https://example.test/forecast"
        assert result.max_retries == 5
        assert isinstance(result.max_retries, int)
        assert result.base_backoff_seconds == 1.5
        assert isinstance(result.base_backoff_seconds, float)
        assert result.cache_ttl_seconds == 600
        assert isinstance(result.cache_ttl_seconds, int)
        assert result.history_file_path == "/tmp/history.csv"
        assert result.city_config_path == "/tmp/cities.json"

    def test_missing_env_vars_produce_safe_defaults(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="heatsafe.config"):
            result = load_config()

        assert result.open_meteo_url == config_module.DEFAULT_OPEN_METEO_URL
        assert result.max_retries == config_module.DEFAULT_MAX_RETRIES
        assert any("not set" in message for message in caplog.messages)

    def test_invalid_max_retries_falls_back_to_default_and_logs_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("MAX_RETRIES", "not-a-number")

        with caplog.at_level(logging.ERROR, logger="heatsafe.config"):
            result = load_config()

        assert result.max_retries == config_module.DEFAULT_MAX_RETRIES
        assert any("MAX_RETRIES" in message for message in caplog.messages)

    def test_invalid_base_backoff_seconds_falls_back_to_default_and_logs_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("BASE_BACKOFF_SECONDS", "fast")

        with caplog.at_level(logging.ERROR, logger="heatsafe.config"):
            result = load_config()

        assert result.base_backoff_seconds == config_module.DEFAULT_BASE_BACKOFF_SECONDS
        assert any("BASE_BACKOFF_SECONDS" in message for message in caplog.messages)

    def test_invalid_cache_ttl_seconds_falls_back_to_default_and_logs_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("CACHE_TTL_SECONDS", "")

        with caplog.at_level(logging.ERROR, logger="heatsafe.config"):
            result = load_config()

        assert result.cache_ttl_seconds == config_module.DEFAULT_CACHE_TTL_SECONDS
        assert any("CACHE_TTL_SECONDS" in message for message in caplog.messages)


# ---------------------------------------------------------------------------
# serialize_config / deserialize_config
# ---------------------------------------------------------------------------

class TestConfigSerialization:
    def test_serialize_config_round_trip(self) -> None:
        original = AppConfig(
            open_meteo_url="https://api.open-meteo.com/v1/forecast",
            max_retries=3,
            base_backoff_seconds=0.5,
            cache_ttl_seconds=300,
            history_file_path="./history.csv",
            city_config_path="",
        )

        json_str = json.dumps(serialize_config(original))
        restored = deserialize_config(json.loads(json_str))

        assert restored == original

    def test_deserialize_config_missing_field_raises(self) -> None:
        data = serialize_config(
            AppConfig(
                open_meteo_url="https://x.test",
                max_retries=3,
                base_backoff_seconds=0.5,
                cache_ttl_seconds=300,
                history_file_path="./history.csv",
                city_config_path="",
            )
        )
        del data["max_retries"]

        with pytest.raises(ValueError, match="max_retries"):
            deserialize_config(data)


# ---------------------------------------------------------------------------
# parse_city_config()
# ---------------------------------------------------------------------------

class TestParseCityConfig:
    def test_parses_valid_json(self) -> None:
        json_str = json.dumps(
            [
                {"key": "dubai", "name": "Dubai, UAE", "lat": 25.2048, "lon": 55.2708},
                {"key": "phoenix", "name": "Phoenix, USA", "lat": 33.4484, "lon": -112.0740},
            ]
        )

        result = parse_city_config(json_str)

        assert result == [
            CityConfig(key="dubai", name="Dubai, UAE", lat=25.2048, lon=55.2708),
            CityConfig(key="phoenix", name="Phoenix, USA", lat=33.4484, lon=-112.0740),
        ]

    def test_raises_value_error_on_malformed_json(self) -> None:
        with pytest.raises(ValueError):
            parse_city_config("{not valid json")

    def test_raises_value_error_when_top_level_is_not_a_list(self) -> None:
        with pytest.raises(ValueError):
            parse_city_config(json.dumps({"key": "dubai", "name": "Dubai, UAE", "lat": 25.2, "lon": 55.3}))

    def test_raises_value_error_on_missing_field(self) -> None:
        json_str = json.dumps([{"key": "dubai", "name": "Dubai, UAE", "lat": 25.2048}])

        with pytest.raises(ValueError, match="lon"):
            parse_city_config(json_str)

    def test_raises_value_error_on_invalid_field_type(self) -> None:
        json_str = json.dumps([{"key": "dubai", "name": "Dubai, UAE", "lat": "not-a-number", "lon": 55.2708}])

        with pytest.raises(ValueError):
            parse_city_config(json_str)

    def test_empty_list_returns_empty_result(self) -> None:
        assert parse_city_config("[]") == []


# ---------------------------------------------------------------------------
# serialize_advisory / deserialize_advisory
# ---------------------------------------------------------------------------

class TestAdvisorySerialization:
    def test_serialize_advisory_returns_json_serialisable_dict(self) -> None:
        advisory = _sample_advisory()

        data = serialize_advisory(advisory)
        # Must not raise: proves the dict is JSON-serialisable.
        json_str = json.dumps(data)

        assert json.loads(json_str) == data
        assert data["band"] == "Danger"
        assert data["micro_advisory"] == advisory.micro_advisory

    def test_round_trip_preserves_all_fields(self) -> None:
        advisory = _sample_advisory()

        result = deserialize_advisory(serialize_advisory(advisory))

        assert result == advisory

    def test_deserialize_advisory_raises_on_missing_field(self) -> None:
        data = serialize_advisory(_sample_advisory())
        del data["heat_index_c"]

        with pytest.raises(ValueError, match="heat_index_c"):
            deserialize_advisory(data)

    def test_deserialize_advisory_raises_on_invalid_field_type(self) -> None:
        data = serialize_advisory(_sample_advisory())
        data["temperature_c"] = "not-a-number"

        with pytest.raises(ValueError):
            deserialize_advisory(data)


# ---------------------------------------------------------------------------
# CSV formatting
# ---------------------------------------------------------------------------

class TestCsvFormatting:
    def test_advisory_to_csv_row_column_order(self) -> None:
        advisory = _sample_advisory()

        row = advisory_to_csv_row(advisory, timestamp="2026-09-20T12:00:00")
        columns = row.split(",")

        assert columns[0] == "2026-09-20T12:00:00"
        assert columns[1] == ""  # latitude unavailable in this helper
        assert columns[2] == ""  # longitude unavailable in this helper
        assert columns[3] == ""  # location_name unavailable in this helper
        assert columns[4] == "35.0"
        assert columns[5] == "60.0"
        assert columns[6] == "45.1"
        assert columns[7] == "Extreme"
        assert columns[8] == "Danger"

    def test_advisories_to_csv_empty_input_returns_header_only(self) -> None:
        result = advisories_to_csv([])

        assert result == ",".join(config_module.CSV_HEADER) + "\n"

    def test_advisories_to_csv_includes_header_and_rows(self) -> None:
        records = [
            HistoryRecord(
                timestamp="2026-09-20T12:00:00",
                latitude=33.4484,
                longitude=-112.0740,
                location_name="Phoenix, USA",
                temperature_c=38.3,
                relative_humidity=19.0,
                heat_index_c=36.8,
                risk_level="High",
                band="Extreme Caution",
            )
        ]

        result = advisories_to_csv(records)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)

        assert rows[0] == list(config_module.CSV_HEADER)
        assert rows[1] == [
            "2026-09-20T12:00:00",
            "33.4484",
            "-112.074",
            "Phoenix, USA",
            "38.3",
            "19.0",
            "36.8",
            "High",
            "Extreme Caution",
        ]

    def test_advisories_to_csv_is_utf8_encodable(self) -> None:
        records = [
            HistoryRecord(
                timestamp="2026-09-20T12:00:00",
                latitude=25.2048,
                longitude=55.2708,
                location_name="Dubai, UAE",
                temperature_c=40.0,
                relative_humidity=55.0,
                heat_index_c=48.2,
                risk_level="Extreme",
                band="Danger",
            )
        ]

        result = advisories_to_csv(records)

        # Must not raise UnicodeEncodeError.
        encoded = result.encode("utf-8")
        assert encoded.decode("utf-8") == result
