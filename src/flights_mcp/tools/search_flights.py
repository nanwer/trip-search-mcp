"""The `search_flights` tool function.

Wires validation, caching, provider calls, error translation, and logging.
The MCP-facing description string lives here too — it is what Claude reads.
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any

from pydantic import ValidationError

from flights_mcp.cache import TTLCache, canonical_key
from flights_mcp.errors import ErrorCode, ToolError, error_response
from flights_mcp.logging_config import log_event
from flights_mcp.models import (
    ROUND_TRIP_MAX_RESULTS,
    SearchFlightsInput,
    SearchFlightsResult,
)
from flights_mcp.serpapi.client import SerpAPIClient

TOOL_NAME = "search_flights"

# When the caller doesn't specify max_results, pick a default that matches the
# upstream cost profile. Round-trip needs 1+N upstream calls so we stay small;
# one-way is a single call so we can afford the full default.
DEFAULT_MAX_RESULTS_ROUND_TRIP = 3
DEFAULT_MAX_RESULTS_ONE_WAY = 20

# Operational severity for each upstream failure mode. AUTH_FAILED and
# QUOTA_EXCEEDED need human action; RATE_LIMITED and UPSTREAM_ERROR are
# usually transient.
_LEVEL_FOR_CODE = {
    ErrorCode.AUTH_FAILED: logging.ERROR,
    ErrorCode.QUOTA_EXCEEDED: logging.ERROR,
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

TOOL_DESCRIPTION = f"""\
Search live flight offers for a given route and date range using Google Flights data (via SerpAPI).

Returns a ranked list of flight options with prices, airlines, segment details, and fare information. Does not book flights, only searches.

Times in the response are local to the departure or arrival airport, with the airport's IATA code attached so the timezone can be derived. Do not perform timezone math on these times without first converting them.

Origin and destination are 3-letter IATA codes — either an airport code (IAD, DCA, BWI) or a city code (WAS, LON, NYC). City codes return offers across all airports in that city; Google Flights handles the multi-airport expansion server-side.

For round-trip queries (return_date provided), `max_results` is capped at {ROUND_TRIP_MAX_RESULTS} and defaults to {DEFAULT_MAX_RESULTS_ROUND_TRIP} because each result requires a separate upstream call to fetch its matching return leg. For one-way queries, `max_results` defaults to {DEFAULT_MAX_RESULTS_ONE_WAY} and can go up to 50 (single upstream call). If you ask for more than {ROUND_TRIP_MAX_RESULTS} on a round-trip you'll receive an `invalid_input` error — set `max_results` to {ROUND_TRIP_MAX_RESULTS} or lower, or omit `return_date` for a one-way search.

Results from identical searches are cached for up to 5 minutes. Prices may move within minutes, so a returned price may be up to 5 minutes old. If the user is about to act on a specific offer, re-run the search before committing to a number.

Dates must be today or future in UTC. The tool rejects past dates with an `invalid_input` error — if a user gives a date that may already be past in their local timezone, advance to the next valid day before calling.

Several fields may be null because Google Flights does not always populate them. Most importantly, a null `baggage_allowance` means "the carrier did not return this information," not "no checked bag is included." Do not state that a fare excludes checked bags based on a null value. The same applies to `last_ticketing_date` and `seats_available` — both are commonly null with this data source.

PRE-CALL ELICITATION: Before calling this tool, ensure the user has expressed preferences on the following. If any are unspecified, ask the user before searching. Do not assume defaults; results vary materially based on these.

- Baggage: carry-on only, or checked bag needed (affects fare class and final price)
- Connections: non-stop preferred, or okay with stops
- Time of day: red-eye okay, hard arrival deadlines, preferred departure window
- Airline preferences: any airlines to prefer (loyalty programs) or avoid

RESULT PRESENTATION: When returning 2 or more results to the user, render them as an interactive artifact rather than a text list. Each offer is a card showing:

- Total price, prominent
- Airlines (IATA codes)
- Total trip duration and stop count for each leg
- Departure and arrival times for outbound and inbound, labeled with airport codes
- A "Book on Google Flights" button linking to the offer's booking_url, opening in a new tab

Sort cards by price ascending. For a single result, prose is fine."""

_logger = logging.getLogger("flights_mcp")


def _no_results_message(origin: str, destination: str, departure_date: str) -> str:
    return (
        f"No flights found for {origin} to {destination} on {departure_date}. "
        "Try adjusting dates, the cabin class, or the airports."
    )


def _resolve_default_max_results(max_results: int | None, is_round_trip: bool) -> int:
    if max_results is not None:
        return max_results
    return DEFAULT_MAX_RESULTS_ROUND_TRIP if is_round_trip else DEFAULT_MAX_RESULTS_ONE_WAY


async def search_flights(
    *,
    client: SerpAPIClient,
    cache: TTLCache,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin_class: str = "ECONOMY",
    currency: str = "USD",
    non_stop_only: bool = False,
    max_results: int | None = None,
) -> dict[str, Any]:
    # Apply the smart default for max_results (one for round-trip, another for
    # one-way). After this point max_results is concrete.
    resolved_max_results = _resolve_default_max_results(max_results, return_date is not None)

    raw_input = dict(
        origin=origin, destination=destination, departure_date=departure_date,
        return_date=return_date, adults=adults, children=children, infants=infants,
        cabin_class=cabin_class, currency=currency, non_stop_only=non_stop_only,
        max_results=resolved_max_results,
    )
    # 1. Input validation.
    try:
        params = SearchFlightsInput.model_validate(raw_input)
    except ValidationError as e:
        first_error = e.errors()[0]
        field_path = ".".join(str(p) for p in first_error.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first_error.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first_error.get("msg"))
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    # 2. Cache.
    key = canonical_key(params.model_dump())
    cached = cache.get(key)
    if cached is not None:
        log_event(_logger, "tool.cache_hit", tool=TOOL_NAME, input=params.model_dump())
        # Deep-copy so callers cannot mutate the cache entry in place.
        return copy.deepcopy(cached)

    # 3. Provider call.
    started = time.monotonic()
    try:
        offers = await client.search(params)
    except ToolError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if e.code is ErrorCode.NO_RESULTS:
            msg = _no_results_message(params.origin, params.destination, params.departure_date)
            log_event(_logger, "tool.no_results", tool=TOOL_NAME,
                      input=params.model_dump(), elapsed_ms=elapsed_ms)
            return error_response(ErrorCode.NO_RESULTS, msg, retryable=False)
        log_event(_logger, "tool.upstream_error", tool=TOOL_NAME,
                  code=e.code.value, elapsed_ms=elapsed_ms,
                  level=_LEVEL_FOR_CODE.get(e.code, logging.WARNING))
        return error_response(e.code, e.message, retryable=e.retryable)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    result = SearchFlightsResult(results=offers).model_dump(mode="json")
    cache.set(key, result)
    log_event(_logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
              count=len(offers), elapsed_ms=elapsed_ms, cache_hit=False)
    # Return a copy too, for symmetry with the cache-hit branch.
    return copy.deepcopy(result)
