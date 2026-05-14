#!/usr/bin/env python
"""Phase 0 (weather forecast MCP tool): verify free weather providers.

Evaluates two free, no-API-key weather forecast services to decide between a
single-provider (Open-Meteo, global) design and a hybrid (NWS for US +
Open-Meteo elsewhere) design:

  1. Open-Meteo (https://api.open-meteo.com) — free, global, no key.
  2. National Weather Service (https://api.weather.gov) — free, US-only,
     no key but requires a User-Agent header. Two-step protocol:
     /points/{lat},{lon} returns a `properties.forecast` URL → GET that.

We probe each provider with two coordinates:
  - Reston, VA (38.96, -77.36)  — inside NWS coverage
  - Tampere, FI (61.50, 23.79)  — outside NWS coverage (must fail gracefully)

Outputs:
  tests/fixtures/weather_open_meteo_reston.json
  tests/fixtures/weather_open_meteo_tampere.json
  tests/fixtures/weather_nws_reston.json
  tests/fixtures/weather_nws_tampere_outside.json

Each fixture wraps the raw response with metadata (latency, status, error).

Usage:
    .venv/bin/python scripts/verify_weather_providers.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

FIXTURE_DIR = Path("tests/fixtures")
OM_RESTON = FIXTURE_DIR / "weather_open_meteo_reston.json"
OM_TAMPERE = FIXTURE_DIR / "weather_open_meteo_tampere.json"
NWS_RESTON = FIXTURE_DIR / "weather_nws_reston.json"
NWS_TAMPERE = FIXTURE_DIR / "weather_nws_tampere_outside.json"

RESTON = (38.96, -77.36)
TAMPERE = (61.50, 23.79)

# NWS requires a descriptive User-Agent. Per their docs:
# https://www.weather.gov/documentation/services-web-api
NWS_HEADERS = {
    "User-Agent": "trip-search-mcp/0.2 (verify_weather_providers.py; contact: nanwer@omnesoft.com)",
    "Accept": "application/geo+json",
}

# Daily fields we want — matches the data the future MCP tool would expose.
OM_DAILY_FIELDS = ",".join([
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "weathercode",
    "sunrise",
    "sunset",
])


def _write_fixture(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))


def call_open_meteo(lat: float, lon: float, label: str) -> dict[str, Any]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": OM_DAILY_FIELDS,
        "timezone": "auto",
        "forecast_days": 7,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }
    print(f"-> Open-Meteo {label} ({lat}, {lon})")
    started = time.monotonic()
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(url, params=params)
    elapsed = time.monotonic() - started
    print(f"   status={resp.status_code} in {elapsed:.2f}s")
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        body = {"_decode_error": str(exc), "text": resp.text}
    return {
        "provider": "open-meteo",
        "label": label,
        "request": {"url": str(resp.request.url), "params": params},
        "status_code": resp.status_code,
        "latency_seconds": round(elapsed, 3),
        "response": body,
    }


def call_nws(lat: float, lon: float, label: str) -> dict[str, Any]:
    """Two-step NWS call: /points/{lat},{lon} -> properties.forecast -> GET it."""
    points_url = f"https://api.weather.gov/points/{lat},{lon}"
    print(f"-> NWS {label} ({lat}, {lon}) step 1: points")
    t0 = time.monotonic()
    with httpx.Client(timeout=15.0, headers=NWS_HEADERS) as client:
        try:
            points_resp = client.get(points_url)
        except httpx.HTTPError as exc:
            return {
                "provider": "nws",
                "label": label,
                "step": "points",
                "error": f"transport: {exc}",
                "latency_seconds": round(time.monotonic() - t0, 3),
            }
        points_elapsed = time.monotonic() - t0
        print(f"   points status={points_resp.status_code} in {points_elapsed:.2f}s")

        try:
            points_body = points_resp.json()
        except Exception as exc:  # noqa: BLE001
            points_body = {"_decode_error": str(exc), "text": points_resp.text}

        # If points failed (e.g., outside US), capture and return.
        if points_resp.status_code >= 400:
            return {
                "provider": "nws",
                "label": label,
                "outside_coverage": True,
                "points_request": {"url": str(points_resp.request.url)},
                "points_status_code": points_resp.status_code,
                "points_latency_seconds": round(points_elapsed, 3),
                "points_response": points_body,
                "forecast_response": None,
            }

        forecast_url = (
            points_body.get("properties", {}).get("forecast")
            if isinstance(points_body, dict)
            else None
        )
        if not forecast_url:
            return {
                "provider": "nws",
                "label": label,
                "outside_coverage": True,
                "points_status_code": points_resp.status_code,
                "points_latency_seconds": round(points_elapsed, 3),
                "points_response": points_body,
                "forecast_response": None,
                "note": "points response missing properties.forecast",
            }

        print(f"-> NWS {label} step 2: forecast {forecast_url}")
        t1 = time.monotonic()
        forecast_resp = client.get(forecast_url)
        forecast_elapsed = time.monotonic() - t1
        print(
            f"   forecast status={forecast_resp.status_code} in {forecast_elapsed:.2f}s"
        )
        try:
            forecast_body = forecast_resp.json()
        except Exception as exc:  # noqa: BLE001
            forecast_body = {"_decode_error": str(exc), "text": forecast_resp.text}

    total = points_elapsed + forecast_elapsed
    return {
        "provider": "nws",
        "label": label,
        "outside_coverage": False,
        "points_request": {"url": points_url},
        "points_status_code": points_resp.status_code,
        "points_latency_seconds": round(points_elapsed, 3),
        "points_response_sample": {
            "gridId": points_body.get("properties", {}).get("gridId"),
            "forecast": forecast_url,
            "forecastHourly": points_body.get("properties", {}).get("forecastHourly"),
            "timeZone": points_body.get("properties", {}).get("timeZone"),
        }
        if isinstance(points_body, dict)
        else points_body,
        "forecast_url": forecast_url,
        "forecast_status_code": forecast_resp.status_code,
        "forecast_latency_seconds": round(forecast_elapsed, 3),
        "total_latency_seconds": round(total, 3),
        "forecast_response": forecast_body,
    }


def summarize_open_meteo(reston: dict, tampere: dict) -> None:
    print()
    print("=" * 72)
    print("Q1. Open-Meteo field parity (daily)")
    print("=" * 72)
    body = reston["response"]
    daily = body.get("daily", {}) if isinstance(body, dict) else {}
    daily_units = body.get("daily_units", {}) if isinstance(body, dict) else {}
    keys = list(daily.keys())
    print(f"  daily keys returned: {keys}")
    print(f"  daily_units: {daily_units}")
    expected = {
        "max temp": "temperature_2m_max",
        "min temp": "temperature_2m_min",
        "precip prob": "precipitation_probability_max",
        "weather code": "weathercode",
        "sunrise": "sunrise",
        "sunset": "sunset",
    }
    for human, key in expected.items():
        present = key in daily
        sample = daily.get(key, [None])[0] if present else None
        unit = daily_units.get(key, "")
        print(f"    - {human:<14} key={key!r:<35} present={present} unit={unit!r} sample={sample!r}")
    days = len(daily.get("time", []))
    print(f"  days returned: {days}")
    print(f"  timezone echoed: {body.get('timezone')!r}, offset={body.get('utc_offset_seconds')!r}")

    print()
    print("  Tampere sanity check:")
    tbody = tampere["response"]
    tdaily = tbody.get("daily", {}) if isinstance(tbody, dict) else {}
    print(f"    days={len(tdaily.get('time', []))}, "
          f"tmax_sample={tdaily.get('temperature_2m_max', [None])[0]}, "
          f"timezone={tbody.get('timezone')!r}")


def summarize_nws(reston: dict, tampere: dict) -> None:
    print()
    print("=" * 72)
    print("Q2. NWS field parity (periods)")
    print("=" * 72)
    if reston.get("outside_coverage"):
        print(f"  Reston unexpectedly outside coverage: {reston}")
        return
    forecast = reston.get("forecast_response", {})
    props = forecast.get("properties", {}) if isinstance(forecast, dict) else {}
    periods = props.get("periods", []) if isinstance(props, dict) else []
    print(f"  periods returned: {len(periods)}")
    if periods:
        keys = list(periods[0].keys())
        print(f"  period[0] keys: {keys}")
        for i, p in enumerate(periods[:4]):
            print(
                f"    [{i}] name={p.get('name')!r:<22} "
                f"isDaytime={p.get('isDaytime')} "
                f"temp={p.get('temperature')}{p.get('temperatureUnit')} "
                f"precipProb={(p.get('probabilityOfPrecipitation') or {}).get('value')} "
                f"short={p.get('shortForecast')!r}"
            )
    print()
    print("  Aggregation note: NWS alternates daytime/nighttime periods")
    print("  (e.g. 'Tonight', 'Thursday', 'Thursday Night', 'Friday'). To get a")
    print("  daily high/low pair we'd merge consecutive (daytime, night) periods")
    print("  by matching name prefixes / startTime date, taking daytime temp as")
    print("  the high and nighttime temp as the low.")

    print()
    print("=" * 72)
    print("Q4. NWS Tampere (non-US) behavior")
    print("=" * 72)
    print(f"  outside_coverage flag: {tampere.get('outside_coverage')}")
    print(f"  points status: {tampere.get('points_status_code')}")
    pr = tampere.get("points_response", {})
    if isinstance(pr, dict):
        # Trim to relevant keys.
        keys_present = list(pr.keys())
        print(f"  points response keys: {keys_present}")
        for k in ("title", "type", "status", "detail", "instance", "correlationId"):
            if k in pr:
                print(f"    {k}: {pr[k]!r}")
    else:
        print(f"  points response: {pr!r}")


def summarize_latency(om_reston, om_tampere, nws_reston, nws_tampere) -> None:
    print()
    print("=" * 72)
    print("Q5. Latency")
    print("=" * 72)
    print(f"  Open-Meteo Reston : {om_reston['latency_seconds']}s")
    print(f"  Open-Meteo Tampere: {om_tampere['latency_seconds']}s")
    if not nws_reston.get("outside_coverage"):
        print(
            f"  NWS Reston        : points={nws_reston['points_latency_seconds']}s + "
            f"forecast={nws_reston['forecast_latency_seconds']}s = "
            f"total={nws_reston['total_latency_seconds']}s"
        )
    else:
        print(f"  NWS Reston unexpectedly outside coverage: {nws_reston}")
    print(
        f"  NWS Tampere       : points={nws_tampere.get('points_latency_seconds')}s "
        f"(fast-fail, no second call)"
    )


def summarize_weather_codes(reston: dict) -> None:
    print()
    print("=" * 72)
    print("Q3. Open-Meteo weather code → text mapping")
    print("=" * 72)
    body = reston["response"]
    daily = body.get("daily", {}) if isinstance(body, dict) else {}
    codes = daily.get("weathercode", [])
    print(f"  weathercode samples (7 days): {codes}")
    print("  No human-readable 'condition' string in the payload.")
    print("  We must maintain a WMO code -> text map. See:")
    print("    https://open-meteo.com/en/docs (WMO Weather interpretation codes)")
    print("  Common buckets: 0=Clear, 1-3=Partly cloudy/Overcast, 45/48=Fog,")
    print("    51-57=Drizzle, 61-67=Rain, 71-77=Snow, 80-82=Rain showers,")
    print("    85-86=Snow showers, 95-99=Thunderstorm.")


def main() -> int:
    print("Phase 0: weather provider verification\n")

    om_reston = call_open_meteo(*RESTON, label="Reston VA")
    _write_fixture(OM_RESTON, om_reston)

    om_tampere = call_open_meteo(*TAMPERE, label="Tampere FI")
    _write_fixture(OM_TAMPERE, om_tampere)

    nws_reston = call_nws(*RESTON, label="Reston VA")
    _write_fixture(NWS_RESTON, nws_reston)

    nws_tampere = call_nws(*TAMPERE, label="Tampere FI (expect failure)")
    _write_fixture(NWS_TAMPERE, nws_tampere)

    # Confirm NWS rejects Tampere as expected.
    nws_tampere_failed = bool(nws_tampere.get("outside_coverage")) or (
        nws_tampere.get("points_status_code") and nws_tampere["points_status_code"] >= 400
    )
    print()
    print(f"NWS Tampere rejection confirmed: {nws_tampere_failed}")

    summarize_open_meteo(om_reston, om_tampere)
    summarize_nws(nws_reston, nws_tampere)
    summarize_weather_codes(om_reston)
    summarize_latency(om_reston, om_tampere, nws_reston, nws_tampere)

    print()
    print("Fixtures written:")
    for p in (OM_RESTON, OM_TAMPERE, NWS_RESTON, NWS_TAMPERE):
        print(f"  - {p}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
