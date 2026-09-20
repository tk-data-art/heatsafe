# Implementation Plan: HeatSafe — Hyperlocal Climate & Heat-Stress Advisory Engine

## Overview

The core heat engine (`services/heat_index.py`), weather client (`services/weather.py`), FastAPI application (`main.py`), and dashboard template (`templates/index.html`) are already implemented. The remaining work centres on four gaps:

1. **`services/config.py`** — configuration parser and data serialiser (Requirement 9, entirely missing)
2. **`services/history.py`** — CSV-backed historical data store (Requirement 6, entirely missing)
3. **`main.py` wiring** — integrate `config.py` and `history.py`, add `/api/history/export` endpoint, and make env-var configuration take effect
4. **`tests/`** — full unit, integration, and property-based test suite (missing; `hypothesis` must be added to `requirements.txt`)

All implementation is in Python. Property-based tests use [`hypothesis`](https://hypothesis.readthedocs.io/).

---

## Tasks

- [ ] 1. Add `hypothesis` to requirements and create the `tests/` package
  - Append `hypothesis` and `pytest-asyncio` to `requirements.txt`
  - Create `tests/__init__.py` (empty) and `tests/conftest.py` with a shared `anyio_backend` fixture
  - Create `pytest.ini` (or `pyproject.toml` `[tool.pytest.ini_options]`) configuring `asyncio_mode = auto`
  - _Requirements: 7.3, 8.2_

- [ ] 2. Implement `services/config.py` — configuration parser and data serialiser
  - [ ] 2.1 Define `AppConfig` and `CityConfig` dataclasses and implement `load_config()`
    - Read each env var listed in the design (`OPEN_METEO_URL`, `MAX_RETRIES`, `BASE_BACKOFF_SECONDS`, `CACHE_TTL_SECONDS`, `HISTORY_FILE_PATH`, `CITY_CONFIG_PATH`)
    - On missing variable: use hard-coded safe default and emit `logging.INFO`
    - On wrong type (e.g. `MAX_RETRIES="abc"`): log a descriptive error and use safe default
    - _Requirements: 9.1, 9.2, 9.4_

  - [ ] 2.2 Implement `parse_city_config(json_str: str) → list[CityConfig]`
    - Parse the JSON array format `[{"key":…, "name":…, "lat":…, "lon":…}]`
    - Raise `ValueError` with a descriptive message on malformed JSON
    - _Requirements: 9.3, 9.9_

  - [ ] 2.3 Implement `serialize_advisory(advisory: HeatAdvisory) → dict` and `deserialize_advisory(data: dict) → HeatAdvisory`
    - `serialize_advisory` must return a JSON-serialisable `dict` (all fields preserved)
    - `deserialize_advisory` must raise `ValueError` identifying any missing required field
    - Use UTF-8 compatible types only
    - _Requirements: 9.5, 9.8, 9.9, 9.10_

  - [ ]* 2.4 Write property test for advisory serialisation round-trip (Property 5)
    - **Property 5: Advisory data serialisation round-trip**
    - Build a Hypothesis `@composite` strategy `heat_advisory_strategy()` that generates valid `HeatAdvisory` objects
    - Assert `deserialize_advisory(serialize_advisory(adv)) == adv` for all generated instances
    - Annotate with `@settings(max_examples=100)`
    - **Validates: Requirements 9.5, 9.8**

  - [ ] 2.5 Implement `advisory_to_csv_row(advisory: HeatAdvisory, timestamp: str) → str` and `advisories_to_csv(records: list[HistoryRecord]) → str`
    - Column order matches the CSV header defined in the design: `timestamp,latitude,longitude,location_name,temperature_c,relative_humidity,heat_index_c,risk_level,band`
    - `advisories_to_csv` always emits the header row first; returns header-only string for empty input
    - All output is UTF-8 encoded strings
    - _Requirements: 9.6, 9.10_

  - [ ]* 2.6 Write property test for config serialisation round-trip (Property 6)
    - **Property 6: Configuration serialisation round-trip**
    - Build a Hypothesis `@composite` strategy `app_config_strategy()` covering all `AppConfig` fields
    - Serialise config to JSON and parse back; assert all fields are equal
    - **Validates: Requirements 9.7**

  - [ ]* 2.7 Write property test for city config parse round-trip (Property 7)
    - **Property 7: City config parse round-trip**
    - Generate lists of `CityConfig` objects via Hypothesis, serialise to JSON string, parse via `parse_city_config`, and assert equivalence
    - **Validates: Requirements 9.3, 9.7**

- [ ] 3. Write unit tests for `services/config.py`
  - [ ] 3.1 Write unit tests covering `load_config()` and `parse_city_config()`
    - Valid env vars are parsed to correct Python types
    - Missing env vars produce safe defaults
    - Invalid env var values produce safe defaults and log a descriptive error
    - `parse_city_config` parses valid JSON correctly
    - `parse_city_config` raises `ValueError` on malformed JSON
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.9_

  - [ ]* 3.2 Write unit tests covering serialisation helpers
    - `advisory_to_csv_row` produces correct column order
    - `advisories_to_csv` includes header row; empty input returns header only
    - `deserialize_advisory` raises `ValueError` on missing field
    - _Requirements: 9.5, 9.6, 9.8, 9.9_

- [ ] 4. Implement `services/history.py` — historical data store
  - [ ] 4.1 Implement `append_record(record: HistoryRecord) → None`
    - Append a single CSV row to the file at `config.history_file_path`; create file with header if it does not exist
    - On write failure: log error and return without raising (request must not crash)
    - _Requirements: 6.1, 8.5_

  - [ ] 4.2 Implement `read_records(days: int) → list[HistoryRecord]`
    - Read the CSV file and return only records whose `timestamp` falls within the last `days` days
    - On read failure or missing file: return empty list and log a warning
    - _Requirements: 6.1, 6.3_

  - [ ] 4.3 Implement `compute_trend(records: list[HistoryRecord]) → TrendSummary`
    - Define `TrendSummary` dataclass with a `direction` field (`"increasing"`, `"decreasing"`, `"stable"`) and a `description` string
    - Compare the average heat index of the first half of records vs. the second half; use a configurable tolerance threshold (default 1°C)
    - Return `"stable"` for an empty or single-record input
    - _Requirements: 6.2_

  - [ ] 4.4 Implement `export_csv(records: list[HistoryRecord]) → str` and `prune_old_records(days: int) → int`
    - `export_csv` delegates to `config.advisories_to_csv`; returns header-only string for empty input
    - `prune_old_records` removes rows older than `days` days in-place (rewrite file), returns count of deleted rows
    - _Requirements: 6.4, 6.1_

  - [ ]* 4.5 Write unit tests for `services/history.py`
    - `append_record` writes a row to a temp CSV file (use `tmp_path` pytest fixture)
    - `read_records` returns only records within the specified time window
    - `export_csv` produces a valid CSV string with the correct header
    - `prune_old_records` deletes rows older than N days and returns the correct count
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [ ] 5. Checkpoint — core services complete
  - Ensure all tests written so far pass (`pytest tests/`). Ask the user if questions arise.

- [ ] 6. Implement existing `services/heat_index.py` unit and property tests
  - [ ] 6.1 Write unit tests for `services/heat_index.py` in `tests/test_heat_index.py`
    - Known NOAA reference values (at least three spot-checks of the Rothfusz polynomial)
    - Boundary values exactly at 80°F, 91°F, 103°F heat index thresholds
    - Low-humidity adjustment applied (RH < 13, temp 80–112°F)
    - High-humidity adjustment applied (RH > 85, temp 80–87°F)
    - Temperature conversion round-trips (C → F → C for representative values)
    - `get_micro_advisory` returns a non-empty list for all valid `(band, role)` combinations
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.7_

  - [ ]* 6.2 Write property test for heat index classification consistency (Property 1)
    - **Property 1: Heat index classification is consistent with thresholds**
    - `@given(temp_c=floats(-20, 60), rh=floats(0, 100))`
    - Compute `hi_f = calculate_heat_index_f(celsius_to_fahrenheit(temp_c), rh)`
    - Assert `classify_heat_index(hi_f).risk_level` matches the expected band per the threshold table
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.5**

  - [ ]* 6.3 Write property test for heat index determinism (Property 2)
    - **Property 2: Heat index calculation is deterministic**
    - `@given(temp_f=floats(0, 150), rh=floats(0, 100))`
    - Assert `calculate_heat_index_f(temp_f, rh) == calculate_heat_index_f(temp_f, rh)`
    - **Validates: Requirements 2.6**

  - [ ]* 6.4 Write property test for OSHA work/rest ratio matching classification (Property 3)
    - **Property 3: OSHA work/rest ratio matches classification**
    - `@given(temp_c=floats(-20, 60), rh=floats(0, 100))`
    - Assert `build_advisory(temp_c, rh, role).work_rest_ratio` contains the prescribed string for the resulting `risk_level`
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

  - [ ]* 6.5 Write property test for non-empty advisory (Property 4)
    - **Property 4: Role-specific advisory is non-empty for all valid inputs**
    - `@given(temp_c=floats(-20, 60), rh=floats(0, 100), role=sampled_from(["outdoor_worker", "general_public"]))`
    - Assert `len(build_advisory(temp_c, rh, role).micro_advisory) >= 1`
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**

- [ ] 7. Write unit tests for `services/weather.py` in `tests/test_weather.py`
  - [ ] 7.1 Write example-based unit tests for `WeatherClient`
    - Successful fetch parses `WeatherSnapshot` correctly (mock `httpx` response with realistic JSON)
    - Retry logic: mock fails N times, succeeds on attempt N+1; assert `snapshot.stale == False`
    - Cache fallback: mock always fails; assert returned snapshot has `stale == True`
    - `get_city_coordinates` returns correct values for all preset keys
    - `get_city_coordinates` returns `None` for an unknown key
    - Hourly slice is exactly 24 points starting from the current forecast hour
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 7.2 Write property test for coordinate passthrough (Property 8)
    - **Property 8: Coordinate passthrough to API request**
    - `@given(lat=floats(-90, 90), lon=floats(-180, 180))`
    - Mock `httpx.AsyncClient.get` to capture `params`; assert `params["latitude"] == lat` and `params["longitude"] == lon`
    - **Validates: Requirements 1.5**

- [ ] 8. Wire `services/config.py` and `services/history.py` into `main.py`
  - [ ] 8.1 Call `load_config()` at startup and inject `AppConfig` into `WeatherClient` and the history store
    - Replace hardcoded `OPEN_METEO_URL`, `MAX_RETRIES`, `BASE_BACKOFF_SECONDS` constants in `weather.py` with values read from `AppConfig` (passed as parameters or a module-level singleton loaded once)
    - _Requirements: 9.1, 9.4_

  - [ ] 8.2 Add `append_record()` call inside `GET /api/advisory` after a successful advisory is built
    - Build a `HistoryRecord` from location + `current_advisory` and call `history.append_record(record)`
    - Failure to write must not affect the advisory response
    - _Requirements: 6.1, 8.5_

  - [ ] 8.3 Implement `GET /api/history/export` endpoint
    - Call `history.read_records(days=30)` and `history.export_csv(records)`
    - Return a `Response` with `media_type="text/csv"` and a `Content-Disposition: attachment; filename="heatsafe_history.csv"` header
    - _Requirements: 6.4_

- [ ] 9. Extend the dashboard (`templates/index.html`) with historical comparison
  - [ ] 9.1 Add a "vs. yesterday" comparison line in the Risk Banner section
    - Fetch `/api/history/export` data client-side (or expose a `/api/history/summary` JSON endpoint) to display yesterday's heat index at the same hour
    - Display as a subtle line under the heat index value: `"Yesterday at this hour: X°C"`
    - Show nothing if no historical data is available
    - _Requirements: 6.3, 5.1, 5.6_

- [ ] 10. Write integration tests in `tests/test_integration.py`
  - [ ]* 10.1 Write integration tests using FastAPI `TestClient`
    - `GET /api/health` returns 200 with `{"status": "healthy"}`
    - `GET /api/advisory?city=phoenix` returns a valid JSON response matching the full schema
    - `GET /api/advisory?city=unknown_xyz` returns 404
    - `GET /api/advisory` when weather service is mocked to fail returns 503
    - `GET /` returns HTML 200 with the city selector populated
    - `GET /api/history/export` returns 200 with `Content-Type: text/csv`
    - Full advisory response contains all required top-level keys: `location`, `current`, `hourly`, `cities`, `role`, `stale_data`
    - _Requirements: 1.2, 1.3, 5.1, 7.1, 7.4, 7.5, 8.4_

- [ ] 11. Final checkpoint — all tests pass
  - Run `pytest tests/ -v` and ensure the full suite (unit + property + integration) passes with zero failures.
  - Verify the `Dockerfile` still builds successfully (`docker build .`).
  - Ask the user if any questions arise before closing out the implementation.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP. All non-starred tasks are required.
- Property-based tests (Properties 1–8) are optional sub-tasks; implement them after the corresponding unit tests pass.
- Each task references specific requirements for traceability.
- Checkpoints (tasks 5 and 11) validate incremental progress.
- The `hypothesis` library must be added to `requirements.txt` before any property tests can run.
- `pytest-asyncio` is required for the async Property 8 test (`test_property_8_coordinate_passthrough`).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.2"] },
    { "id": 2, "tasks": ["2.3", "2.5"] },
    { "id": 3, "tasks": ["2.4", "2.6", "2.7", "3.1"] },
    { "id": 4, "tasks": ["3.2", "4.1", "4.2"] },
    { "id": 5, "tasks": ["4.3", "4.4", "6.1", "7.1"] },
    { "id": 6, "tasks": ["4.5", "6.2", "6.3", "6.4", "6.5", "7.2"] },
    { "id": 7, "tasks": ["8.1"] },
    { "id": 8, "tasks": ["8.2", "8.3"] },
    { "id": 9, "tasks": ["9.1"] },
    { "id": 10, "tasks": ["10.1"] }
  ]
}
```
