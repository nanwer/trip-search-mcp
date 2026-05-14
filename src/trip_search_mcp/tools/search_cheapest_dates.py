"""The `search_cheapest_dates` tool function — date-flex price grid.

Mirrors the search_flights orchestration: validation → cache → provider call
→ envelope. The provider call delegates to fli.search.SearchDates instead
of SearchFlights.
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any

from pydantic import ValidationError

from trip_search_mcp.cache import TTLCache, canonical_key
from trip_search_mcp.cities import expand_to_airports
from trip_search_mcp.errors import ErrorCode, ToolError, error_response
from trip_search_mcp.fli_backend.client import FliClient
from trip_search_mcp.logging_config import log_event
from trip_search_mcp.models import (
    SearchCheapestDatesInput,
    SearchCheapestDatesResult,
)

TOOL_NAME = "search_cheapest_dates"

_LEVEL_FOR_CODE = {
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

TOOL_DESCRIPTION = """\
🎯 **RENDERING DIRECTIVE — READ FIRST.** When this tool returns 5+ entries, render them as an **HTML/React artifact** — a small price-grid or chart, NOT a long flowing list. For 1-4 entries, prose is fine. The cheapest 1-2 dates should be visually highlighted. Offer to deep-dive into the cheapest date with `search_flights` once the user picks one.

Find which travel dates are cheapest across a flexible range, using Google Flights data.

Returns a list of (departure_date, return_date, price) entries sorted cheapest first. Does not return flight times, airlines, or layover details — for that, use `search_flights` once the user picks a date.

USE THIS TOOL WHEN: the user is flexible on travel dates and wants to know which dates within a range are cheapest. Typical phrasings: "any week in May", "next month sometime", "around the second week of June", "is it cheaper if I shift my trip a few days?".

USE `search_flights` INSTEAD WHEN: the user has specific dates and wants flight details, airlines, departure times, layovers, and bookable offers.

The currency Google Flights returns is determined by the request region and is surfaced in each entry's `currency` field; do not assume USD.

For round-trip date searches, `trip_duration` (in days) is required — it determines each candidate return date. The tool returns a `(departure_date, departure_date + trip_duration)` pair per result. For one-way, `return_date` in each result is null.

Filter parameters mirror search_flights:
- `max_stops`: one of `ANY` (default), `NON_STOP`, `ONE_STOP_OR_FEWER`, `TWO_OR_FEWER_STOPS`. "Or fewer" semantics.
- `departure_window`: a "HH-HH" string in 24-hour local time, applied to the outbound departure. **Hours are inclusive of the start and EXCLUSIVE of the end** — `"8-20"` matches 08:00 through 19:59 local time.
- `airlines`: an optional list of IATA airline codes. Shows date entries where AT LEAST ONE of the listed airlines operates ANY segment. For example, `["FI"]` returns dates with options operated entirely or partly by Icelandair; it does NOT restrict to Icelandair-only itineraries. Omit or pass null for no airline filter.

PRE-CALL ELICITATION: Before calling this tool, ensure the user has expressed:

- Date range: a clear earliest acceptable departure (`start_date`) and latest acceptable departure (`end_date`). If they said "next month" or "sometime in May" without bounds, ask. The wider the range, the slower and noisier the result.
- Trip duration (round-trip only): the number of nights/days they want to be away. "About 10 days" needs to become a concrete `trip_duration` integer.
- What flexibility actually means to the user: are they only flexible on departure date, or also on trip length? If trip length is flexible, run this tool multiple times with different `trip_duration` values; this tool only varies departure within one duration.

