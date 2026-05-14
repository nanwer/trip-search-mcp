"""Free-text location → (ne_lat, ne_long, sw_lat, sw_long) bounding box.

pyairbnb's `search_all` takes a geographic bounding box, not a city
name. This module converts the user's `location` string (e.g. "Tampere",
"Notting Hill, London") into a bounding box via OpenStreetMap's
Nominatim service. Nominatim is free, has no API key, and has a 1
req/sec rate limit per IP (which we comfortably fit under for MCP use).

Results are cached aggressively (TTL 24h) since cities don't move.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from trip_search_mcp.errors import ErrorCode, ToolError

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's TOS requires a meaningful User-Agent identifying the app.
USER_AGENT = "trip-search-mcp/0.2 (https://github.com/nanwer/trip-search-mcp)"
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# Process-local cache of (location_norm → bbox + timestamp). Cities
# don't move; 24h TTL is generous and prevents Nominatim rate-limit
# pressure from repeated identical queries within a session.
_GEOCODE_CACHE: dict[str, tuple[float, tuple[float, float, float, float]]] = {}
_CACHE_TTL_SECONDS = 24 * 60 * 60


def _cache_key(location: str) -> str:
    return location.strip().casefold()


async def geocode_to_bbox(
    location: str,
    *,
    http: httpx.AsyncClient | None = None,
) -> tuple[float, float, float, float]:
    """Return (ne_lat, ne_long, sw_lat, sw_long) for `location`.

    Raises `ToolError(INVALID_INPUT)` when Nominatim returns no match
    (e.g. typo'd city, free-text that isn't a real place). Raises
    `ToolError(UPSTREAM_ERROR)` for network or parsing failures.

    `http` is injectable for tests; the production path opens its own
    client because Nominatim is a separate service from SerpAPI.
    """
    key = _cache_key(location)
    cached = _GEOCODE_CACHE.get(key)
    if cached is not None:
        cached_at, bbox = cached
        if time.time() - cached_at < _CACHE_TTL_SECONDS:
            return bbox

    own_client = http is None
    client = http or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        try:
            response = await client.get(
                NOMINATIM_URL,
                params={
                    "q": location,
                    "format": "json",
                    "limit": "1",
                    "polygon_geojson": "0",
                    "addressdetails": "0",
                },
                headers={"User-Agent": USER_AGENT},
            )
        except httpx.HTTPError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Nominatim network error while geocoding {location!r}: {e}",
                retryable=True,
            ) from e

        if response.status_code != 200:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Nominatim returned {response.status_code} for {location!r}.",
                retryable=(response.status_code >= 500),
            )

        try:
            data: list[dict[str, Any]] = response.json()
        except ValueError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Nominatim returned non-JSON for {location!r}: {e}",
                retryable=True,
            ) from e

        if not data:
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"Couldn't find {location!r} on the map. Try a more specific "
                "name or include the country (e.g. 'Tampere, Finland').",
                retryable=False,
            )

        # Nominatim returns boundingbox as [south_lat, north_lat, west_lon, east_lon]
        # all as STRINGS. Translate to floats and reorder to our (NE, SW) tuple.
        bbox_raw = data[0].get("boundingbox")
        if not bbox_raw or len(bbox_raw) != 4:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Nominatim response missing boundingbox for {location!r}.",
                retryable=True,
            )
        try:
            south_lat, north_lat, west_lon, east_lon = (float(x) for x in bbox_raw)
        except (TypeError, ValueError) as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Nominatim returned non-numeric boundingbox for {location!r}: {e}",
                retryable=True,
            ) from e

        bbox = (north_lat, east_lon, south_lat, west_lon)
        _GEOCODE_CACHE[key] = (time.time(), bbox)
        return bbox
    finally:
        if own_client:
            await client.aclose()


# Process-local cache for point-resolution (lat/lon) results. Same TTL
# rationale as the bbox cache: cities don't move.
_POINT_CACHE: dict[str, tuple[float, tuple[float, float, str]]] = {}


async def geocode_to_point(
    location: str,
    *,
    http: httpx.AsyncClient | None = None,
) -> tuple[float, float, str]:
    """Return (latitude, longitude, resolved_display_name) for `location`.

    Used by the weather tool, which takes a single point rather than a
    bbox. Same Nominatim caller as `geocode_to_bbox` but reads `lat` /
    `lon` / `display_name` instead of `boundingbox`.

    Raises `ToolError(INVALID_INPUT)` when Nominatim returns no match,
    `ToolError(UPSTREAM_ERROR)` for network / parsing failures.
    """
    key = _cache_key(location)
    cached = _POINT_CACHE.get(key)
    if cached is not None:
        cached_at, point = cached
        if time.time() - cached_at < _CACHE_TTL_SECONDS:
            return point

    own_client = http is None
    client = http or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        try:
            response = await client.get(
                NOMINATIM_URL,
                params={
                    "q": location,
                    "format": "json",
                    "limit": "1",
                    "addressdetails": "0",
                },
                headers={"User-Agent": USER_AGENT},
            )
        except httpx.HTTPError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Nominatim network error while geocoding {location!r}: {e}",
                retryable=True,
            ) from e

        if response.status_code != 200:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Nominatim returned {response.status_code} for {location!r}.",
                retryable=(response.status_code >= 500),
            )

        try:
            data: list[dict[str, Any]] = response.json()
        except ValueError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Nominatim returned non-JSON for {location!r}: {e}",
                retryable=True,
            ) from e

        if not data:
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"Couldn't find {location!r} on the map. Try a more specific "
                "name or include the country (e.g. 'Tampere, Finland').",
                retryable=False,
            )

        first = data[0]
        try:
            lat = float(first["lat"])
            lon = float(first["lon"])
        except (KeyError, TypeError, ValueError) as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Nominatim response missing/malformed lat/lon for {location!r}: {e}",
                retryable=True,
            ) from e
        display_name = first.get("display_name") or location

        point = (lat, lon, display_name)
        _POINT_CACHE[key] = (time.time(), point)
        return point
    finally:
        if own_client:
            await client.aclose()
