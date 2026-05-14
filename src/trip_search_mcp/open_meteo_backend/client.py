"""Open-Meteo client.

Calls https://api.open-meteo.com/v1/forecast for daily weather data.
No API key required.

Tests substitute `httpx.MockTransport` via the injectable `http` param,
just like every other backend in the project.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import (
    GetWeatherForecastInput,
    GetWeatherForecastResult,
    WeatherUnits,
)
from trip_search_mcp.open_meteo_backend.normalize import build_forecast
from trip_search_mcp.open_meteo_backend.raw import OpenMeteoResponse

BASE_URL = "https://api.open-meteo.com"
_FORECAST_PATH = "/v1/forecast"
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# Fields we always request from the daily endpoint. Open-Meteo is happy
# with a comma-separated list; "auto" timezone returns the IANA name
# for the resolved coordinates so sunrise/sunset are already local.
_DAILY_FIELDS = ",".join([
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "weathercode",
    "sunrise",
    "sunset",
])


class OpenMeteoClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        base_url: str = BASE_URL,
    ):
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def forecast(
        self,
        *,
        latitude: float,
        longitude: float,
        start_date: str,
        end_date: str,
        units: WeatherUnits,
        label: str,
    ) -> GetWeatherForecastResult:
        params: dict[str, str] = {
            "latitude": f"{latitude}",
            "longitude": f"{longitude}",
            "start_date": start_date,
            "end_date": end_date,
            "daily": _DAILY_FIELDS,
            "timezone": "auto",
        }
        if units is WeatherUnits.IMPERIAL:
            params["temperature_unit"] = "fahrenheit"
            params["windspeed_unit"] = "mph"
            params["precipitation_unit"] = "inch"

        body = await self._call(params)
        try:
            parsed = OpenMeteoResponse.model_validate(body)
        except ValidationError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Open-Meteo returned an unparseable response: {e}",
                retryable=True,
            ) from e

        if parsed.daily is None or not parsed.daily.time:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"Open-Meteo returned no daily data for ({latitude}, {longitude}).",
            )

        return build_forecast(parsed, label=label, units=units)

    async def _call(self, params: dict[str, str]) -> dict[str, Any]:
        own_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=_TIMEOUT)
        try:
            try:
                response = await client.get(
                    f"{self._base_url}{_FORECAST_PATH}",
                    params=params,
                    timeout=_TIMEOUT,
                )
            except httpx.HTTPError as e:
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"Open-Meteo network error: {e}",
                    retryable=True,
                ) from e

            sc = response.status_code
            if sc == 429:
                raise ToolError(
                    ErrorCode.RATE_LIMITED,
                    "Open-Meteo rate limit hit. Try again in a moment.",
                    retryable=True,
                )
            if sc == 400:
                raise ToolError(
                    ErrorCode.INVALID_INPUT,
                    f"Open-Meteo rejected the request (400): {response.text[:200]}",
                    retryable=False,
                )
            if sc >= 500:
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"Open-Meteo returned {sc}.",
                    retryable=True,
                )
            if sc != 200:
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"Open-Meteo returned {sc}: {response.text[:200]}",
                )

            try:
                return response.json()
            except json.JSONDecodeError as e:
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"Open-Meteo returned non-JSON body: {e}",
                    retryable=True,
                ) from e
        finally:
            if own_client:
                await client.aclose()
