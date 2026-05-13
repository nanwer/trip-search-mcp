"""FastMCP server entry point. Run via `fastmcp run src/trip_search_mcp/server.py`
or `python -m trip_search_mcp.server`."""
from __future__ import annotations

import os
import warnings
from typing import Any

import httpx

# FastMCP transitively imports authlib.jose, which emits an AuthlibDeprecation-
# Warning at every interpreter start. authlib *replaces* the global warning
# filter list on its own import, so the usual filterwarnings/catch_warnings
# tricks don't stick. Intercept at the display layer instead — drop only its
# own class by name so genuine deprecation warnings still surface.
_original_showwarning = warnings.showwarning

def _drop_authlib_deprecation(message, category, filename, lineno, file=None, line=None):
    if category.__name__ == "AuthlibDeprecationWarning":
        return
    _original_showwarning(message, category, filename, lineno, file, line)

warnings.showwarning = _drop_authlib_deprecation

from fastmcp import FastMCP  # noqa: E402 — must follow the warning hook above

from trip_search_mcp.cache import TTLCache
from trip_search_mcp.fli_backend.client import FliClient
from trip_search_mcp.logging_config import configure_logging, log_event
from trip_search_mcp.serpapi_hotels_backend.client import SerpAPIHotelsClient
from trip_search_mcp.tools.search_cheapest_dates import (
    TOOL_DESCRIPTION as CHEAPEST_DATES_DESCRIPTION,
    search_cheapest_dates,
)
from trip_search_mcp.tools.search_flights import TOOL_DESCRIPTION, search_flights
from trip_search_mcp.tools.get_stay_details import (
    TOOL_DESCRIPTION as STAY_DETAILS_DESCRIPTION,
    get_stay_details,
)
from trip_search_mcp.tools.search_stays import (
    TOOL_DESCRIPTION as STAYS_DESCRIPTION,
    search_stays,
)

_logger = configure_logging()


_CLIENT = FliClient()
_CACHE = TTLCache(ttl_seconds=int(os.environ.get("CACHE_TTL_SECONDS", "300")))


def _build_hotels_client() -> SerpAPIHotelsClient | None:
    """Lazy-instantiate the hotels client only if a SerpAPI key is present.

    The server intentionally does not REQUIRE the key — flights work key-
    free via fli, and forcing a SerpAPI signup just to launch the server
    would lose that property. If the key is missing, hotel tool calls
    return a structured auth_failed envelope at call time."""
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return None
    http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    return SerpAPIHotelsClient(http=http, api_key=api_key)


_HOTELS_CLIENT = _build_hotels_client()

mcp = FastMCP("trip-search-mcp")


@mcp.tool(name="search_flights", description=TOOL_DESCRIPTION)
async def search_flights_tool(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin_class: str = "ECONOMY",
    max_stops: str = "ANY",
    departure_window: str | None = None,
    inbound_window: str | None = None,
    airlines: list[str] | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    return await search_flights(
        client=_CLIENT,
        cache=_CACHE,
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
        children=children,
        infants=infants,
        cabin_class=cabin_class,
        max_stops=max_stops,
        departure_window=departure_window,
        inbound_window=inbound_window,
        airlines=airlines,
        max_results=max_results,
    )


@mcp.tool(name="search_cheapest_dates", description=CHEAPEST_DATES_DESCRIPTION)
async def search_cheapest_dates_tool(
    origin: str,
    destination: str,
    start_date: str,
    end_date: str,
    trip_duration: int | None = None,
    is_round_trip: bool = False,
    passengers: int = 1,
    cabin_class: str = "ECONOMY",
    max_stops: str = "ANY",
    departure_window: str | None = None,
    airlines: list[str] | None = None,
) -> dict[str, Any]:
    return await search_cheapest_dates(
        client=_CLIENT,
        cache=_CACHE,
        origin=origin,
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        trip_duration=trip_duration,
        is_round_trip=is_round_trip,
        passengers=passengers,
        cabin_class=cabin_class,
        max_stops=max_stops,
        departure_window=departure_window,
        airlines=airlines,
    )


@mcp.tool(name="search_stays", description=STAYS_DESCRIPTION)
async def search_stays_tool(
    location: str,
    check_in_date: str,
    check_out_date: str,
    adults: int = 2,
    children: int = 0,
    rooms: int = 1,
    category: str = "all",
    min_rating: int | None = None,
    min_bedrooms: int | None = None,
    min_bathrooms: int | None = None,
    min_review_score: float | None = None,
    max_price_per_night: float | None = None,
    required_amenities: list[str] | None = None,
    sort_by: str = "BEST",
    max_results: int = 10,
    currency: str = "EUR",
) -> dict[str, Any]:
    return await search_stays(
        client=_HOTELS_CLIENT,
        cache=_CACHE,
        location=location,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        adults=adults,
        children=children,
        rooms=rooms,
        category=category,
        min_rating=min_rating,
        min_bedrooms=min_bedrooms,
        min_bathrooms=min_bathrooms,
        min_review_score=min_review_score,
        max_price_per_night=max_price_per_night,
        required_amenities=required_amenities,
        sort_by=sort_by,
        max_results=max_results,
        currency=currency,
    )


@mcp.tool(name="get_stay_details", description=STAY_DETAILS_DESCRIPTION)
async def get_stay_details_tool(
    property_token: str,
    check_in_date: str,
    check_out_date: str,
    adults: int = 2,
    currency: str = "EUR",
) -> dict[str, Any]:
    return await get_stay_details(
        client=_HOTELS_CLIENT,
        cache=_CACHE,
        property_token=property_token,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        adults=adults,
        currency=currency,
    )


def main() -> None:
    log_event(_logger, "server.start", stays_enabled=bool(_HOTELS_CLIENT))
    mcp.run()


if __name__ == "__main__":
    main()