RESULT PRESENTATION: Render the results as a sorted list with the cheapest entries highlighted, or a small date grid if the range is short. Each entry shows the departure date, the return date (if round-trip), and the total price with currency. Lead with the cheapest. Offer to deep-dive into a specific date with `search_flights` once the user picks one."""

_logger = logging.getLogger("trip_search_mcp")


async def search_cheapest_dates(
    *,
    client: FliClient,
    cache: TTLCache,
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
    raw_input = dict(
        origin=origin, destination=destination,
        start_date=start_date, end_date=end_date,
        trip_duration=trip_duration, is_round_trip=is_round_trip,
        passengers=passengers, cabin_class=cabin_class,
        max_stops=max_stops, departure_window=departure_window,
        airlines=airlines,
    )

    # 1. Input validation.
    try:
        params = SearchCheapestDatesInput.model_validate(raw_input)
    except ValidationError as e:
        first_error = e.errors()[0]
        field_path = ".".join(str(p) for p in first_error.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first_error.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first_error.get("msg"))
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    # 2. Cache.
    key = canonical_key({"tool": TOOL_NAME, **params.model_dump()})
    cached = cache.get(key)
    if cached is not None:
        log_event(_logger, "tool.cache_hit", tool=TOOL_NAME, input=params.model_dump())
        return copy.deepcopy(cached)

    # 3. Provider call(s). City codes expand to multiple airport pairs.
    started = time.monotonic()
    try:
        offers = await _search_dates_with_city_expansion(client, params)
    except ToolError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if e.code is ErrorCode.NO_RESULTS:
            log_event(_logger, "tool.no_results", tool=TOOL_NAME,
                      input=params.model_dump(), elapsed_ms=elapsed_ms)
            msg = (
                f"No price data found for {params.origin} to {params.destination} "
                f"between {params.start_date} and {params.end_date}. "
                "Try widening the date range or relaxing filters."
            )
            return error_response(ErrorCode.NO_RESULTS, msg, retryable=False)
        if e.code is ErrorCode.INVALID_INPUT:
            log_event(_logger, "tool.invalid_input", tool=TOOL_NAME,
                      input=params.model_dump(), error=e.message)
            return error_response(ErrorCode.INVALID_INPUT, e.message, retryable=False)
        log_event(_logger, "tool.upstream_error", tool=TOOL_NAME,
                  code=e.code.value, elapsed_ms=elapsed_ms,
                  level=_LEVEL_FOR_CODE.get(e.code, logging.WARNING))
        return error_response(e.code, e.message, retryable=e.retryable)

    # 4. Sort cheapest first and wrap.
    offers_sorted = sorted(offers, key=lambda o: o.price)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    result = SearchCheapestDatesResult(results=offers_sorted).model_dump(mode="json")
    cache.set(key, result)
    log_event(_logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
              count=len(offers_sorted), elapsed_ms=elapsed_ms, cache_hit=False)
    return copy.deepcopy(result)


# ----- city-code expansion / multi-airport fanout ----------------------------


async def _search_dates_with_city_expansion(client: FliClient, params):
    """Expand origin / destination city codes and fan out to N parallel
    fli.search_dates() calls. For each (departure_date, return_date)
    tuple across all pairs, keep the LOWEST price.

    For the common case (both sides are airport codes), this collapses
    to a single client.search_dates() — same as the pre-expansion path.
    """
    origins = expand_to_airports(params.origin)
    dests = expand_to_airports(params.destination)
    pairs = [(o, d) for o in origins for d in dests if o != d]
    if not pairs:
        return await client.search_dates(params)
    if len(pairs) == 1:
        sub = params.model_copy(update={"origin": pairs[0][0], "destination": pairs[0][1]})
        return await client.search_dates(sub)

    # Serial (not parallel) — see comment in search_flights's analogue.
    # Parallel fanout triggers Google rate-limit retries that blow per-pair
    # latency from 5-10s up to 50s+. Sequential keeps each pair's call
    # in the normal range; worst case 3×3=9 calls bounded under ~90s.
    results: list = []
    for o, d in pairs:
        sub = params.model_copy(update={"origin": o, "destination": d})
        try:
            res = await client.search_dates(sub)
        except BaseException as exc:  # noqa: BLE001 — preserve to filter below
            res = exc
        results.append(res)

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
            raise res
        all_offers.extend(res)

    if not all_offers:
        if no_results_count == len(pairs):
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"No price data on any of {len(pairs)} airport pairs expanded from "
                f"{params.origin}→{params.destination}.",
            )
        if last_error is not None:
            raise last_error
        raise ToolError(
            ErrorCode.UPSTREAM_ERROR,
            f"All {len(pairs)} airport-pair searches failed.",
            retryable=True,
        )

    # Dedup by (departure_date, return_date) keeping the lowest price.
    by_dates: dict[tuple, Any] = {}
    for offer in all_offers:
        key = (offer.departure_date, offer.return_date)
        existing = by_dates.get(key)
        if existing is None or offer.price < existing.price:
            by_dates[key] = offer
    return list(by_dates.values())
