"""Translate Open-Meteo response into our WeatherDay shape.

Open-Meteo's `weather_code` is a WMO numeric code (0–99). We maintain
a static map to a human-readable condition string. The map is from
Open-Meteo's documentation at https://open-meteo.com/en/docs.

Pure functions, no I/O.
"""
from __future__ import annotations

from trip_search_mcp.models import GetWeatherForecastResult, WeatherDay, WeatherUnits
from trip_search_mcp.open_meteo_backend.raw import OpenMeteoResponse

# WMO weather code → human-readable condition.
# Source: https://open-meteo.com/en/docs (WMO Weather interpretation codes).
# Grouped by intensity / type. The strings are deliberately short — the
# LLM can pair them with the precip% and high/low when narrating.
WMO_CODE_TO_CONDITION: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Light rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with light hail",
    99: "Thunderstorm with heavy hail",
}


def wmo_to_condition(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return WMO_CODE_TO_CONDITION.get(code, f"Unknown WMO code {code}")


def _temp_unit_from_units_string(units_str: str | None, requested: WeatherUnits) -> str:
    """Open-Meteo returns the unit as '°C' or '°F' in `daily_units`.
    Map to our compact 'C' / 'F'. Fall back to the requested units if the
    response lacks `daily_units` (defensive)."""
    if units_str == "°F":
        return "F"
    if units_str == "°C":
        return "C"
    return "F" if requested is WeatherUnits.IMPERIAL else "C"


def build_forecast(
    parsed: OpenMeteoResponse,
    *,
    label: str,
    units: WeatherUnits,
) -> GetWeatherForecastResult:
    """Translate the Open-Meteo response into a GetWeatherForecastResult.

    Drops days where essential fields (date, max temp, min temp) are
    missing — Open-Meteo is reliable but defensive coding doesn't hurt.
    """
    daily = parsed.daily
    if daily is None or not daily.time:
        return GetWeatherForecastResult(
            location=label,
            latitude=parsed.latitude or 0.0,
            longitude=parsed.longitude or 0.0,
            timezone=parsed.timezone or "UTC",
            units=units,
            days=[],
        )

    temp_unit = _temp_unit_from_units_string(
        parsed.daily_units.temperature_2m_max if parsed.daily_units else None,
        units,
    )

    days: list[WeatherDay] = []
    for i, date_str in enumerate(daily.time):
        hi = daily.temperature_2m_max[i] if i < len(daily.temperature_2m_max) else None
        lo = daily.temperature_2m_min[i] if i < len(daily.temperature_2m_min) else None
        if hi is None or lo is None:
            continue
        code = daily.weathercode[i] if i < len(daily.weathercode) else None
        precip = daily.precipitation_probability_max[i] if i < len(daily.precipitation_probability_max) else None
        sunrise = daily.sunrise[i] if i < len(daily.sunrise) else None
        sunset = daily.sunset[i] if i < len(daily.sunset) else None
        days.append(
            WeatherDay(
                date=date_str,
                high_temp=float(hi),
                low_temp=float(lo),
                temp_unit=temp_unit,
                condition_summary=wmo_to_condition(code),
                weather_code=int(code) if code is not None else -1,
                precipitation_probability_percent=int(precip) if precip is not None else None,
                sunrise=sunrise,
                sunset=sunset,
            )
        )

    return GetWeatherForecastResult(
        location=label,
        latitude=parsed.latitude or 0.0,
        longitude=parsed.longitude or 0.0,
        timezone=parsed.timezone or "UTC",
        units=units,
        days=days,
    )
