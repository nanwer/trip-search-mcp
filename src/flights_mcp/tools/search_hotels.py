"""The `search_hotels` tool function.

Same orchestration shape as `search_flights`: validate → cache → call →
envelope. Difference: this tool needs a SerpAPI key. The MCP server starts
WITHOUT requiring `SERPAPI_KEY` — flights work key-free — so the hotels
client may be None at call time. We return a structured auth_failed envelope
in that case rather than crashing the server or the call.
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
    HotelSortBy,
    SearchHotelsInput,
    SearchHotelsResult,
)
from flights_mcp.serpapi_hotels_backend.client import SerpAPIHotelsClient

TOOL_NAME = "search_hotels"

_LEVEL_FOR_CODE = {
    ErrorCode.AUTH_FAILED: logging.ERROR,
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

_NO_KEY_MESSAGE = (
    "SERPAPI_KEY is not set. Hotel search requires a SerpAPI key "
    "(free tier 100 searches/month at serpapi.com). "
    "Set SERPAPI_KEY in your MCP client's environment and restart."
)

TOOL_DESCRIPTION = """\
Search Google Hotels for a city, date range, and party size, returning a ranked list of available properties.

Returns ranked hotel offers with name, photos, star rating, review score, price (per-night and total), top amenities, GPS coordinates, and a per-property Google Hotels deep link. Does NOT book — the booking_url opens the specific property's Google Hotels entity page with the user's check-in/check-out pre-filled, where they can click through to a booking partner.

