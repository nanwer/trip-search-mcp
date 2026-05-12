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
from flights_mcp.fli_backend.client import FliClient
from flights_mcp.logging_config import log_event
from flights_mcp.models import (
    MaxStops,
    SearchFlightsInput,
    SearchFlightsResult,
)

TOOL_NAME = "search_flights"

# Operational severity per upstream failure mode. fli has no auth/quota
# concepts, so the surviving codes are about transient outages and bugs.
_LEVEL_FOR_CODE = {
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

TOOL_DESCRIPTION = """\
Search live flight offers for a given route and date range using Google Flights data.

Returns a ranked list of flight options with prices, airlines, segment details, and total trip durations. Does not book flights, only searches.

Times in the response are local to the departure or arrival airport, with the airport's IATA code attached so the timezone can be derived. Do not perform timezone math on these times without first converting them.

Origin and destination are 3-letter IATA airport codes (HEL, JFK, LHR). The currency Google Flights returns is determined by the request region and is surfaced in each offer's `currency` field; do not assume USD.

Filter parameters:
- `max_stops`: one of `ANY` (default), `NON_STOP`, `ONE_STOP_OR_FEWER`, `TWO_OR_FEWER_STOPS`. The names mean "this many stops or fewer".
- `departure_window`: a "HH-HH" string in 24-hour local time, e.g. `"6-20"` to restrict to outbound flights departing between 6am and 8pm local. **Applies to the outbound leg only.** Google Flights' native filter does not control the return leg.
- `inbound_window`: a separate "HH-HH" window for the return leg. Same format as `departure_window`. Has no effect on one-way searches. When set, offers whose return-leg first segment departs outside this window are filtered out post-hoc.
- `airlines`: an optional list of IATA airline codes (e.g. `["AY", "FI"]`) to restrict results to those carriers. Omit or pass null for no filter.

Results from identical searches are cached for up to 5 minutes. If the user is about to act on a specific offer, re-run the search before committing to a number.

Dates must be today or future in UTC. The tool rejects past dates with an `invalid_input` error — if a user gives a date that may already be past in their local timezone, advance to the next valid day before calling.

Several fields are commonly null with this data source: `baggage_allowance`, `last_ticketing_date`, and `seats_available`. A null `baggage_allowance` means "the carrier did not surface this information," not "no checked bag is included." Do not state that a fare excludes checked bags based on a null value.

PRE-CALL ELICITATION: Before calling this tool, ensure the user has expressed preferences on the following. If any are unspecified, ask the user before searching. Do not assume defaults; results vary materially based on these.

- Baggage: carry-on only, or checked bag needed (affects fare class and final price)
- Connections: non-stop preferred, or okay with stops (sets `max_stops`)
- Time of day: red-eye okay, hard arrival deadlines, preferred outbound departure window (sets `departure_window`), preferred return departure window (sets `inbound_window`)
- Airline preferences: any airlines to prefer (loyalty programs) or avoid (sets `airlines`)

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
        "Try adjusting dates, the cabin class, the airports, or relaxing max_stops/departure_window."
    )


async def search_flights(
    *,
    client: FliClient,
    cache: TTLCache,
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
    raw_input = dict(
        origin=origin, destination=destination, departure_date=departure_date,
        return_date=return_date, adults=adults, children=children, infants=infants,
        cabin_class=cabin_class, max_stops=max_stops, departure_window=departure_window,
        inbound_window=inbound_window, airlines=airlines, max_results=max_results,
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

    # 2. Cache. Namespace the key by tool so we never collide with another
    # tool's identical-shape input.
    key = canonical_key({"tool": TOOL_NAME, **params.model_dump()})
    cached = cache.get(key)
    if cached is not None:
        log_event(_logger, "tool.cache_hit", tool=TOOL_NAME, input=params.model_dump())
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
        if e.code is ErrorCode.INVALID_INPUT:
            log_event(_logger, "tool.invalid_input", tool=TOOL_NAME,
                      input=params.model_dump(), error=e.message)
            return error_response(ErrorCode.INVALID_INPUT, e.message, retryable=False)
        log_event(_logger, "tool.upstream_error", tool=TOOL_NAME,
                  code=e.code.value, elapsed_ms=elapsed_ms,
                  level=_LEVEL_FOR_CODE.get(e.code, logging.WARNING))
        return error_response(e.code, e.message, retryable=e.retryable)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    result = SearchFlightsResult(results=offers).model_dump(mode="json")
    cache.set(key, result)
    log_event(_logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
              count=len(offers), elapsed_ms=elapsed_ms, cache_hit=False)
    return copy.deepcopy(result)
