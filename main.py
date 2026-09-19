"""
main.py

HeatSafe FastAPI application: serves the JSON advisory API and the
server-rendered dashboard.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from services import weather as weather_service
from services.heat_index import build_advisory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("heatsafe.main")

app = FastAPI(title="HeatSafe", description="Hyperlocal Climate & Heat-Stress Advisory Engine")

templates = Jinja2Templates(directory="templates")


@app.get("/api/health")
async def health_check():
    """Simple health check endpoint for container orchestration / App Runner."""
    return {"status": "healthy"}


@app.get("/api/advisory")
async def get_advisory(
    city: Optional[str] = Query(None, description="Preset city key, e.g. 'dubai'"),
    lat: Optional[float] = Query(None, description="Latitude, used if city is not provided"),
    lon: Optional[float] = Query(None, description="Longitude, used if city is not provided"),
    role: str = Query("general_public", pattern="^(outdoor_worker|general_public)$"),
):
    """
    Return current conditions, a 24h hourly heat-index timeline, and a
    role-based safety advisory for a preset city or arbitrary coordinates.
    """
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
        # Default to Phoenix if nothing specified
        coords = weather_service.get_city_coordinates("phoenix")
        latitude, longitude = coords["lat"], coords["lon"]
        location_name = coords["name"]

    try:
        snapshot = await weather_service.fetch_weather(latitude, longitude)
    except RuntimeError as exc:
        logger.error("Weather fetch failed entirely: %s", exc)
        raise HTTPException(status_code=503, detail="Weather data unavailable") from exc

    current_advisory = build_advisory(
        snapshot.current_temperature_c,
        snapshot.current_relative_humidity,
        role,  # type: ignore[arg-type]
    )

    hourly_timeline = [
        {
            "time": point.time,
            "temperature_c": point.temperature_c,
            "relative_humidity": point.relative_humidity,
            **{
                k: v
                for k, v in build_advisory(
                    point.temperature_c, point.relative_humidity, role  # type: ignore[arg-type]
                ).__dict__.items()
                if k in ("heat_index_c", "heat_index_f", "band", "risk_level", "color")
            },
        }
        for point in snapshot.hourly
    ]

    return JSONResponse(
        {
            "location": {
                "name": location_name,
                "latitude": snapshot.latitude,
                "longitude": snapshot.longitude,
            },
            "role": role,
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