Prices come back in **EUR** by default (matches the flights tool's typical response currency for European-IP users, so hotel-vs-flight totals are directly comparable). The server pins the request currency for predictability; the `currency` field on each offer reflects what was actually requested.

**`address` is always null on offers** — SerpAPI's google_hotels list endpoint doesn't carry per-property addresses. Use `latitude`/`longitude` for location, or follow up with a property_details call (not yet implemented) for the postal address.

The review score is **Google's native 0-5 scale** (e.g., 4.6 / 5), NOT a 0-10 scale. The star rating is the property's hotel class (1-5 stars, integer).

Filter parameters (apply post-fetch when SerpAPI doesn't natively bind them):
- `min_rating` (1-5): minimum star count. Properties without a star rating are excluded when this is set.
- `min_review_score` (0.0-5.0): minimum Google review score. Properties without a review score are excluded when set.
- `max_price_per_night`: cap on per-night price in the response currency.
- `required_amenities`: free-text amenity names matched case-insensitively as substrings against each property's amenity list. Best-effort — SerpAPI returns amenities as untyped strings ('Free breakfast', 'Free Wi-Fi') so subtle wording matters.

`sort_by` accepts: `BEST` (Google's default ordering — preserves SerpAPI's returned order), `PRICE_LOW`, `PRICE_HIGH`, `RATING` (star rating descending, review_score tie-break), `REVIEW_SCORE` (review_score descending, review_count tie-break).

PRE-CALL ELICITATION: Before calling this tool, confirm with the user:

- **Location**: specific city or neighborhood — "Tampere" works, "Notting Hill, London" works, "somewhere in Europe" does not. Ask if vague.
- **Check-in and check-out dates**: both required and check_out must be strictly after check_in. Confirm UTC-today or later.
- **Party size**: adults, children, and number of rooms. Default is 2 adults / 0 children / 1 room — don't assume; ask if not stated.
- **Budget**: any per-night ceiling? If the user said "cheap" or "affordable", ask for a concrete number to set max_price_per_night.
- **Must-have amenities**: wifi, breakfast, parking, gym, pool, pet-friendly? Don't assume; ask.
- **Star rating or review score floor**: "at least 4 stars", "well-reviewed (8+)"? Map to min_rating or min_review_score (remember review_score is 0-5, so "8+" should become min_review_score=4.0 or you should ask for clarification).
- **Sort priority**: cheapest first, highest-rated, best location? Map to sort_by.

RESULT PRESENTATION: When returning 2+ hotels, render them as an interactive artifact with one card per offer. Each card shows:

- The hotel name, prominent and large at the top of the card (it carries the card's visual hierarchy in the absence of a photo).
- Star rating (as filled stars if you can) and review_score with review_count: "4.6 / 5  (686 reviews)".
- Price per night with the total alongside in smaller text, in the response currency.
- Top 3-4 amenities pulled from the `amenities` list.
- Hotel type and short description if present.
- A "Book on Google Hotels" button linking to `booking_url`, opening in a new tab.

Do NOT render the `images` field as photo elements. Hotel image CDNs (Google's signed `gps-cs-s` URLs, hotelbeds, trvl-media, bstatic, giata) use hotlink protection that breaks these URLs outside their intended hosts; broken images degrade the card more than missing photos. The `images` field stays on the response model for future use (e.g., a server-side image proxy layer), but card rendering should be text-only. Compensate for the missing visual hierarchy by making the hotel name larger and prominent at the top of each card.

Sort cards by the same `sort_by` the user requested. For a single result, prose is fine."""

_logger = logging.getLogger("flights_mcp")


async def search_hotels(
    *,
    client: SerpAPIHotelsClient | None,
    cache: TTLCache,
    location: str,
    check_in_date: str,
    check_out_date: str,
    adults: int = 2,
    children: int = 0,
    rooms: int = 1,
    min_rating: int | None = None,
    min_review_score: float | None = None,
    max_price_per_night: float | None = None,
    required_amenities: list[str] | None = None,
    sort_by: str = "BEST",
    max_results: int = 10,
) -> dict[str, Any]:
    raw_input = dict(
        location=location, check_in_date=check_in_date, check_out_date=check_out_date,
        adults=adults, children=children, rooms=rooms,
        min_rating=min_rating, min_review_score=min_review_score,
        max_price_per_night=max_price_per_night,
        required_amenities=required_amenities,
        sort_by=sort_by, max_results=max_results,
    )

    # 1. Input validation.
    try:
        params = SearchHotelsInput.model_validate(raw_input)
    except ValidationError as e:
        first_error = e.errors()[0]
        field_path = ".".join(str(p) for p in first_error.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first_error.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first_error.get("msg"))
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    # 2. Lazy auth check. If the server started without SERPAPI_KEY, the
    #    hotels client wasn't built. Surface that as a clean auth_failed
    #    envelope with an actionable next step.
    if client is None:
        log_event(_logger, "tool.auth_failed", tool=TOOL_NAME,
                  level=logging.ERROR, reason="SERPAPI_KEY not set")
        return error_response(ErrorCode.AUTH_FAILED, _NO_KEY_MESSAGE, retryable=False)

    # 3. Cache.
    key = canonical_key({"tool": TOOL_NAME, **params.model_dump()})
    cached = cache.get(key)
    if cached is not None:
        log_event(_logger, "tool.cache_hit", tool=TOOL_NAME, input=params.model_dump())
        return copy.deepcopy(cached)

    # 4. Provider call.
    started = time.monotonic()
    try:
        offers = await client.search(params)
    except ToolError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if e.code is ErrorCode.NO_RESULTS:
            log_event(_logger, "tool.no_results", tool=TOOL_NAME,
                      input=params.model_dump(), elapsed_ms=elapsed_ms)
            return error_response(ErrorCode.NO_RESULTS, e.message, retryable=False)
        log_event(_logger, "tool.upstream_error", tool=TOOL_NAME,
                  code=e.code.value, elapsed_ms=elapsed_ms,
                  level=_LEVEL_FOR_CODE.get(e.code, logging.WARNING))
        return error_response(e.code, e.message, retryable=e.retryable)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    result = SearchHotelsResult(results=offers).model_dump(mode="json")
    cache.set(key, result)
    log_event(_logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
              count=len(offers), elapsed_ms=elapsed_ms, cache_hit=False)
    return copy.deepcopy(result)
