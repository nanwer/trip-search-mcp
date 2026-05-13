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

from trip_search_mcp.cache import TTLCache, canonical_key
from trip_search_mcp.errors import ErrorCode, ToolError, error_response
from trip_search_mcp.cities import expand_to_airports, is_known_city
from trip_search_mcp.fli_backend.client import FliClient
from trip_search_mcp.logging_config import log_event
from trip_search_mcp.models import (
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

Origin and destination accept 3-letter IATA **airport codes** (HEL, JFK, LHR) AND **city codes** (WAS, NYC, LON, PAR, TYO, LAX/QLA, BOS, …). City codes auto-expand to the metro's busiest 3 airports and search them in parallel; results merge under one ranked list (cheaper variant wins on dedup). Use the airport code when the traveler insists on a specific airport. The currency Google Flights returns is determined by the request region and is surfaced in each offer's `currency` field; do not assume USD.

Filter parameters:
- `max_stops`: one of `ANY` (default), `NON_STOP`, `ONE_STOP_OR_FEWER`, `TWO_OR_FEWER_STOPS`. The names mean "this many stops or fewer".
- `departure_window`: a "HH-HH" string in 24-hour local time, e.g. `"8-20"` to restrict to outbound departures between 8am and 8pm local. **Hours are inclusive of the start and EXCLUSIVE of the end** — `"8-20"` matches 08:00 through 19:59 local time; a 20:00 or 20:30 departure does NOT match. **Applies to the outbound leg only.** Google Flights' native filter does not control the return leg.
- `inbound_window`: a separate "HH-HH" window for the return leg. Same format and same inclusive-start/exclusive-end semantics as `departure_window`. Has no effect on one-way searches. When set, offers whose return-leg first segment departs outside this window are filtered out post-hoc.
- `airlines`: an optional list of IATA airline codes. Shows offers where AT LEAST ONE of the listed airlines operates ANY segment of the itinerary. For example, `["FI"]` returns options operated entirely or partly by Icelandair; it does NOT restrict to Icelandair-only itineraries. Omit or pass null for no airline filter.

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

_logger = logging.getLogger("trip_search_mcp")


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

    # 3. Provider call(s). City codes expand to multiple airport pairs.
    started = time.monotonic()
    try:
        offers = await _search_with_city_expansion(client, params)
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


# ----- city-code expansion / multi-airport fanout ----------------------------


async def _search_with_city_expansion(
    client: FliClient, params: SearchFlightsInput,
):
    """Expand origin / destination city codes to airport lists and fan
    out to N parallel fli calls. Merge by offer_id (lower-price wins),
    sort by total_price, truncate to max_results.

    For the common case (both sides are airport codes), this collapses
    to a single client.search() — same as the pre-expansion path.
    """
    import asyncio

    origins = expand_to_airports(params.origin)
    dests = expand_to_airports(params.destination)
    pairs = [(o, d) for o in origins for d in dests if o != d]
    # If both sides are 1 airport (the typical case), pairs has 1 element.
    if not pairs:
        # Same city on both sides — unlikely but possible if origin=destination.
        # Fall back to the original single-call path.
        return await client.search(params)
    if len(pairs) == 1:
        # Hot path: no fanout needed. Avoid the overhead of asyncio.gather +
        # merge logic when there's nothing to merge.
        sub = params.model_copy(update={"origin": pairs[0][0], "destination": pairs[0][1]})
        return await client.search(sub)

    # Multi-airport fanout. Build a copy of params for each pair, fire
    # them in parallel with return_exceptions=True so one bad pair doesn't
    # tank the others.
    tasks = []
    for o, d in pairs:
        sub = params.model_copy(update={"origin": o, "destination": d})
        tasks.append(client.search(sub))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_offers: list = []
    last_error: ToolError | None = None
    no_results_count = 0
    for res in results:
        if isinstance(res, ToolError):
            if res.code is ErrorCode.NO_RESULTS:
                no_results_count += 1
                continue
            last_error = res
            continue
        if isinstance(res, BaseException):
            raise res  # unexpected, propagate
        all_offers.extend(res)

    # If EVERY pair errored, surface the most informative error.
    if not all_offers:
        if no_results_count == len(pairs):
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"No flights on any of {len(pairs)} airport pairs expanded from "
                f"{params.origin}→{params.destination}.",
            )
        if last_error is not None:
            raise last_error
        raise ToolError(
            ErrorCode.UPSTREAM_ERROR,
            f"All {len(pairs)} airport-pair searches failed.",
            retryable=True,
        )

    return _dedup_and_truncate_offers(all_offers, params.max_results)


def _dedup_and_truncate_offers(offers: list, limit: int) -> list:
    """Dedup offers by offer_id (keep the cheaper variant), sort by
    total_price ascending, truncate to limit."""
    by_id: dict[str, Any] = {}
    for offer in offers:
        existing = by_id.get(offer.offer_id)
        if existing is None or offer.total_price < existing.total_price:
            by_id[offer.offer_id] = offer
    deduped = sorted(by_id.values(), key=lambda o: o.total_price)
    return deduped[:limit]
