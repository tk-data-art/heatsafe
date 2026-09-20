"""
history.py

Thin CSV-backed persistence layer for historical heat-advisory records
(design.md, Component 5 / Requirement 6).

Storage note: `history.csv` is written to local/container ephemeral
storage. Data does not persist across container restarts in the MVP.
Production roadmap: replace file I/O with Amazon S3 `PutObject`/`ListObjectsV2`
calls against a `heatsafe-audit-logs` bucket.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.config import CSV_HEADER, HistoryRecord, advisories_to_csv, load_config

logger = logging.getLogger("heatsafe.history")

DEFAULT_TREND_THRESHOLD_C = 1.0


@dataclass
class TrendSummary:
    direction: str  # "increasing" | "decreasing" | "stable"
    description: str


def _history_file_path() -> str:
    """Resolve the current history file path from configuration (re-read each call)."""
    return load_config().history_file_path


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp string, treating naive timestamps as UTC."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _record_from_row(row: dict[str, str]) -> HistoryRecord:
    """Convert a raw CSV DictReader row into a HistoryRecord. Raises KeyError/ValueError on bad data."""
    return HistoryRecord(
        timestamp=row["timestamp"],
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        location_name=row["location_name"],
        temperature_c=float(row["temperature_c"]),
        relative_humidity=float(row["relative_humidity"]),
        heat_index_c=float(row["heat_index_c"]),
        risk_level=row["risk_level"],
        band=row["band"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def append_record(record: HistoryRecord) -> None:
    """
    Append a single HistoryRecord as a CSV row to the configured history
    file, creating the file with a header row if it does not yet exist.

    Never raises: any I/O failure is logged as an error and swallowed so
    the calling request is unaffected.
    """
    path = _history_file_path()

    try:
        file_exists = os.path.exists(path) and os.path.getsize(path) > 0
        with open(path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(CSV_HEADER)
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
    except (OSError, csv.Error) as exc:
        logger.error("Failed to append history record to %s: %s", path, exc)


def read_records(days: int) -> list[HistoryRecord]:
    """
    Read the history CSV file and return only records whose timestamp
    falls within the last `days` days.

    On a missing file or any read/parse failure, logs a warning and
    returns an empty list rather than raising.
    """
    path = _history_file_path()

    if not os.path.exists(path):
        logger.warning("History file not found at %s; returning empty record list", path)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    records: list[HistoryRecord] = []

    try:
        with open(path, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    timestamp = _parse_timestamp(row["timestamp"])
                except (KeyError, ValueError) as exc:
                    logger.warning("Skipping malformed history row in %s: %s", path, exc)
                    continue

                if timestamp < cutoff:
                    continue

                try:
                    records.append(_record_from_row(row))
                except (KeyError, ValueError) as exc:
                    logger.warning("Skipping malformed history row in %s: %s", path, exc)
                    continue
    except (OSError, csv.Error) as exc:
        logger.warning("Failed to read history file %s: %s", path, exc)
        return []

    return records


def compute_trend(records: list[HistoryRecord], threshold_c: float = DEFAULT_TREND_THRESHOLD_C) -> TrendSummary:
    """
    Compare the average heat index of the first half of `records` against
    the second half (chronologically ordered) and classify the trend.

    Returns "stable" for an empty or single-record input. `threshold_c`
    is the minimum average delta (°C) required to call a trend
    increasing/decreasing rather than stable.
    """
    if len(records) < 2:
        return TrendSummary(
            direction="stable",
            description="Not enough historical data to determine a trend.",
        )

    ordered = sorted(records, key=lambda r: _parse_timestamp(r.timestamp))
    midpoint = len(ordered) // 2
    first_half = ordered[:midpoint]
    second_half = ordered[midpoint:]

    first_avg = sum(r.heat_index_c for r in first_half) / len(first_half)
    second_avg = sum(r.heat_index_c for r in second_half) / len(second_half)
    delta = second_avg - first_avg

    if delta > threshold_c:
        return TrendSummary(
            direction="increasing",
            description=f"Heat index is trending upward (+{delta:.1f}\u00b0C vs. the earlier period).",
        )
    if delta < -threshold_c:
        return TrendSummary(
            direction="decreasing",
            description=f"Heat index is trending downward ({delta:.1f}\u00b0C vs. the earlier period).",
        )
    return TrendSummary(
        direction="stable",
        description=f"Heat index has remained stable ({delta:+.1f}\u00b0C vs. the earlier period).",
    )


def export_csv(records: list[HistoryRecord]) -> str:
    """Format records as a full CSV string with headers; header-only for empty input."""
    return advisories_to_csv(records)


def prune_old_records(days: int) -> int:
    """
    Remove rows older than `days` days from the history file, rewriting it
    in place. Returns the number of rows removed, or 0 if the file does
    not exist or cannot be read.
    """
    path = _history_file_path()

    if not os.path.exists(path):
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        with open(path, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or list(CSV_HEADER)
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        logger.warning("Failed to read history file %s for pruning: %s", path, exc)
        return 0

    kept_rows: list[dict[str, str]] = []
    removed_count = 0

    for row in rows:
        try:
            timestamp = _parse_timestamp(row["timestamp"])
        except (KeyError, ValueError) as exc:
            logger.warning("Keeping unparsable history row during prune of %s: %s", path, exc)
            kept_rows.append(row)
            continue

        if timestamp < cutoff:
            removed_count += 1
        else:
            kept_rows.append(row)

    try:
        with open(path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(kept_rows)
    except (OSError, csv.Error) as exc:
        logger.error("Failed to write pruned history file %s: %s", path, exc)
        return 0

    return removed_count
