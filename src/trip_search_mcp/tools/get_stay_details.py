"""The `get_stay_details` tool function.

Takes a `property_token` from a prior `search_stays` result and returns
rich per-property data — long-form description, per-booking-partner
direct booking URLs, and an expanded `nearby_places` list. Used when
the user wants to drill into a specific property after browsing the
ranked stay list.

NOTE on what's NOT in the response: SerpAPI's property_details endpoint
does NOT carry a postal address (Phase 0 verification confirmed). The
tool surfaces lat/long coordinates and ~14 nearby landmarks as the
location signal instead.
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any

from pydantic import ValidationError

from trip_search_mcp.cache import TTLCache, canonical_key
from trip_search_mcp.errors import ErrorCode, ToolError, error_response
from trip_search_mcp.logging_config import log_event
from trip_search_mcp.models import GetStayDetailsInput
from trip_search_mcp.serpapi_hotels_backend.client import SerpAPIHotelsClient

TOOL_NAME = "get_stay_details"

_LEVEL_FOR_CODE = {
    ErrorCode.AUTH_FAILED: logging.ERROR,
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

_NO_KEY_MESSAGE = (
    "SERPAPI_KEY is not set. get_stay_details requires a SerpAPI key "
    "(free tier 100 searches/month at serpapi.com). "
    "Set SERPAPI_KEY in your MCP client's environment and restart."
)

TOOL_DESCRIPTION = """\
Fetch rich per-property details for a single stay (hotel or vacation rental) the user has already seen in a `search_stays` result.

Takes a `property_token` (copied from any offer in a `search_stays` response) plus the same check_in/check_out dates and party size used for the original search. Returns a single `StayDetails` object — NOT a list.

USE THIS TOOL WHEN: the user has narrowed down to a specific property from a previous `search_stays` result and wants more detail before booking — typical phrasings: "tell me more about [hotel name]", "what's it like inside", "show me the booking options", "what's near it", "is breakfast included".

DO NOT USE THIS TOOL WHEN: the user is still browsing or hasn't specified a property. Use `search_stays` first.

Returns:
- `description`: long-form prose (rentals: usually 1–2 paragraphs; hotels: 1–3 sentences).
- `booking_partners`: list of OTAs offering this property with `link` (direct deep-link to the partner's booking flow), `price_per_night`, `total_price`, `official` (true if the property's own site), `free_cancellation`. **This is the key payload — surface these as prominent "Book on X" buttons.**
- `nearby_places`: up to ~14 entries (airports, transit stations, restaurants, landmarks) each with `name`, `category`, `latitude`, `longitude`. Use to answer "what's nearby" questions.
- `amenities` / `excluded_amenities`: the full lists (no top-3 truncation).
- `check_in_time` / `check_out_time`: e.g. "3:00 PM" / "11:00 AM".
- `star_rating`, `review_score` (0–5), `review_count`, `location_rating`.

**`address` is NOT in the response.** SerpAPI's property_details endpoint doesn't carry a postal address. Use the GPS coordinates + nearby_places to communicate location.

Costs 1 SerpAPI quota call per invocation. Cached aggressively (TTL ~5 min by default) — repeat calls for the same (token, dates) tuple are free.

RESULT PRESENTATION: Render as a single rich card with the booking_partners list prominently displayed (one button per partner, "Book on [name] — €X/night, free cancellation: yes/no"). If the user asked about a specific aspect (location, breakfast, refundability), lead with that. Surface the GPS coordinates on a small map link if you have that capability."""

_logger = logging.getLogger("trip_search_mcp")


async def get_stay_details(
    *,
    client: SerpAPIHotelsClient | None,
    cache: TTLCache,
    property_token: str,
    check_in_date: str,
    check_out_date: str,
    adults: int = 2,
    currency: str = "EUR",
) -> dict[str, Any]:
    raw_input = dict(
        property_token=property_token,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        adults=adults,
        currency=currency,
    )

    # 1. Input validation.
    try:
        params = GetStayDetailsInput.model_validate(raw_input)
    except ValidationError as e:
        first_error = e.errors()[0]
        field_path = ".".join(str(p) for p in first_error.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first_error.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first_error.get("msg"))
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    # 2. Lazy auth check.
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
        details_model = await client.get_property_details(params)
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
    result = details_model.model_dump(mode="json")
    cache.set(key, result)
    log_event(
        _logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
        partners=len(details_model.booking_partners),
        nearby=len(details_model.nearby_places),
        elapsed_ms=elapsed_ms, cache_hit=False,
    )
    return copy.deepcopy(result)
