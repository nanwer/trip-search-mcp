"""The `search_flights` tool function.

Wires validation, caching, Amadeus calls, error translation, and logging.
The MCP-facing description string lives here too — it is what Claude reads.
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any

from pydantic import ValidationError

from flights_mcp.amadeus.client import AmadeusClient
from flights_mcp.cache import TTLCache, canonical_key
from flights_mcp.errors import ErrorCode, ToolError, error_response
from flights_mcp.logging_config import log_event
from flights_mcp.models import SearchFlightsInput, SearchFlightsResult

TOOL_NAME = "search_flights"

# Operational severity for each upstream failure mode. AUTH_FAILED and
# QUOTA_EXCEEDED need human action; RATE_LIMITED and UPSTREAM_ERROR are
# usually transient.
_LEVEL_FOR_CODE = {
    ErrorCode.AUTH_FAILED: logging.ERROR,
    ErrorCode.QUOTA_EXCEEDED: logging.ERROR,
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

TOOL_DESCRIPTION = """\
Search live flight offers for a given route and date range using the Amadeus GDS feed.

Returns a ranked list of flight options with prices, airlines, segment details, and fare information. Does not book flights, only searches.

Times in the response are local to the departure or arrival airport, with the airport's IATA code attached so the timezone can be derived. Do not perform timezone math on these times without first converting them.

Origin and destination can be either airport IATA codes (IAD, DCA, BWI) or city IATA codes (WAS, LON, NYC). City codes return offers across all airports in that city; Amadeus handles the multi-airport expansion server-side.

Results from identical searches are cached for up to 5 minutes. Prices may move within minutes, so a returned price may be up to 5 minutes old. If the user is about to act on a specific offer, re-run the search before committing to a number.

Dates must be today or future in UTC. The tool rejects past dates with an `invalid_input` error — if a user gives a date that may already be past in their local timezone, advance to the next valid day before calling.

Several fields are nullable because Amadeus does not always populate them. Most importantly, a null `baggage_allowance` means "the airline did not return this information," not "no checked bag is included." Do not state that a fare excludes checked bags based on a null value. The same applies to `last_ticketing_date` and `seats_available`."""

_logger = logging.getLogger("flights_mcp")


def _no_results_message(env: str, origin: str, destination: str, departure_date: str) -> str:
    base = f"No flights found for {origin} to {destination} on {departure_date}."
    if env == "test":
        return (base + " Note: the Amadeus test environment only covers a subset of routes — "
                "if you suspect this route should have service, retry in production.")
    return base + " Try adjusting dates or airports."


async def search_flights(
    *,
    amadeus: AmadeusClient,
    cache: TTLCache,
    env: str,
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
    max_results: int = 20,
) -> dict[str, Any]:
    raw_input = dict(
        origin=origin, destination=destination, departure_date=departure_date,
        return_date=return_date, adults=adults, children=children, infants=infants,
        cabin_class=cabin_class, currency=currency, non_stop_only=non_stop_only,
        max_results=max_results,
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

    # 3. Amadeus call.
    started = time.monotonic()
    try:
        offers = await amadeus.search(params)
    except ToolError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if e.code is ErrorCode.NO_RESULTS:
            msg = _no_results_message(env, params.origin, params.destination, params.departure_date)
            log_event(_logger, "tool.no_results", tool=TOOL_NAME,
                      input=params.model_dump(), elapsed_ms=elapsed_ms)
            return error_response(ErrorCode.NO_RESULTS, msg, retryable=False)
        log_event(_logger, "tool.amadeus_error", tool=TOOL_NAME,
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
