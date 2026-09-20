"""
tests/test_heat_index.py

Unit (example-based) tests for services/heat_index.py:
- NOAA Rothfusz reference-value spot checks
- Risk-band threshold boundaries
- Low-humidity and high-humidity correction terms
- Celsius <-> Fahrenheit round-trip conversions
- Role-specific micro-advisory and OSHA work/rest ratio coverage
"""

from __future__ import annotations

import pytest

from services.heat_index import (
    Role,
    build_advisory,
    calculate_heat_index_f,
    celsius_to_fahrenheit,
    classify_heat_index,
    fahrenheit_to_celsius,
    get_micro_advisory,
)

ALL_BANDS = ["Normal", "Caution", "Extreme Caution", "Danger", "Extreme Danger"]
ALL_ROLES: list[Role] = ["outdoor_worker", "general_public"]


# ---------------------------------------------------------------------------
# NOAA Rothfusz reference-value benchmarks
# ---------------------------------------------------------------------------

class TestNoaaBenchmarks:
    """Spot-check the Rothfusz polynomial against published NOAA reference values."""

    @pytest.mark.parametrize(
        "temp_f, rh, expected_hi_f, tolerance",
        [
            (80.0, 75.0, 84.0, 1.0),
            (90.0, 65.0, 103.0, 1.0),
            (100.0, 55.0, 124.0, 1.0),
            (76.0, 50.0, 76.0, 1.0),
        ],
    )
    def test_reference_values_within_tolerance(
        self, temp_f: float, rh: float, expected_hi_f: float, tolerance: float
    ) -> None:
        result = calculate_heat_index_f(temp_f, rh)
        assert abs(result - expected_hi_f) <= tolerance

    def test_deterministic_exact_values(self) -> None:
        """Exact values produced by our implementation, pinned to catch regressions."""
        assert calculate_heat_index_f(80.0, 75.0) == 83.6
        assert calculate_heat_index_f(90.0, 65.0) == 102.7
        assert calculate_heat_index_f(100.0, 55.0) == 123.6


# ---------------------------------------------------------------------------
# Risk-band threshold boundaries
# ---------------------------------------------------------------------------

class TestThresholdBoundaries:
    def test_just_below_80f_is_low_risk(self) -> None:
        band = classify_heat_index(79.9)
        assert band.risk_level == "Low"
        assert band.band == "Normal"

    def test_at_80f_is_moderate_risk(self) -> None:
        band = classify_heat_index(80.0)
        assert band.risk_level == "Moderate"
        assert band.band == "Caution"

    def test_just_below_91f_is_moderate_risk(self) -> None:
        band = classify_heat_index(90.9)
        assert band.risk_level == "Moderate"

    def test_at_91f_is_high_risk(self) -> None:
        band = classify_heat_index(91.0)
        assert band.risk_level == "High"
        assert band.band == "Extreme Caution"

    def test_just_below_103f_is_high_risk(self) -> None:
        band = classify_heat_index(102.9)
        assert band.risk_level == "High"

    def test_at_103f_is_extreme_risk_danger_band(self) -> None:
        band = classify_heat_index(103.0)
        assert band.risk_level == "Extreme"
        assert band.band == "Danger"

    def test_just_below_125f_is_danger_band(self) -> None:
        band = classify_heat_index(124.9)
        assert band.band == "Danger"
        assert band.risk_level == "Extreme"

    def test_at_125f_is_extreme_danger_band(self) -> None:
        band = classify_heat_index(125.0)
        assert band.band == "Extreme Danger"
        assert band.risk_level == "Extreme"


# ---------------------------------------------------------------------------
# Humidity correction terms
# ---------------------------------------------------------------------------

