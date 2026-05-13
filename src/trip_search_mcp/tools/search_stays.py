"""The `search_stays` tool function.

Same orchestration shape as `search_flights`: validate → cache → call →
envelope. Difference: this tool needs a SerpAPI key. The MCP server starts
WITHOUT requiring `SERPAPI_KEY` — flights work key-free — so the stays
client may be None at call time. We return a structured auth_failed envelope
in that case rather than crashing the server or the call.

Phase 1 added `category` (hotels / vacation_rentals / all). When `all`,
the client fans out to two parallel SerpAPI calls and merges the
results — see `serpapi_hotels_backend/client.py::_search_merged`.
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
from trip_search_mcp.models import SearchStaysInput
from trip_search_mcp.serpapi_hotels_backend.client import SerpAPIHotelsClient

TOOL_NAME = "search_stays"

_LEVEL_FOR_CODE = {
    ErrorCode.AUTH_FAILED: logging.ERROR,
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

_NO_KEY_MESSAGE = (
    "SERPAPI_KEY is not set. Stay search requires a SerpAPI key "
    "(free tier 100 searches/month at serpapi.com). "
    "Set SERPAPI_KEY in your MCP client's environment and restart."
)

TOOL_DESCRIPTION = """\
Search Google's hotel AND vacation rental listings for a city, date range, and party size, returning a ranked unified list of available places to stay.

Returns ranked stay offers — each with name, photos, star rating (hotels only), review score, price (per-night and total), top amenities, GPS coordinates, a category badge (`"hotel"` or `"vacation_rental"`), per-property OTA price comparison via `sources`, and a Google Hotels deep link. Does NOT book — the booking_url opens the specific property's Google Hotels entity page with the user's check-in/check-out pre-filled, where they can click through to a booking partner.

**`category` selector**:
- `"all"` (default) — fans out to TWO SerpAPI calls in parallel: one for hotels, one for vacation rentals. Merges, dedupes, sorts. Latency is ~3s (parallel, not summed). Costs 2 SerpAPI calls instead of 1 per query — burns SERPAPI quota twice as fast.
- `"hotels"` — only hotel-class properties. One SerpAPI call.
- `"vacation_rentals"` — only short-term rentals. One SerpAPI call. NOTE: Google aggregates rentals from OTAs (Booking.com, Hotels.com, Bluepillow.com, Vrbo.com when available). **Airbnb is NOT in Google's aggregation** and will not appear in `sources` or results.

