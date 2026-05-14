"""The `search_activities` tool function.

Wraps SerpAPI's Tripadvisor engine with `ssrc=A` (Things to Do).
Returns a mixed list of sights and bookable experiences.

Requires SERPAPI_KEY (lazy-fail like search_stays / search_events).
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
from trip_search_mcp.models import SearchActivitiesInput, SearchActivitiesResult
from trip_search_mcp.tripadvisor_backend.client import SerpAPITripadvisorClient

TOOL_NAME = "search_activities"

_LEVEL_FOR_CODE = {
    ErrorCode.AUTH_FAILED: logging.ERROR,
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

_NO_KEY_MESSAGE = (
    "SERPAPI_KEY is not set. Activity search requires a SerpAPI key "
    "(free tier 100 searches/month at serpapi.com). "
    "Set SERPAPI_KEY in your MCP client's environment and restart."
)

TOOL_DESCRIPTION = """\
Search Tripadvisor's "Things to Do" (sights + bookable experiences/tours) for a location, optionally filtered by free-text query, place type, and minimum rating.

DISTINCT FROM `search_events`: activities are ongoing (visit a museum, take a cooking class anytime); events are date-specific (a concert on June 21). Use this for "what should I do in X"; use `search_events` for "what's happening while I'm there".

USE THIS TOOL WHEN:
- The user asks "what should I do in X" / "things to do in X" / "tours in X"
- They name an activity type ("cooking classes", "boat tours", "museums", "wine tasting")
- They want recommendations based on their preferences

Inputs:
- `location` (string, required) — free-text city or neighborhood. Combined with `query` into a single Tripadvisor search.
- `query` (string, optional) — free-text filter on activity type. Natural language works: "cooking class", "boat tours", "wine tasting", "free walking tour".
- `place_type_filter` (enum, optional, default `"both"`) — one of `"sights"` (free attractions like museums, viewpoints), `"experiences"` (bookable tours), or `"both"` (default).
- `min_rating` (float, optional) — minimum review score 0.0-5.0. Results without a rating are excluded when this is set.
- `max_results` (int, optional, default 15) — 1-50.

Returns `ActivityOffer` entries each with:
- `offer_id` — Tripadvisor's `place_id`, stable per activity. Use this to drill in via `get_activity_details` (when implemented).
- `name` — activity name.
- `activity_type` — `"sight"` (free, non-bookable) or `"experience"` (bookable tour).
- `rating`, `review_count` — 0-5 scale (Tripadvisor's native).
- `description` — short prose (often missing on generic city searches; usually present on specific-activity searches).
- `location` — text "City, Country".
- `thumbnail` — URL (NOT hotlink-safe — don't render as a photo element).
- `highlighted_review` — `{text, mention_count}` — a relevant review snippet.
- `booking_url` — Tripadvisor listing URL. For experiences, this is the path to Viator tickets; for sights, it's the info page.

**No coordinates and no price.** Tripadvisor's search endpoint surfaces neither. Use `get_activity_details(offer_id)` (when implemented) to get price + duration + a direct Viator URL for bookable experiences.

PRE-CALL ELICITATION — three branches:

**Branch 1: User names a specific activity type.**
"Find cooking classes in Lisbon." → `query="cooking class"`. Search immediately.

**Branch 2: User asks for a recommendation.**
"What should I do in Lisbon?" — before searching, infer the user's interests from conversation context + your own memory of them ("they love food and wine", "they're into history"). Bake the interest into `query`. **NOTE:** The MCP tool does NOT read Claude's memory — you (Claude) do the inference and pass the resulting query string. If memory yields nothing actionable, fall to Branch 3.

**Branch 3: User is vague and you have no preference signal.**
"Things to do in Lisbon?" with empty conversation context. Ask ONE clarifying question: *"Any particular interest — food, history, outdoors, nightlife?"* Then search.

RESULT PRESENTATION:
- Card-based artifact, one card per result.
- For Branch 2 (memory-driven), preamble: *"Based on your interest in food and wine, here are top-rated experiences in Lisbon."* — makes the inference legible.
- Card content: name, activity_type badge (Sight / Experience), rating + review_count, location, the `highlighted_review.text` as a 1-line testimonial, "Find on Tripadvisor" button → `booking_url`.
- Do NOT render `thumbnail` as a photo element (Tripadvisor's CDN hotlink-protects). Same no-photos rule as stays/events.
- For a single result, prose is fine."""

_logger = logging.getLogger("trip_search_mcp")


async def search_activities(
    *,
    client: SerpAPITripadvisorClient | None,
    cache: TTLCache,
    location: str,
    query: str | None = None,
    place_type_filter: str = "both",
    min_rating: float | None = None,
    max_results: int = 15,
) -> dict[str, Any]:
    raw_input = dict(
        location=location, query=query,
        place_type_filter=place_type_filter,
        min_rating=min_rating, max_results=max_results,
    )

    # 1. Input validation.
    try:
        params = SearchActivitiesInput.model_validate(raw_input)
    except ValidationError as e:
        first = e.errors()[0]
        field_path = ".".join(str(p) for p in first.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first.get("msg"))
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
    result = SearchActivitiesResult(results=offers).model_dump(mode="json")
    cache.set(key, result)
    log_event(_logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
              count=len(offers), elapsed_ms=elapsed_ms, cache_hit=False)
    return copy.deepcopy(result)
