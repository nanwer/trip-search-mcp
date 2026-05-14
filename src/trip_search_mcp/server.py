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
from trip_search_mcp.airbnb_backend.client import AirbnbClient
from trip_search_mcp.ecb_backend.client import EcbClient
from trip_search_mcp.open_meteo_backend.client import OpenMeteoClient
from trip_search_mcp.serpapi_events_backend.client import SerpAPIEventsClient
from trip_search_mcp.serpapi_hotels_backend.client import SerpAPIHotelsClient
from trip_search_mcp.tripadvisor_backend.client import SerpAPITripadvisorClient
from trip_search_mcp.tools.search_cheapest_dates import (
    TOOL_DESCRIPTION as CHEAPEST_DATES_DESCRIPTION,
    search_cheapest_dates,
)
from trip_search_mcp.tools.search_activities import (
    TOOL_DESCRIPTION as ACTIVITIES_DESCRIPTION,
    search_activities,
)
from trip_search_mcp.tools.search_events import (
    TOOL_DESCRIPTION as EVENTS_DESCRIPTION,
    search_events,
)
from trip_search_mcp.tools.search_flights import TOOL_DESCRIPTION, search_flights
from trip_search_mcp.tools.cancel_watch import (
    TOOL_DESCRIPTION as CANCEL_WATCH_DESCRIPTION,
    cancel_watch,
)
from trip_search_mcp.tools.convert_currency import (
    TOOL_DESCRIPTION as CONVERT_CURRENCY_DESCRIPTION,
    convert_currency,
)
from trip_search_mcp.tools.get_stay_details import (
    TOOL_DESCRIPTION as STAY_DETAILS_DESCRIPTION,
    get_stay_details,
)
from trip_search_mcp.tools.get_weather_forecast import (
    TOOL_DESCRIPTION as WEATHER_DESCRIPTION,
    get_weather_forecast,
)
from trip_search_mcp.tools.list_active_watches import (
    TOOL_DESCRIPTION as LIST_WATCHES_DESCRIPTION,
    list_active_watches,
)
from trip_search_mcp.tools.watch_flight_price import (
    TOOL_DESCRIPTION as WATCH_FLIGHT_DESCRIPTION,
    watch_flight_price,
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


def _build_events_client() -> SerpAPIEventsClient | None:
    """Same lazy pattern as hotels — events also need SERPAPI_KEY."""
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return None
    http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    return SerpAPIEventsClient(http=http, api_key=api_key)


def _build_tripadvisor_client() -> SerpAPITripadvisorClient | None:
    """Same lazy pattern as hotels/events — Tripadvisor also needs SERPAPI_KEY."""
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return None
    http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    return SerpAPITripadvisorClient(http=http, api_key=api_key)


_HOTELS_CLIENT = _build_hotels_client()
_EVENTS_CLIENT = _build_events_client()
_TRIPADVISOR_CLIENT = _build_tripadvisor_client()
# Airbnb client is always available — pyairbnb is a hard dependency and
# has no API key requirement. Geocoding uses Nominatim (also key-free).
_AIRBNB_CLIENT = AirbnbClient()
# Open-Meteo client is always available — free, no API key.
_WEATHER_CLIENT = OpenMeteoClient()
# ECB client is always available — free, no API key, daily XML feed.
_ECB_CLIENT = EcbClient()

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
        airbnb_client=_AIRBNB_CLIENT,
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


# ----- trip-planning context tools -------------------------------------------


@mcp.tool(name="search_activities", description=ACTIVITIES_DESCRIPTION)
async def search_activities_tool(
    location: str,
    query: str | None = None,
    place_type_filter: str = "both",
    min_rating: float | None = None,
    max_results: int = 15,
) -> dict[str, Any]:
    return await search_activities(
        client=_TRIPADVISOR_CLIENT,
        cache=_CACHE,
        location=location,
        query=query,
        place_type_filter=place_type_filter,
        min_rating=min_rating,
        max_results=max_results,
    )


@mcp.tool(name="search_events", description=EVENTS_DESCRIPTION)
async def search_events_tool(
    location: str,
    query: str | None = None,
    date_filter: str | None = None,
    max_results: int = 15,
) -> dict[str, Any]:
    return await search_events(
        client=_EVENTS_CLIENT,
        cache=_CACHE,
        location=location,
        query=query,
        date_filter=date_filter,
        max_results=max_results,
    )


@mcp.tool(name="convert_currency", description=CONVERT_CURRENCY_DESCRIPTION)
async def convert_currency_tool(
    amount: float,
    from_currency: str,
    to_currency: str,
) -> dict[str, Any]:
    return await convert_currency(
        client=_ECB_CLIENT,
        amount=amount,
        from_currency=from_currency,
        to_currency=to_currency,
    )


@mcp.tool(name="get_weather_forecast", description=WEATHER_DESCRIPTION)
async def get_weather_forecast_tool(
    location: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    units: str = "metric",
) -> dict[str, Any]:
    return await get_weather_forecast(
        client=_WEATHER_CLIENT,
        cache=_CACHE,
        location=location,
        latitude=latitude,
        longitude=longitude,
        start_date=start_date,
        end_date=end_date,
        units=units,
    )


# ----- monitoring tools (deal hunting) ---------------------------------------


@mcp.tool(name="watch_flight_price", description=WATCH_FLIGHT_DESCRIPTION)
async def watch_flight_price_tool(
    origin: str,
    destination: str,
    departure_date: str,
    threshold_price: float,
    currency: str = "EUR",
    return_date: str | None = None,
    adults: int = 1,
    cabin_class: str = "ECONOMY",
    max_stops: str = "ANY",
    note: str | None = None,
) -> dict[str, Any]:
    return await watch_flight_price(
        origin=origin, destination=destination,
        departure_date=departure_date, threshold_price=threshold_price,
        currency=currency, return_date=return_date,
        adults=adults, cabin_class=cabin_class, max_stops=max_stops,
        note=note,
    )


@mcp.tool(name="list_active_watches", description=LIST_WATCHES_DESCRIPTION)
async def list_active_watches_tool(
    refresh_after_hours: float = 6.0,
    include_cancelled: bool = False,
) -> dict[str, Any]:
    return await list_active_watches(
        client=_CLIENT,
        refresh_after_hours=refresh_after_hours,
        include_cancelled=include_cancelled,
    )


@mcp.tool(name="cancel_watch", description=CANCEL_WATCH_DESCRIPTION)
async def cancel_watch_tool(watch_id: str) -> dict[str, Any]:
    return await cancel_watch(watch_id=watch_id)


def main() -> None:
    log_event(_logger, "server.start", stays_enabled=bool(_HOTELS_CLIENT))
    mcp.run()


if __name__ == "__main__":
    main()