**`sources`** is a per-offer list of `(name, price_per_night)` entries showing the same property listed across different booking partners. Empty list for hotels in the current data (SerpAPI doesn't surface partner prices for hotels in our queries). Populated for vacation rentals.

Prices come back in **EUR** by default (matches the flights tool's typical response currency for European-IP users). Pass `currency` (ISO 4217, e.g. `"USD"`, `"JPY"`, `"GBP"`) to override per call. The `currency` field on each offer reflects what was actually requested.

**Filter scoping** (important — the wrong filter on the wrong category is silently dropped):
- `min_rating` (1-5 stars) applies only to hotels. When `category="all"`, it filters the hotel side; vacation rentals pass through unfiltered (they have no hotel class).
- `min_bedrooms` and `min_bathrooms` apply only to vacation rentals. Filter the rental side; hotels pass through.
- `min_review_score`, `max_price_per_night`, `required_amenities`, `sort_by`, `max_results`, `currency` apply uniformly.

**`address` is always null on offers** — SerpAPI's google_hotels list endpoint doesn't carry per-property addresses. Use `latitude`/`longitude` for location.

The review score is **Google's native 0-5 scale** (e.g., 4.6 / 5), NOT a 0-10 scale.

`sort_by` accepts: `BEST` (preserve SerpAPI's returned order; for the merged path this falls back to price-ascending as the tie-breaker since neither response has a globally meaningful rank), `PRICE_LOW`, `PRICE_HIGH`, `RATING` (star rating descending; hotels-only signal), `REVIEW_SCORE` (review_score descending, review_count tie-break).

PRE-CALL ELICITATION: Before calling this tool, confirm with the user:

- **Type of stay** (`category`): default to `"all"` unless the user signals "hotel" / "rental" / "Airbnb" / "vacation rental" / "apartment" / "STR". "Find me a place to stay in Lisbon" stays at `"all"`. "Find me a nice hotel in Lisbon" is `"hotels"`. "Find me a rental in Lisbon" is `"vacation_rentals"`.
- **Location**: specific city or neighborhood — "Tampere" works, "Notting Hill, London" works, "somewhere in Europe" does not. Ask if vague.
- **Check-in and check-out dates**: both required and check_out must be strictly after check_in. Confirm UTC-today or later.
- **Party size**: adults, children, and number of rooms. Default is 2 adults / 0 children / 1 room — don't assume; ask if not stated.
- **Budget**: any per-night ceiling? If the user said "cheap" or "affordable", ask for a concrete number to set max_price_per_night.
- **Must-have amenities**: wifi, breakfast, parking, gym, pool, pet-friendly? Don't assume; ask.
- **Star rating or review score floor**: "at least 4 stars", "well-reviewed (8+)"? Map to min_rating or min_review_score (remember review_score is 0-5, so "8+" should become min_review_score=4.0 or you should ask for clarification).
- **Rental size**: if the user mentioned bedrooms or bathrooms ("a 2-bedroom apartment"), set `min_bedrooms` / `min_bathrooms`. These constrain the vacation-rental side only.
- **Sort priority**: cheapest first, highest-rated, best location? Map to sort_by.
- **Currency**: infer from the user's stated location or budget. "I'm in Tokyo, budget ¥30000/night" → `currency="JPY"`, `max_price_per_night=30000`. "$200/night in NYC" → `currency="USD"`. Default `"EUR"` if the user gives no signal. Always pass the currency that matches the units the user spoke in for `max_price_per_night` — mixing currencies silently corrupts the budget filter.

RESULT PRESENTATION: When returning 2+ stays, render them as an interactive artifact with one card per offer. Each card shows:

- The stay name, prominent and large at the top of the card (it carries the card's visual hierarchy in the absence of a photo).
- A small **category badge** at the top: `Hotel` or `Vacation rental`, taken from the `category` field.
- Star rating (hotels only — render as filled stars if you can) and review_score with review_count: "4.6 / 5  (686 reviews)".
- For vacation rentals, surface **bedrooms / bathrooms / sleeps** inline if present (e.g. "2 BR · 2 BA · sleeps 6").
- Price per night with the total alongside in smaller text, in the response currency.
- For offers with a non-empty `sources` array, show "from €X on [cheapest source]" with a smaller "also on [other sources]" note when 2+ sources are present.
- Top 3-4 amenities pulled from the `amenities` list.
- Short description if present (hotels only — rentals leave this null and surface essential_info via the bedrooms/bathrooms/sleeps fields above).
- A "Book on Google Hotels" button linking to `booking_url`, opening in a new tab.

Do NOT render the `images` field as photo elements. Hotel image CDNs (Google's signed `gps-cs-s` URLs, hotelbeds, trvl-media, bstatic, giata) use hotlink protection that breaks these URLs outside their intended hosts; broken images degrade the card more than missing photos. The `images` field stays on the response model for future use (e.g., a server-side image proxy layer), but card rendering should be text-only. Compensate for the missing visual hierarchy by making the stay name larger and prominent at the top of each card.

If the response has a non-empty `warnings` array, surface them verbatim above the cards (e.g., "Note: vacation rental data was unavailable for this query; showing hotels only."). Do NOT silently swallow them.

Sort cards by the same `sort_by` the user requested. For a single result, prose is fine."""

_logger = logging.getLogger("trip_search_mcp")


async def search_stays(
    *,
    client: SerpAPIHotelsClient | None,
    cache: TTLCache,
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
    raw_input = dict(
        location=location, check_in_date=check_in_date, check_out_date=check_out_date,
        adults=adults, children=children, rooms=rooms,
        category=category,
        min_rating=min_rating,
        min_bedrooms=min_bedrooms, min_bathrooms=min_bathrooms,
        min_review_score=min_review_score,
        max_price_per_night=max_price_per_night,
        required_amenities=required_amenities,
        sort_by=sort_by, max_results=max_results,
        currency=currency,
    )

    # 1. Input validation.
    try:
        params = SearchStaysInput.model_validate(raw_input)
    except ValidationError as e:
        first_error = e.errors()[0]
        field_path = ".".join(str(p) for p in first_error.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first_error.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first_error.get("msg"))
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    # 2. Lazy auth check. If the server started without SERPAPI_KEY, the
    #    stays client wasn't built. Surface that as a clean auth_failed
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

    # 4. Provider call. Returns SearchStaysResult (with possible warnings on
    #    the partial-failure path). The client raises ToolError only when
    #    the user-facing call should fail entirely (both sides down, or
    #    single-mode failure).
    started = time.monotonic()
    try:
        result_model = await client.search(params)
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
    result = result_model.model_dump(mode="json")
    cache.set(key, result)
    log_event(
        _logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
        count=len(result_model.results),
        warnings=len(result_model.warnings),
        elapsed_ms=elapsed_ms, cache_hit=False,
    )
    return copy.deepcopy(result)