class TestHumidityAdjustments:
    def test_low_humidity_adjustment_lowers_heat_index(self) -> None:
        """RH below 13 percent, temp in [80, 112]F: correction should subtract from the raw regression."""
        temp_f, rh = 95.0, 5.0
        raw = self._raw_rothfusz(temp_f, rh)
        adjusted = calculate_heat_index_f(temp_f, rh)

        assert adjusted < raw
        assert adjusted == 88.2

    def test_low_humidity_adjustment_boundary_at_13_percent(self) -> None:
        """At exactly RH=13, the low-humidity correction (RH < 13) should not apply."""
        temp_f = 95.0
        raw_at_boundary = round(self._raw_rothfusz(temp_f, 13.0), 1)
        adjusted_at_boundary = calculate_heat_index_f(temp_f, 13.0)
        assert adjusted_at_boundary == raw_at_boundary

        # Just below the boundary and with lower humidity, the correction does apply.
        raw_at_zero = round(self._raw_rothfusz(temp_f, 0.0), 1)
        adjusted_at_zero = calculate_heat_index_f(temp_f, 0.0)
        assert adjusted_at_zero < raw_at_zero

    def test_low_humidity_adjustment_not_applied_outside_temp_range(self) -> None:
        """Outside 80-112F, the low-humidity correction should not apply even if RH < 13."""
        temp_f, rh = 113.0, 0.0  # just above the 112F upper bound
        raw = round(self._raw_rothfusz(temp_f, rh), 1)
        adjusted = calculate_heat_index_f(temp_f, rh)
        assert adjusted == raw

    def test_high_humidity_adjustment_raises_heat_index(self) -> None:
        """RH above 85 percent, temp in [80, 87]F: correction should add to the raw regression."""
        temp_f, rh = 80.0, 100.0
        raw = self._raw_rothfusz(temp_f, rh)
        adjusted = calculate_heat_index_f(temp_f, rh)

        assert adjusted > raw
        assert adjusted == 89.3

    def test_high_humidity_adjustment_not_applied_outside_temp_range(self) -> None:
        """Outside 80-87F, the high-humidity correction should not apply even if RH > 85."""
        temp_f, rh = 95.0, 90.0
        raw = self._raw_rothfusz(temp_f, rh)
        adjusted = calculate_heat_index_f(temp_f, rh)
        assert adjusted == round(raw, 1)

    @staticmethod
    def _raw_rothfusz(temp_f: float, rh: float) -> float:
        """Unadjusted Rothfusz polynomial, replicated here to isolate correction-term effects."""
        return (
            -42.379
            + 2.04901523 * temp_f
            + 10.14333127 * rh
            - 0.22475541 * temp_f * rh
            - 0.00683783 * temp_f * temp_f
            - 0.05481717 * rh * rh
            + 0.00122874 * temp_f * temp_f * rh
            + 0.00085282 * temp_f * rh * rh
            - 0.00000199 * temp_f * temp_f * rh * rh
        )


# ---------------------------------------------------------------------------
# Temperature conversions
# ---------------------------------------------------------------------------

class TestTemperatureConversions:
    @pytest.mark.parametrize("temp_c", [-40.0, -17.8, 0.0, 20.0, 37.0, 100.0])
    def test_celsius_to_fahrenheit_to_celsius_round_trip(self, temp_c: float) -> None:
        temp_f = celsius_to_fahrenheit(temp_c)
        result = fahrenheit_to_celsius(temp_f)
        assert result == pytest.approx(temp_c, abs=1e-9)

    def test_known_conversion_values(self) -> None:
        assert celsius_to_fahrenheit(0.0) == pytest.approx(32.0)
        assert celsius_to_fahrenheit(100.0) == pytest.approx(212.0)
        assert fahrenheit_to_celsius(32.0) == pytest.approx(0.0)
        assert fahrenheit_to_celsius(212.0) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Micro-advisories and OSHA work/rest ratios
# ---------------------------------------------------------------------------

class TestAdvisories:
    @pytest.mark.parametrize("band", ALL_BANDS)
    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_get_micro_advisory_non_empty_for_all_band_role_combinations(self, band: str, role: Role) -> None:
        result = get_micro_advisory(band, role)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert all(isinstance(item, str) and item.strip() for item in result)

    def test_get_micro_advisory_falls_back_to_normal_for_unknown_band(self) -> None:
        result = get_micro_advisory("Nonexistent Band", "general_public")
        assert result == get_micro_advisory("Normal", "general_public")

    @pytest.mark.parametrize(
        "temp_c, rh, expected_ratio_substring",
        [
            (10.0, 30.0, "75% work / 25% rest"),
            (28.0, 60.0, "50% work / 50% rest"),
            (33.0, 55.0, "25% work / 75% rest"),
            (45.0, 60.0, "shelter"),
        ],
    )
    def test_build_advisory_work_rest_ratio_matches_risk_level(
        self, temp_c: float, rh: float, expected_ratio_substring: str
    ) -> None:
        advisory = build_advisory(temp_c, rh, "outdoor_worker")
        assert expected_ratio_substring.lower() in advisory.work_rest_ratio.lower()

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_build_advisory_returns_non_empty_micro_advisory(self, role: Role) -> None:
        advisory = build_advisory(35.0, 60.0, role)
        assert len(advisory.micro_advisory) >= 1

    def test_build_advisory_defaults_to_general_public(self) -> None:
        advisory = build_advisory(35.0, 60.0)
        assert advisory.micro_advisory == build_advisory(35.0, 60.0, "general_public").micro_advisory
