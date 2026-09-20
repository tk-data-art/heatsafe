"""
heat_index.py

Pure-Python implementation of NOAA's Rothfusz regression for Heat Index,
plus safety-band classification and role-specific micro-advisories.

Reference: NOAA National Weather Service Heat Index equation
https://www.wpc.ncep.noaa.gov/html/heatindex_equation.shtml
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["outdoor_worker", "general_public"]


# ---------------------------------------------------------------------------
# Temperature conversions
# ---------------------------------------------------------------------------

def celsius_to_fahrenheit(temp_c: float) -> float:
    return temp_c * 9.0 / 5.0 + 32.0


def fahrenheit_to_celsius(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


# ---------------------------------------------------------------------------
# NOAA Rothfusz regression
# ---------------------------------------------------------------------------

def _rothfusz_heat_index_f(temp_f: float, rh: float) -> float:
    """
    Full NOAA Rothfusz regression, including the low-range simple formula
    and the standard high/low humidity adjustments.

    temp_f: air temperature in Fahrenheit
    rh: relative humidity in percent (0-100)
    """
    # Simple formula (Steadman-based average), used to decide if the full
    # regression is even necessary, and as the answer for mild conditions.
    simple_hi = 0.5 * (temp_f + 61.0 + ((temp_f - 68.0) * 1.2) + (rh * 0.094))
    average_hi = (simple_hi + temp_f) / 2.0

    if average_hi < 80.0:
        return simple_hi

    # Full Rothfusz regression
    hi = (
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

    # Low humidity adjustment
    if rh < 13.0 and 80.0 <= temp_f <= 112.0:
        adjustment = ((13.0 - rh) / 4.0) * math.sqrt((17.0 - abs(temp_f - 95.0)) / 17.0)
        hi -= adjustment

    # High humidity adjustment
    if rh > 85.0 and 80.0 <= temp_f <= 87.0:
        adjustment = ((rh - 85.0) / 10.0) * ((87.0 - temp_f) / 5.0)
        hi += adjustment

    return hi


def calculate_heat_index_f(temp_f: float, rh: float) -> float:
    """Calculate NOAA Heat Index in Fahrenheit. Deterministic, pure function."""
    rh = max(0.0, min(100.0, rh))
    return round(_rothfusz_heat_index_f(temp_f, rh), 1)


def calculate_heat_index_c(temp_c: float, rh: float) -> float:
    """Calculate NOAA Heat Index in Celsius from Celsius temperature input."""
    temp_f = celsius_to_fahrenheit(temp_c)
    hi_f = calculate_heat_index_f(temp_f, rh)
    return round(fahrenheit_to_celsius(hi_f), 1)


# ---------------------------------------------------------------------------
# Safety band classification
# ---------------------------------------------------------------------------

@dataclass
class SafetyBand:
    band: str            # NOAA band name
    risk_level: str      # Low / Moderate / High / Extreme
    color: str           # UI color token
    work_rest_ratio: str
    hydration_l_per_hr: float


_BANDS: list[tuple[float, SafetyBand]] = [
    # upper_bound_f (exclusive), SafetyBand
    (80.0, SafetyBand("Normal", "Low", "green", "75% work / 25% rest per hour", 0.25)),
    (91.0, SafetyBand("Caution", "Moderate", "yellow", "50% work / 50% rest per hour", 0.5)),
    (103.0, SafetyBand("Extreme Caution", "High", "orange", "25% work / 75% rest per hour", 0.75)),
    (125.0, SafetyBand("Danger", "Extreme", "red", "Cease strenuous work / seek shelter", 1.0)),
    (math.inf, SafetyBand("Extreme Danger", "Extreme", "darkred", "Cease all outdoor work immediately / seek shelter", 1.0)),
]


def classify_heat_index(hi_f: float) -> SafetyBand:
    """Classify a Fahrenheit heat index value into a NOAA safety band."""
    for upper_bound, band in _BANDS:
        if hi_f < upper_bound:
            return band
    return _BANDS[-1][1]


# ---------------------------------------------------------------------------
# Role-specific micro-advisories
# ---------------------------------------------------------------------------

_OUTDOOR_WORKER_ADVISORY: dict[str, list[str]] = {
    "Normal": [
        "Standard hydration schedule: drink water regularly throughout your shift.",
        "No special precautions needed, but keep water accessible.",
    ],
    "Caution": [
        "Drink about 0.5 L of water per hour, even if not thirsty.",
        "Take short shade breaks every hour; wear light-colored, breathable clothing.",
        "Watch coworkers for early signs of heat stress (fatigue, headache).",
    ],
    "Extreme Caution": [
        "Drink about 0.75 L of water per hour and add electrolytes if sweating heavily.",
        "Enforce mandatory shade/rest breaks every 30 minutes (25% work / 75% rest).",
        "Use the buddy system — monitor each other for cramps, dizziness, or nausea.",
        "Reschedule strenuous tasks to early morning or evening if possible.",
    ],
    "Danger": [
        "Drink at least 1 L of water per hour; sports drinks recommended for electrolyte loss.",
        "Suspend non-essential outdoor work; limit exposure to short, monitored intervals.",
        "Provide immediate access to air-conditioned or shaded cooling areas.",
        "Train crew to recognize heat exhaustion and heat stroke symptoms; have a response plan.",
    ],
    "Extreme Danger": [
        "Stop all outdoor work immediately and move to an air-conditioned space.",
        "Continue hydration at 1 L/hr even at rest until body temperature normalizes.",
        "Seek medical attention immediately for confusion, hot/dry skin, or loss of consciousness.",
    ],
}

_GENERAL_PUBLIC_ADVISORY: dict[str, list[str]] = {
    "Normal": [
        "Enjoy outdoor activities normally; carry water as a precaution.",
    ],
    "Caution": [
        "Stay hydrated and limit prolonged direct sun exposure during peak hours.",
        "Check in on elderly neighbors, young children, and pets.",
    ],
    "Extreme Caution": [
        "Reduce strenuous outdoor activity, especially between 11am and 4pm.",
        "Vulnerable groups (elderly, children, chronic illness) should stay indoors in A/C.",
        "Never leave children or pets unattended in vehicles.",
    ],
    "Danger": [
        "Avoid outdoor activity; move errands and exercise indoors.",
        "Check on vulnerable relatives and neighbors at least twice a day.",
        "Watch for heat exhaustion signs: heavy sweating, weakness, dizziness, nausea.",
    ],
    "Extreme Danger": [
        "Stay indoors in air conditioning; this is a life-threatening heat emergency.",
        "Avoid all non-essential outdoor exposure, especially for at-risk populations.",
        "Call emergency services immediately for confusion, hot/dry skin, or fainting.",
    ],
}


def get_micro_advisory(band: str, role: Role) -> list[str]:
    """Return a list of tailored safety micro-advisories for the given band/role."""
    table = _OUTDOOR_WORKER_ADVISORY if role == "outdoor_worker" else _GENERAL_PUBLIC_ADVISORY
    return table.get(band, table["Normal"])


# ---------------------------------------------------------------------------
# High-level convenience API
# ---------------------------------------------------------------------------

@dataclass
class HeatAdvisory:
    temperature_c: float
    relative_humidity: float
    heat_index_c: float
    heat_index_f: float
    band: str
    risk_level: str
    color: str
    work_rest_ratio: str
    hydration_l_per_hr: float
    micro_advisory: list[str] = field(default_factory=list)


def build_advisory(temp_c: float, rh: float, role: Role = "general_public") -> HeatAdvisory:
    """Compute the full heat-safety advisory for a given temperature/humidity/role."""
    temp_f = celsius_to_fahrenheit(temp_c)
    hi_f = calculate_heat_index_f(temp_f, rh)
    hi_c = round(fahrenheit_to_celsius(hi_f), 1)
    band = classify_heat_index(hi_f)
    advisory = get_micro_advisory(band.band, role)

    return HeatAdvisory(
        temperature_c=round(temp_c, 1),
        relative_humidity=round(rh, 1),
        heat_index_c=hi_c,
        heat_index_f=round(hi_f, 1),
        band=band.band,
        risk_level=band.risk_level,
        color=band.color,
        work_rest_ratio=band.work_rest_ratio,
        hydration_l_per_hr=band.hydration_l_per_hr,
        micro_advisory=advisory,
    )
