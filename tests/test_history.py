"""
tests/test_history.py

Unit tests for services/history.py: append_record, read_records,
compute_trend, export_csv, and prune_old_records.

All file operations are isolated from the real filesystem by pointing the
`HISTORY_FILE_PATH` environment variable at pytest's `tmp_path` fixture.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from services import history
from services.config import CSV_HEADER, HistoryRecord


def _set_history_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("HISTORY_FILE_PATH", str(path))


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _make_record(timestamp: str, heat_index_c: float = 30.0, **overrides: Any) -> HistoryRecord:
    defaults: dict[str, Any] = {
        "timestamp": timestamp,
        "latitude": 33.4484,
        "longitude": -112.0740,
        "location_name": "Phoenix, USA",
        "temperature_c": 32.0,
        "relative_humidity": 20.0,
        "heat_index_c": heat_index_c,
        "risk_level": "High",
        "band": "Extreme Caution",
    }
    defaults.update(overrides)
    return HistoryRecord(**defaults)


# ---------------------------------------------------------------------------
# append_record
# ---------------------------------------------------------------------------

class TestAppendRecord:
    def test_creates_file_with_header_when_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "history.csv"
        _set_history_path(monkeypatch, path)

        history.append_record(_make_record(_iso(0)))

        assert path.exists()
        with path.open(encoding="utf-8") as f:
            rows = list(csv.reader(f))

        assert rows[0] == list(CSV_HEADER)
        assert len(rows) == 2

    def test_appends_without_duplicating_header(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "history.csv"
        _set_history_path(monkeypatch, path)

        history.append_record(_make_record(_iso(2)))
        history.append_record(_make_record(_iso(1)))

        with path.open(encoding="utf-8") as f:
            rows = list(csv.reader(f))

        assert rows[0] == list(CSV_HEADER)
        assert len(rows) == 3  # header + 2 data rows

    def test_write_failure_is_caught_and_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Parent directory does not exist -> open() raises OSError.
        bad_path = tmp_path / "nonexistent_dir" / "history.csv"
        _set_history_path(monkeypatch, bad_path)

        with caplog.at_level(logging.ERROR, logger="heatsafe.history"):
            history.append_record(_make_record(_iso(0)))  # must not raise

        assert not bad_path.exists()
        assert any("Failed to append" in message for message in caplog.messages)


# ---------------------------------------------------------------------------
# read_records
# ---------------------------------------------------------------------------

class TestReadRecords:
    def test_returns_empty_list_when_file_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "missing.csv"
        _set_history_path(monkeypatch, path)

        with caplog.at_level(logging.WARNING, logger="heatsafe.history"):
            result = history.read_records(days=7)

        assert result == []
        assert any("not found" in message for message in caplog.messages)

    def test_filters_records_within_time_window(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "history.csv"
        _set_history_path(monkeypatch, path)

        history.append_record(_make_record(_iso(10)))  # outside 7-day window
        history.append_record(_make_record(_iso(3)))   # inside window
        history.append_record(_make_record(_iso(0)))   # inside window

        result = history.read_records(days=7)

        assert len(result) == 2
        assert all(isinstance(r, HistoryRecord) for r in result)

    def test_skips_malformed_rows_and_logs_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "history.csv"
        _set_history_path(monkeypatch, path)
        path.write_text(
            ",".join(CSV_HEADER) + "\n"
            "not-a-timestamp,33.0,-112.0,Phoenix,32.0,20.0,30.0,High,Extreme Caution\n"
            f"{_iso(0)},33.0,-112.0,Phoenix,32.0,20.0,30.0,High,Extreme Caution\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="heatsafe.history"):
            result = history.read_records(days=7)

        assert len(result) == 1
        assert any("Skipping malformed" in message for message in caplog.messages)


# ---------------------------------------------------------------------------
# compute_trend
# ---------------------------------------------------------------------------

class TestComputeTrend:
    def test_empty_list_is_stable(self) -> None:
        result = history.compute_trend([])
        assert result.direction == "stable"
        assert result.description

    def test_single_record_is_stable(self) -> None:
        result = history.compute_trend([_make_record(_iso(0))])
        assert result.direction == "stable"

    def test_increasing_trend_detected(self) -> None:
        records = [
            _make_record(_iso(3), heat_index_c=28.0),
            _make_record(_iso(2), heat_index_c=29.0),
            _make_record(_iso(1), heat_index_c=34.0),
            _make_record(_iso(0), heat_index_c=35.0),
        ]

        result = history.compute_trend(records)

        assert result.direction == "increasing"
        assert result.description

    def test_decreasing_trend_detected(self) -> None:
        records = [
            _make_record(_iso(3), heat_index_c=38.0),
            _make_record(_iso(2), heat_index_c=37.0),
            _make_record(_iso(1), heat_index_c=30.0),
            _make_record(_iso(0), heat_index_c=29.0),
        ]

        result = history.compute_trend(records)

        assert result.direction == "decreasing"
        assert result.description

    def test_stable_trend_within_threshold(self) -> None:
        records = [
            _make_record(_iso(3), heat_index_c=30.0),
            _make_record(_iso(2), heat_index_c=30.4),
            _make_record(_iso(1), heat_index_c=30.2),
            _make_record(_iso(0), heat_index_c=30.6),
        ]

        result = history.compute_trend(records)

        assert result.direction == "stable"

    def test_custom_threshold_is_respected(self) -> None:
        records = [
            _make_record(_iso(1), heat_index_c=30.0),
            _make_record(_iso(0), heat_index_c=31.5),
        ]

        # Default threshold (1.0) would call this "increasing" (delta=1.5);
        # a wider threshold should call it "stable".
        assert history.compute_trend(records).direction == "increasing"
        assert history.compute_trend(records, threshold_c=2.0).direction == "stable"


# ---------------------------------------------------------------------------
# export_csv
# ---------------------------------------------------------------------------

class TestExportCsv:
    def test_empty_records_returns_header_only(self) -> None:
        result = history.export_csv([])
        assert result == ",".join(CSV_HEADER) + "\n"

    def test_records_are_formatted_correctly(self) -> None:
        record = _make_record(_iso(0))

        result = history.export_csv([record])
        lines = result.strip("\n").split("\n")

        assert lines[0] == ",".join(CSV_HEADER)
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# prune_old_records
# ---------------------------------------------------------------------------

class TestPruneOldRecords:
    def test_returns_zero_when_file_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "missing.csv"
        _set_history_path(monkeypatch, path)

        assert history.prune_old_records(days=30) == 0

    def test_removes_old_rows_and_returns_count(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "history.csv"
        _set_history_path(monkeypatch, path)

        history.append_record(_make_record(_iso(40)))
        history.append_record(_make_record(_iso(35)))
        history.append_record(_make_record(_iso(10)))
        history.append_record(_make_record(_iso(1)))

        removed = history.prune_old_records(days=30)

        assert removed == 2
        remaining = history.read_records(days=365)
        assert len(remaining) == 2

    def test_no_rows_removed_when_all_within_window(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "history.csv"
        _set_history_path(monkeypatch, path)

        history.append_record(_make_record(_iso(1)))
        history.append_record(_make_record(_iso(2)))

        removed = history.prune_old_records(days=30)

        assert removed == 0

    def test_pruned_file_still_has_valid_header(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "history.csv"
        _set_history_path(monkeypatch, path)

        history.append_record(_make_record(_iso(40)))
        history.append_record(_make_record(_iso(1)))

        history.prune_old_records(days=30)

        with path.open(encoding="utf-8") as f:
            rows = list(csv.reader(f))

        assert rows[0] == list(CSV_HEADER)
        assert len(rows) == 2  # header + 1 remaining row
