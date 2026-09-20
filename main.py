"""
main.py

HeatSafe FastAPI application: serves the JSON advisory API and the
server-rendered dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from services import history as history_service
from services import weather as weather_service
from services.config import HistoryRecord, load_config
from services.heat_index import build_advisory
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("heatsafe.main")

app = FastAPI(title="HeatSafe", description="Hyperlocal Climate & Heat-Stress Advisory Engine")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Loaded once at startup; `services/weather.py` and `services/history.py`
# read the same environment variables independently (via `load_config()`),
# so this is the single source of truth for configuration across the app.
app_config = load_config()
logger.info(
    "HeatSafe configuration loaded: open_meteo_url=%s max_retries=%d "
    "base_backoff_seconds=%.2f cache_ttl_seconds=%d history_file_path=%s "
    "city_config_path=%s",
    app_config.open_meteo_url,
    app_config.max_retries,
    app_config.base_backoff_seconds,
    app_config.cache_ttl_seconds,
    app_config.history_file_path,
    app_config.city_config_path or "(none)",
)

_VALID_ROLES = {"outdoor_worker", "general_public"}
_DEFAULT_ROLE = "general_public"


def _resolve_role(role: str | None, persona: str | None) -> str:
    """Canonicalize the role/persona query params, preferring `role`, falling back to `persona`."""
    candidate = role or persona
    if candidate not in _VALID_ROLES:
        if candidate:
            logger.warning("Unrecognized role/persona value %r; defaulting to %s", candidate, _DEFAULT_ROLE)
        return _DEFAULT_ROLE
    return candidate


@app.get("/api/health")
async def health_check():
    """Simple health check endpoint for container orchestration / App Runner."""
    return {"status": "healthy"}


@app.get("/api/advisory")
async def get_advisory(
    city: str | None = Query(None, description="Preset city key, e.g. 'dubai'"),
    lat: float | None = Query(None, description="Latitude, used if city is not provided"),
    lon: float | None = Query(None, description="Longitude, used if city is not provided"),
    role: str | None = Query(None, description="'outdoor_worker' or 'general_public'"),
    persona: str | None = Query(None, description="Alias for 'role', used if 'role' is not provided"),
):
    """
    Return current conditions, a 24h hourly heat-index timeline, and a
    role-based safety advisory for a preset city or arbitrary coordinates.
    """
    resolved_role = _resolve_role(role, persona)
    location_name = "Custom Location"

    if city:
        coords = weather_service.get_city_coordinates(city)
        if coords is None:
            raise HTTPException(status_code=404, detail=f"Unknown city: {city}")
        latitude, longitude = coords["lat"], coords["lon"]
        location_name = coords["name"]
    elif lat is not None and lon is not None:
        latitude, longitude = lat, lon
    else:
        # Default to Phoenix if nothing specified.
        coords = weather_service.get_city_coordinates("phoenix")
        assert coords is not None, "phoenix must always be a valid preset city"
        latitude, longitude = coords["lat"], coords["lon"]
        location_name = coords["name"]

    try:
        snapshot = await weather_service.fetch_weather(latitude, longitude, config=app_config)
    except RuntimeError as exc:
        logger.error("Weather fetch failed entirely: %s", exc)
        raise HTTPException(status_code=503, detail="Weather data unavailable") from exc

    current_advisory = build_advisory(
        snapshot.current_temperature_c,
        snapshot.current_relative_humidity,
        resolved_role,  # type: ignore[arg-type]
    )

    hourly_timeline = [
        {
            "time": point.time,
            "temperature_c": point.temperature_c,
            "relative_humidity": point.relative_humidity,
            **{
                k: v
                for k, v in build_advisory(
                    point.temperature_c, point.relative_humidity, resolved_role  # type: ignore[arg-type]
                ).__dict__.items()
                if k in ("heat_index_c", "heat_index_f", "band", "risk_level", "color")
            },
        }
        for point in snapshot.hourly
    ]

    try:
        history_service.append_record(
            HistoryRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                latitude=snapshot.latitude,
                longitude=snapshot.longitude,
                location_name=location_name,
                temperature_c=current_advisory.temperature_c,
                relative_humidity=current_advisory.relative_humidity,
                heat_index_c=current_advisory.heat_index_c,
                risk_level=current_advisory.risk_level,
                band=current_advisory.band,
            )
        )
    except Exception:
        # History logging must never break the advisory response.
        logger.exception("Unexpected error while appending history record; continuing without it")

    return JSONResponse(
        {
            "location": {
                "name": location_name,
                "latitude": snapshot.latitude,
                "longitude": snapshot.longitude,
            },
            "role": resolved_role,
            "stale_data": snapshot.stale,
            "current": {
                "temperature_c": current_advisory.temperature_c,
                "relative_humidity": current_advisory.relative_humidity,
                "wind_speed_kmh": snapshot.current_wind_speed_kmh,
                "heat_index_c": current_advisory.heat_index_c,
                "heat_index_f": current_advisory.heat_index_f,
                "band": current_advisory.band,
                "risk_level": current_advisory.risk_level,
                "color": current_advisory.color,
                "work_rest_ratio": current_advisory.work_rest_ratio,
                "hydration_l_per_hr": current_advisory.hydration_l_per_hr,
                "micro_advisory": current_advisory.micro_advisory,
            },
            "hourly": hourly_timeline,
            "cities": weather_service.PRESET_CITIES,
        }
    )


@app.get("/api/history/export")
async def export_history() -> Response:
    """Export the last 30 days of historical advisory records as a downloadable CSV file."""
    records = history_service.read_records(days=30)
    csv_content = history_service.export_csv(records)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="heatsafe_history.csv"'},
    )


@app.get("/")
async def index(request: Request):
    """Render the HeatSafe dashboard."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "cities": weather_service.PRESET_CITIES,
        },
    )
