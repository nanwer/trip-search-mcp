"""The `search_events` tool function.

Wraps SerpAPI's google_events engine. Returns time-bound events
(concerts, festivals, sports, conferences) — distinct from
`search_activities` which covers ongoing attractions.

Requires SERPAPI_KEY (lazy-fail like search_stays / get_stay_details).
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
from trip_search_mcp.models import SearchEventsInput, SearchEventsResult
from trip_search_mcp.serpapi_events_backend.client import SerpAPIEventsClient

TOOL_NAME = "search_events"

_LEVEL_FOR_CODE = {
    ErrorCode.AUTH_FAILED: logging.ERROR,
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

_NO_KEY_MESSAGE = (
    "SERPAPI_KEY is not set. Event search requires a SerpAPI key "
    "(free tier 100 searches/month at serpapi.com). "
    "Set SERPAPI_KEY in your MCP client's environment and restart."
)

TOOL_DESCRIPTION = """\
🎯 **RENDERING DIRECTIVE — READ FIRST.** When this tool returns 2+ events, you MUST present them as an **interactive HTML/React artifact** (Artifacts block, not flowing prose). Each event is a visually distinct CARD with one "Tickets on [vendor]" **button** per `ticket_sources` entry (or a single button on `ticket_url` if no extras), styled as HTML buttons, NOT inline markdown links. If the call is part of a larger trip plan, the plan itself should be an artifact containing event cards. Single-result responses may use prose.

Search Google for time-bound events — concerts, festivals, sports games, comedy shows, conferences — happening at a location, optionally filtered by event type and date range.

DISTINCT FROM `search_activities`: events are time-bound (a specific date or window); activities (tours, attractions) are ongoing. Use this tool for "what's on while I'm there"; use `search_activities` for "what should I do".

USE THIS TOOL WHEN:
- The user asks "what's happening in X" / "events in X" / "any concerts in X" / "is BTS playing anywhere I'm going"
- They mention a specific event type ("concerts", "festivals", "sports", "comedy", "theatre")
- They're planning a trip and want time-bound options to anchor the dates around

Inputs:
- `location` (string, required) — free-text city. Combined with `query` into the SerpAPI search string.
- `query` (string, optional) — event-type filter. Examples: `"concerts"`, `"festivals"`, `"sports"`, `"comedy"`, `"theatre"`, or a specific artist/team (`"BTS"`, `"Coldplay"`).
- `date_filter` (enum, optional) — one of `"today"`, `"tomorrow"`, `"week"`, `"weekend"`, `"next_week"`, `"month"`, `"next_month"`. SerpAPI's named-range filter; do NOT pass arbitrary date strings. If the user wants a specific calendar month, bake the month name into `query` instead (e.g. `query="concerts June 2026"`).
- `max_results` (int, optional, default 15) — 1-50.

Returns up to `max_results` `EventOffer` entries, each with:
- `offer_id` — stable hash for downstream reference
- `title` — event name
- `start_date_raw` — SerpAPI's `"Jun 21"` style string (month + day, no year — `when_text` carries the year)
- `when_text` — full formatted display string: `"Fri, Jul 17, 8 – 11 PM GMT+2"`
- `venue_name`, `venue_rating`, `venue_review_count` — venue info if available
- `address` — flattened single string ("B.Leza Club, Cais do Gás 1, Lisbon, Portugal")
- `description` — short text from Google
- `thumbnail`, `image` — URLs (NOT hotlink-safe — same rule as stays, don't render as photo elements)
- `ticket_url` — primary deep-link to the ticket vendor (Viagogo, Eventbrite, Spotify Concerts, venue site — varies per event)
- `ticket_sources` — list of additional ticket vendors with `{source, link}` per entry. Surface all of these as "Tickets on X" buttons so the user can comparison-shop.

PRE-CALL ELICITATION:
- If the user names an event type, set `query`. "Concerts in Lisbon" → `query="concerts"`.
- If they mention a relative date ("this weekend", "next week", "this month"), set `date_filter` to the matching enum.
- If they mention a specific calendar month + year, bake it INTO the query string ("concerts June 2026") instead of using `date_filter`.
- If they're vague ("things happening in Lisbon"), call with no `query` and no `date_filter` — default upcoming events.

RESULT PRESENTATION: card-based artifact, one card per event. Lead with `title` + `when_text` + `venue_name`. Show "Tickets on [source]" buttons (one per `ticket_sources` entry); for events with no `ticket_sources`, surface the primary `ticket_url` as a single button. Do NOT render `thumbnail`/`image` as photo elements (same hotlink-protection issue as stays). For a single result, prose is fine."""

_logger = logging.getLogger("trip_search_mcp")


async def search_events(
    *,
    client: SerpAPIEventsClient | None,
    cache: TTLCache,
    location: str,
    query: str | None = None,
    date_filter: str | None = None,
    max_results: int = 15,
) -> dict[str, Any]:
    raw_input = dict(
        location=location, query=query,
        date_filter=date_filter, max_results=max_results,
    )

    # 1. Input validation.
    try:
        params = SearchEventsInput.model_validate(raw_input)
    except ValidationError as e:
        first = e.errors()[0]
        field_path = ".".join(str(p) for p in first.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first.get("msg"))
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    # 2. Lazy auth check (same pattern as search_stays).
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
    result = SearchEventsResult(results=offers).model_dump(mode="json")
    cache.set(key, result)
    log_event(_logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
              count=len(offers), elapsed_ms=elapsed_ms, cache_hit=False)
    return copy.deepcopy(result)
