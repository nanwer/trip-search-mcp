"""The `get_weather_forecast` tool function.

Calls Open-Meteo (free, no API key) for a 7-day forecast at a given
location. Accepts either a free-text `location` (geocoded via the
existing Nominatim helper) or direct `latitude`/`longitude`.
"""
from __future__ import annotations

import copy
import logging
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from trip_search_mcp.airbnb_backend.geocode import geocode_to_point
from trip_search_mcp.cache import TTLCache, canonical_key
from trip_search_mcp.errors import ErrorCode, ToolError, error_response
from trip_search_mcp.logging_config import log_event
from trip_search_mcp.models import (
    GetWeatherForecastInput,
    WeatherUnits,
)
from trip_search_mcp.open_meteo_backend.client import OpenMeteoClient

TOOL_NAME = "get_weather_forecast"

_LEVEL_FOR_CODE = {
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

TOOL_DESCRIPTION = """\
🎯 **RENDERING DIRECTIVE — READ FIRST.** When this tool returns 3+ forecast days, render them as an **HTML/React artifact** — a horizontal day-strip or 7-day card grid with one tile per day (day name + emoji icon + high/low + precip%), NOT a paragraph or table-in-prose. 1-2 days may be prose. If used as context inside a trip plan, embed the strip inside the plan's artifact.

Get a 7-day weather forecast for a city or specific coordinates. Powered by Open-Meteo (free, global, no API key required).

USE THIS TOOL WHEN:
- The user is planning a trip and packing or scheduling decisions hinge on weather ("will it rain in Lisbon next week", "what's the weather like in Tokyo for the second week of March")
- The user is comparing dates and wants to bias toward sunnier ones
- You're already showing flight or stay options and want to enrich them with a weather context line ("FYI, expect rain Thursday — bias indoor activities")

Inputs:
- `location` (string) — free-text city or neighborhood. Resolved to coordinates via OpenStreetMap Nominatim. Examples: "Tampere, Finland", "Notting Hill, London".
- OR `latitude` + `longitude` (floats) — direct coordinates, skip the geocoding step.
- `start_date` (YYYY-MM-DD) — optional. Defaults to today (UTC).
- `end_date` (YYYY-MM-DD) — optional. Defaults to start_date + 6 days. Hard cap: forecast horizon is 7 days from today.
- `units` — `"metric"` (default, °C, km/h) or `"imperial"` (°F, mph).

Returns a `GetWeatherForecastResult` with:
- `location` — echoed/resolved label
- `latitude`, `longitude`, `timezone` — the resolved coordinates and IANA timezone
- `units` — `"metric"` or `"imperial"`
- `days[]` — list of `WeatherDay` (date, high_temp, low_temp, temp_unit, condition_summary, weather_code, precipitation_probability_percent, sunrise, sunset)

PRE-CALL ELICITATION:
- For "weather in X" with no date hint, default to a 7-day forecast starting today.
- For a specific date ("weather in Tokyo on Friday"), set both `start_date` and `end_date` to that date.
- For a range ("weather in Lisbon next week"), infer the Monday→Sunday range from "next week".
- If the user gives only a country ("weather in Italy"), ask for a specific city.

RESULT PRESENTATION:
- For 4+ days, render as a small artifact: one row per day with date, high/low (with unit symbol), condition + a small WMO-driven emoji (☀️ partly cloudy, 🌧️ rain, ⛈️ thunderstorm, ❄️ snow, ☁️ overcast), precip%.
- For 1-3 days, prose is fine.
- Always disclose units once at the top ("All temperatures in °C.").
- If trip planning is in flight, lead with the rainy days the user should plan around."""

_logger = logging.getLogger("trip_search_mcp")


async def get_weather_forecast(
    *,
    client: OpenMeteoClient,
    cache: TTLCache,
    location: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    units: str = "metric",
) -> dict[str, Any]:
    raw_input = dict(
        location=location, latitude=latitude, longitude=longitude,
        start_date=start_date, end_date=end_date, units=units,
    )

    # 1. Input validation. The pydantic model handles defaulting of
    #    start_date / end_date to (today, today+6) and date-range bounds.
    try:
        params = GetWeatherForecastInput.model_validate(raw_input)
    except ValidationError as e:
        first = e.errors()[0]
        field_path = ".".join(str(p) for p in first.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first.get("msg"))
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    # 2. Resolve location → coordinates if not directly provided.
    label = location or f"({latitude}, {longitude})"
    try:
        if params.latitude is None or params.longitude is None:
            assert params.location is not None  # model validator enforces this
            lat, lon, display_name = await geocode_to_point(params.location)
            params = params.model_copy(update={
                "latitude": lat, "longitude": lon,
            })
            label = display_name
    except ToolError as e:
        elapsed_ms = 0
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME,
                  input=params.model_dump(), error=e.message)
        return error_response(e.code, e.message, retryable=e.retryable)

    # 3. Cache. Key on the FINAL params (with resolved lat/lon) so two
    #    differently-named queries for the same coordinates share a hit.
    key = canonical_key({"tool": TOOL_NAME, **params.model_dump()})
    cached = cache.get(key)
    if cached is not None:
        log_event(_logger, "tool.cache_hit", tool=TOOL_NAME, input=params.model_dump())
        return copy.deepcopy(cached)

    # 4. Provider call.
    started = time.monotonic()
    try:
        result_model = await client.forecast(
            latitude=params.latitude,
            longitude=params.longitude,
            start_date=params.start_date,
            end_date=params.end_date,
            units=WeatherUnits(params.units),
            label=label,
        )
    except ToolError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if e.code is ErrorCode.NO_RESULTS:
            log_event(_logger, "tool.no_results", tool=TOOL_NAME,
                      input=params.model_dump(), elapsed_ms=elapsed_ms)
            return error_response(ErrorCode.NO_RESULTS, e.message, retryable=False)
        log_event(_logger, "tool.upstream_error", tool=TOOL_NAME,
                  code=e.code.value, elapsed_ms=elapsed_ms,
                  level=_LEVEL_FOR_CODE.get(e.code, logging.WARNING))
        return error_response(e.code, e.message, retryable=e.retryable)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    result = result_model.model_dump(mode="json")
    cache.set(key, result)
    log_event(
        _logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
        days=len(result_model.days), elapsed_ms=elapsed_ms, cache_hit=False,
    )
    return copy.deepcopy(result)
