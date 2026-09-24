"""
tests/test_integration.py

End-to-end integration tests for the HeatSafe FastAPI application using
Starlette's TestClient.

Network calls to Open-Meteo are mocked (no real HTTP traffic), and history
file I/O is redirected to a temporary path via `HISTORY_FILE_PATH` so these
tests never touch the real project directory.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.testclient import TestClient

import main
from services import weather as weather_service
from services.config import CSV_HEADER


def _fake_response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    request = httpx.Request("GET", weather_service.OPEN_METEO_URL)
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


@pytest.fixture(autouse=True)
def _isolated_history_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all history file I/O to a temp file so tests never touch the real project directory."""
    path = tmp_path / "history.csv"
    monkeypatch.setenv("HISTORY_FILE_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def _clear_weather_cache() -> Iterator[None]:
    weather_service._last_good_cache.clear()
    yield
    weather_service._last_good_cache.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_returns_200_with_healthy_status(self, client: TestClient) -> None:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


# ---------------------------------------------------------------------------
# GET /api/advisory
# ---------------------------------------------------------------------------

class TestAdvisoryEndpoint:
    def test_valid_city_returns_full_schema(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        response = client.get("/api/advisory?city=phoenix")

        assert response.status_code == 200
        data = response.json()
        for key in ("location", "current", "hourly", "cities", "role", "stale_data"):
            assert key in data

        assert data["role"] == "general_public"
        assert data["location"]["name"] == "Phoenix, USA"
        assert len(data["hourly"]) == 24
        assert data["current"]["temperature_c"] == 38.3

    def test_advisory_by_exact_coordinates(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload(latitude=25.2048, longitude=55.2708)
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        response = client.get("/api/advisory?lat=25.2048&lon=55.2708")

        assert response.status_code == 200
        data = response.json()
        assert data["location"]["latitude"] == pytest.approx(25.2048, abs=1e-3)
        assert data["location"]["longitude"] == pytest.approx(55.2708, abs=1e-3)
        assert "Current Location" in data["location"]["name"]

    def test_advisory_multilingual_arabic(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        response = client.get("/api/advisory?city=dubai&lang=ar")

        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "ar"
        assert "ai_advisory" in data
        assert isinstance(data["ai_advisory"], list)
        assert len(data["ai_advisory"]) == 3
        for bullet in data["ai_advisory"]:
            assert isinstance(bullet, str)
            assert len(bullet) > 0

    def test_role_query_param_is_honored(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        response = client.get("/api/advisory?city=phoenix&role=outdoor_worker")

        assert response.status_code == 200
        assert response.json()["role"] == "outdoor_worker"

    def test_persona_param_used_when_role_absent(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        response = client.get("/api/advisory?city=phoenix&persona=outdoor_worker")

        assert response.status_code == 200
        assert response.json()["role"] == "outdoor_worker"

    def test_role_takes_priority_over_persona(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        response = client.get("/api/advisory?city=phoenix&role=outdoor_worker&persona=general_public")

        assert response.json()["role"] == "outdoor_worker"

    def test_invalid_role_defaults_to_general_public(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        response = client.get("/api/advisory?city=phoenix&role=not_a_real_role")

        assert response.status_code == 200
        assert response.json()["role"] == "general_public"

    def test_unknown_city_returns_404(self, client: TestClient) -> None:
        response = client.get("/api/advisory?city=unknown_city_xyz")
        assert response.status_code == 404

    def test_weather_service_failure_returns_503(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _always_fail(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated total weather outage")

        monkeypatch.setattr(main.weather_service, "fetch_weather", _always_fail)

        response = client.get("/api/advisory?city=phoenix")

        assert response.status_code == 503

    def test_advisory_appends_history_record(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, _isolated_history_path: Path
    ) -> None:
        payload = _build_payload()
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        response = client.get("/api/advisory?city=phoenix")

        assert response.status_code == 200
        assert _isolated_history_path.exists()

        with _isolated_history_path.open(encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows[0] == list(CSV_HEADER)
        assert len(rows) == 2  # header + one appended record

    def test_history_write_failure_does_not_break_response(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even if history logging fails outright, the advisory response must still succeed."""
        payload = _build_payload()
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        def _broken_append(*args: Any, **kwargs: Any) -> None:
            raise OSError("simulated disk failure")

        monkeypatch.setattr(main.history_service, "append_record", _broken_append)

        response = client.get("/api/advisory?city=phoenix")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestIndexPage:
    def test_returns_html_with_city_selector(self, client: TestClient) -> None:
        response = client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "city-select" in response.text
        assert "Phoenix, USA" in response.text
        assert 'value="dubai"' in response.text
        assert "near-me-btn" in response.text
        assert "Near Me" in response.text
        assert "lang-select" in response.text
        assert "listen-btn" in response.text
        assert "Amazon Bedrock" in response.text


# ---------------------------------------------------------------------------
# GET /api/history/export
# ---------------------------------------------------------------------------

class TestHistoryExport:
    def test_returns_csv_with_valid_headers(self, client: TestClient) -> None:
        response = client.get("/api/history/export")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert 'attachment; filename="heatsafe_history.csv"' in response.headers["content-disposition"]

        reader = csv.reader(io.StringIO(response.text))
        header_row = next(reader)
        assert header_row == list(CSV_HEADER)

    def test_includes_previously_appended_records(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _build_payload()
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=_fake_response(payload)))

        advisory_response = client.get("/api/advisory?city=phoenix")
        assert advisory_response.status_code == 200

        export_response = client.get("/api/history/export")
        lines = export_response.text.strip("\n").split("\n")

        assert lines[0] == ",".join(CSV_HEADER)
        assert len(lines) >= 2  # header + at least one appended record
