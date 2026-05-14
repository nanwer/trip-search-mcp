"""Pydantic models for parsing Open-Meteo's `/v1/forecast` response.

Captures only the daily-forecast fields we read. Open-Meteo has hourly
+ current_weather endpoints too; not needed for v1.

`extra="ignore"` so a future Open-Meteo field addition slips through
without breaking parsing.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _OpenMeteoModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class OpenMeteoDailyBlock(_OpenMeteoModel):
    """The `daily` block — parallel arrays keyed by index. Index 0 is
    `time[0]`, index 1 is `time[1]`, etc."""
    time: list[str] = Field(default_factory=list)              # ISO dates
    temperature_2m_max: list[float | None] = Field(default_factory=list)
    temperature_2m_min: list[float | None] = Field(default_factory=list)
    precipitation_probability_max: list[int | None] = Field(default_factory=list)
    weathercode: list[int | None] = Field(default_factory=list)
    sunrise: list[str | None] = Field(default_factory=list)
    sunset: list[str | None] = Field(default_factory=list)


class OpenMeteoDailyUnits(_OpenMeteoModel):
    temperature_2m_max: str | None = None     # "°C" or "°F"
    temperature_2m_min: str | None = None


class OpenMeteoResponse(_OpenMeteoModel):
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None                # IANA name
    timezone_abbreviation: str | None = None
    utc_offset_seconds: int | None = None
    daily: OpenMeteoDailyBlock | None = None
    daily_units: OpenMeteoDailyUnits | None = None
