"""Tests for the Open-Meteo backend + get_weather_forecast tool.

Uses the Phase 0 fixtures (real Open-Meteo responses for Reston VA and
Tampere FI) for shape parity, plus mocks for orchestration concerns.
No live API calls in CI.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from trip_search_mcp.cache import TTLCache
from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import GetWeatherForecastInput, WeatherUnits
from trip_search_mcp.open_meteo_backend.client import OpenMeteoClient
from trip_search_mcp.open_meteo_backend.normalize import (
    WMO_CODE_TO_CONDITION,
    build_forecast,
    wmo_to_condition,
)
from trip_search_mcp.open_meteo_backend.raw import OpenMeteoResponse
from trip_search_mcp.tools.get_weather_forecast import get_weather_forecast

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def reston_fixture() -> dict:
    """Phase 0 capture for Reston VA, wrapped in metadata by the verify
    script. Unwrap to get the raw Open-Meteo response."""
    return json.loads(
        (FIXTURES / "weather_open_meteo_reston.json").read_text()
    )["response"]


@pytest.fixture
def tampere_fixture() -> dict:
    return json.loads(
        (FIXTURES / "weather_open_meteo_tampere.json").read_text()
    )["response"]


def _today_iso() -> str:
    return datetime.now(tz=timezone.utc).date().isoformat()


def _days_from_today(n: int) -> str:
    return (datetime.now(tz=timezone.utc).date() + timedelta(days=n)).isoformat()


# ----- WMO code map ---------------------------------------------------------


def test_wmo_code_map_covers_common_codes():
    """The codes Phase 0 saw in the wild must be in the map."""
    for code in [0, 1, 2, 3, 51, 61, 71, 80, 95]:
        assert code in WMO_CODE_TO_CONDITION


def test_wmo_to_condition_known_codes():
    assert wmo_to_condition(0) == "Clear sky"
    assert wmo_to_condition(3) == "Overcast"
    assert wmo_to_condition(61) == "Light rain"
    assert wmo_to_condition(95) == "Thunderstorm"


def test_wmo_to_condition_unknown_falls_back():
    assert "Unknown" in wmo_to_condition(999)
    assert wmo_to_condition(None) == "Unknown"


# ----- normalize from real fixture ------------------------------------------


def test_build_forecast_from_real_fixture(reston_fixture):
    parsed = OpenMeteoResponse.model_validate(reston_fixture)
    result = build_forecast(parsed, label="Reston, VA", units=WeatherUnits.METRIC)
    assert result.location == "Reston, VA"
    assert result.timezone == "America/New_York"
    assert result.units is WeatherUnits.METRIC
    assert len(result.days) == 7
    first = result.days[0]
    assert first.temp_unit == "C"
    assert first.high_temp > first.low_temp
    assert first.condition_summary  # non-empty
    assert isinstance(first.weather_code, int)
    assert first.sunrise and "T" in first.sunrise


def test_build_forecast_tampere_uses_helsinki_timezone(tampere_fixture):
    parsed = OpenMeteoResponse.model_validate(tampere_fixture)
    result = build_forecast(parsed, label="Tampere", units=WeatherUnits.METRIC)
    assert result.timezone == "Europe/Helsinki"
    assert len(result.days) == 7


def test_build_forecast_skips_days_with_missing_temps(reston_fixture):
    """Defensive: if Open-Meteo ever returns a null in the temp arrays,
    that day is dropped rather than the whole result blowing up."""
    import copy
    poisoned = copy.deepcopy(reston_fixture)
    poisoned["daily"]["temperature_2m_max"][2] = None
    parsed = OpenMeteoResponse.model_validate(poisoned)
    result = build_forecast(parsed, label="Reston", units=WeatherUnits.METRIC)
    assert len(result.days) == 6  # day index 2 dropped


# ----- input validation -----------------------------------------------------


def test_input_requires_location_or_coords():
    with pytest.raises(Exception):  # ValidationError
        GetWeatherForecastInput.model_validate({})


def test_input_accepts_coords_only():
    m = GetWeatherForecastInput.model_validate({
        "latitude": 38.96, "longitude": -77.36,
    })
    assert m.latitude == 38.96


def test_input_accepts_location_only():
    m = GetWeatherForecastInput.model_validate({"location": "Tampere"})
    assert m.location == "Tampere"
    assert m.latitude is None


def test_input_defaults_date_range_to_today_plus_six():
    m = GetWeatherForecastInput.model_validate({"location": "Tampere"})
    today = datetime.now(tz=timezone.utc).date()
    assert m.start_date == today.isoformat()
    assert m.end_date == (today + timedelta(days=6)).isoformat()


def test_input_rejects_past_start_date():
    yesterday = (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()
    with pytest.raises(Exception):
        GetWeatherForecastInput.model_validate({
            "location": "Tampere", "start_date": yesterday,
        })


def test_input_rejects_end_before_start():
    with pytest.raises(Exception):
        GetWeatherForecastInput.model_validate({
            "location": "Tampere",
            "start_date": _days_from_today(3),
            "end_date": _days_from_today(1),
        })


def test_input_rejects_horizon_over_7_days():
    with pytest.raises(Exception):
        GetWeatherForecastInput.model_validate({
            "location": "Tampere",
            "end_date": _days_from_today(7),  # 0-indexed: 8th day from today
        })


# ----- client orchestration --------------------------------------------------


def _stub_client_returning(fixture: dict) -> OpenMeteoClient:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=fixture))
    http = httpx.AsyncClient(transport=transport)
    return OpenMeteoClient(http=http)


async def test_client_forecast_returns_normalized_result(reston_fixture):
    client = _stub_client_returning(reston_fixture)
    result = await client.forecast(
        latitude=38.96, longitude=-77.36,
        start_date=_today_iso(), end_date=_days_from_today(6),
        units=WeatherUnits.METRIC, label="Reston, VA",
    )
    assert len(result.days) == 7
    assert all(d.temp_unit == "C" for d in result.days)


async def test_client_threads_imperial_units(reston_fixture):
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(dict(req.url.params))
        return httpx.Response(200, json=reston_fixture)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = OpenMeteoClient(http=http)
    await client.forecast(
        latitude=38.96, longitude=-77.36,
        start_date=_today_iso(), end_date=_days_from_today(2),
        units=WeatherUnits.IMPERIAL, label="Reston, VA",
    )
    assert captured["temperature_unit"] == "fahrenheit"
    assert captured["windspeed_unit"] == "mph"


async def test_client_429_maps_to_rate_limited():
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    http = httpx.AsyncClient(transport=transport)
    client = OpenMeteoClient(http=http)
    with pytest.raises(ToolError) as exc:
        await client.forecast(
            latitude=0, longitude=0,
            start_date=_today_iso(), end_date=_today_iso(),
            units=WeatherUnits.METRIC, label="x",
        )
    assert exc.value.code is ErrorCode.RATE_LIMITED


async def test_client_400_maps_to_invalid_input():
    transport = httpx.MockTransport(lambda req: httpx.Response(400, text="bad request"))
    http = httpx.AsyncClient(transport=transport)
    client = OpenMeteoClient(http=http)
    with pytest.raises(ToolError) as exc:
        await client.forecast(
            latitude=0, longitude=0,
            start_date=_today_iso(), end_date=_today_iso(),
            units=WeatherUnits.METRIC, label="x",
        )
    assert exc.value.code is ErrorCode.INVALID_INPUT


# ----- tool function: orchestration -----------------------------------------


async def test_tool_with_coords_returns_success_envelope(reston_fixture):
    client = _stub_client_returning(reston_fixture)
    cache = TTLCache(ttl_seconds=300)
    result = await get_weather_forecast(
        client=client, cache=cache,
        latitude=38.96, longitude=-77.36,
        start_date=_today_iso(), end_date=_days_from_today(6),
    )
    assert "error" not in result
    assert len(result["days"]) == 7


async def test_tool_geocodes_location_via_nominatim_stub(reston_fixture, monkeypatch):
    """Patch the geocoder so no live Nominatim call escapes CI."""
    from trip_search_mcp.tools import get_weather_forecast as tool_mod

    async def stub_geocode(location, *, http=None):
        return (38.96, -77.36, f"Resolved: {location}")

    monkeypatch.setattr(tool_mod, "geocode_to_point", stub_geocode)

    client = _stub_client_returning(reston_fixture)
    cache = TTLCache(ttl_seconds=300)
    result = await get_weather_forecast(
        client=client, cache=cache, location="Reston, VA",
    )
    assert "error" not in result
    assert "Resolved" in result["location"]


async def test_tool_caches_by_resolved_coordinates(reston_fixture):
    """Second identical call hits cache → zero Open-Meteo traffic."""
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=reston_fixture)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = OpenMeteoClient(http=http)
    cache = TTLCache(ttl_seconds=300)
    kwargs = dict(
        client=client, cache=cache,
        latitude=38.96, longitude=-77.36,
        start_date=_today_iso(), end_date=_days_from_today(6),
    )
    await get_weather_forecast(**kwargs)
    await get_weather_forecast(**kwargs)
    assert call_count["n"] == 1


async def test_tool_invalid_date_returns_envelope():
    client = _stub_client_returning({})  # won't be reached
    cache = TTLCache(ttl_seconds=300)
    yesterday = (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()
    result = await get_weather_forecast(
        client=client, cache=cache,
        latitude=0, longitude=0,
        start_date=yesterday, end_date=_today_iso(),
    )
    assert result["error"]["code"] == "invalid_input"


async def test_tool_geocode_failure_propagates_invalid_input(monkeypatch):
    from trip_search_mcp.tools import get_weather_forecast as tool_mod

    async def stub_failed_geocode(location, *, http=None):
        raise ToolError(
            ErrorCode.INVALID_INPUT,
            f"Couldn't find {location!r}",
            retryable=False,
        )

    monkeypatch.setattr(tool_mod, "geocode_to_point", stub_failed_geocode)
    cache = TTLCache(ttl_seconds=300)
    # Client doesn't matter — geocode fails first.
    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    http = httpx.AsyncClient(transport=transport)
    client = OpenMeteoClient(http=http)
    result = await get_weather_forecast(
        client=client, cache=cache, location="Atlantis-That-Sank",
    )
    assert result["error"]["code"] == "invalid_input"
