# Hotels Extension: Add Google Hotels Search to the Existing MCP

**Owner:** Nophil
**Status:** Draft, ready for Claude Code
**Last updated:** 2026-05-13

---

## Context

Eli asked for hotel search alongside the existing flight search. He uses Google Flights heavily for trip planning and wants Google Hotels in the same workflow. Adding a `search_hotels` tool to the existing MCP is the natural next step.

Architectural pattern we're following: same approach as the fli migration. A new provider backend (`fast_hotels_backend/`) mirrors the existing `fli_backend/`, exposes a clean shape via the existing models pattern, and lights up a new MCP tool registered alongside the existing two.

**Architectural decision: keep the repo named `trip-search-mcp` for now. Add hotels as a sibling backend inside it.**

The "right" long-term name might be `travel-mcp` (or similar) once the scope clearly exceeds flights, but a rename right now is one-time disruption: repo URL, Claude Desktop config path, AGENTS.md, memory entries, all need updating. Not blocking value. If a third domain ever gets added (cars, activities, something else), revisit the rename then. For one new tool, pragmatism wins.

**Provider choice: `fast-hotels` first, SerpAPI `google_hotels` as fallback.**

`fast-hotels` is the obvious primary candidate: MIT licensed, explicitly inspired by fast-flights (predecessor to fli), uses the same HTTP-based protobuf approach. If it works, the integration is nearly identical to fli structurally.

Concerns to verify in Phase 0:
- Last GitHub push was around June 2025. fli ships every few weeks; this is potentially stale.
- 0 stars on GitHub. No adoption signal.
- Solo maintainer.

If verification fails, fall back to SerpAPI's `google_hotels` endpoint on the free tier (100 searches/month). Personal use volume is well below that.

---

## Phase 0: Verify fast-hotels library

Gates. Do not start Phase 1 until each is green.

### 0.1 Install and run a real call

Install: `uv add fast-hotels` or `pip install fast-hotels`.

Write `scripts/verify_fast_hotels.py` that:
- Searches hotels in a real city for real dates. Suggested test: Tampere, check-in 2026-06-15, check-out 2026-06-18, 2 adults
- Saves the response (or `model_dump` of result objects) to `tests/fixtures/fast_hotels_tampere_success.json`
- Prints a structure summary including: result count, sample of fields per hotel, what's null

STOP for review.

**Five things to verify before Phase 1:**

1. **Response shape.** What fields does each hotel result expose? Name, price, rating, review score, review count, amenities, address, photos, booking link, anything else.
2. **Price format.** Is it total for the stay, or per-night? Is there both? What currency does it return (likely IP-geolocation based like fli, probably EUR for Nophil).
3. **Date handling.** Does the library accept ISO date strings (`YYYY-MM-DD`) directly, or require date objects? Are dates inclusive or exclusive of checkout?
4. **Sort and filter primitives.** What filters does the library support natively (price range, min rating, amenities, sort order)? Anything we want must be either supported upstream or implemented as a post-filter.
5. **Library health.** Does the test call actually return data, or has Google broken it since June 2025? If broken, switch to SerpAPI for Phase 1.

### 0.2 Sanity-check upstream

- Open https://github.com/jongan69/hotels/issues. Check for "Google broke," "API change," recent activity.
- If no recent activity AND the Phase 0.1 call works, proceed but document the risk in the spec.
- If no recent activity AND the call fails, pivot to SerpAPI immediately.

### 0.3 Confirm assumptions

- Note response latency. fli runs around 10-15 seconds for 30 results; fast-hotels likely similar order of magnitude.
- Confirm the test call returns a plausible number of hotels (more than 5, fewer than 200).
- Spot-check 3 hotels against Google Hotels directly for accuracy. Names, prices, ratings should match within reason.

---

## Phase 1: Add hotels backend and `search_hotels` tool

Goal: a working `search_hotels` MCP tool that returns hotel offers in a clean structured shape. Same architectural pattern as the existing `search_flights`.

### In scope

**New code:**
- `src/trip_search_mcp/fast_hotels_backend/` package:
  - `client.py`: thin wrapper over `fast_hotels.SearchHotels` (or whatever the library names its main class), with the injectable pattern for fixture-driven tests
  - `normalize.py`: adapts the library's response to a new `HotelOffer` model
- `src/trip_search_mcp/tools/search_hotels.py`: the MCP tool function with description and orchestration
- `src/trip_search_mcp/models.py` additions: `HotelOffer` and `SearchHotelsInput`

**New tool surface (`search_hotels` input):**

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `location` | string | yes | City name or area, e.g., "Lisbon" or "Notting Hill, London" |
| `check_in_date` | string | yes | ISO date, YYYY-MM-DD. Today UTC or later. |
| `check_out_date` | string | yes | ISO date, must be after check_in_date |
| `adults` | integer | no | Default 2 (most common case for hotels). Range 1-10. |
| `children` | integer | no | Default 0 |
| `rooms` | integer | no | Default 1 |
| `min_rating` | integer | no | Star rating, 1-5. Default null (no filter) |
| `min_review_score` | number | no | 0-10. Default null |
| `max_price_per_night` | number | no | In whatever currency the library returns. Default null |
| `required_amenities` | list[string] | no | Free-text amenity names. Default null. Post-filter if library doesn't support natively. |
| `sort_by` | enum | no | One of: BEST, PRICE_LOW, PRICE_HIGH, RATING, REVIEW_SCORE. Default BEST. |
| `max_results` | integer | no | Default 10. Max 25. |

**New output shape (`HotelOffer`):**

| Field | Type | Notes |
|---|---|---|
| `offer_id` | string | Stable SHA256 hash of (name, address, check_in_date, check_out_date) |
| `name` | string | Hotel name |
| `check_in_date` | string | Echoed from input |
| `check_out_date` | string | Echoed from input |
| `nights` | integer | Computed |
| `price_total` | number | Total price for the stay in `currency` |
| `price_per_night` | number | Computed if library only returns one form |
| `currency` | string | ISO 4217, whatever the library returns |
| `star_rating` | integer or null | 1-5 stars, if available |
| `review_score` | number or null | 0-10 scale, if available |
| `review_count` | integer or null | Number of reviews, if available |
| `address` | string or null | Full address or neighborhood |
| `latitude` | number or null | If available |
| `longitude` | number or null | If available |
| `amenities` | list[string] | Top amenities, may be empty if library doesn't return them |
| `photo_url` | string or null | Lead photo, single URL |
| `booking_url` | string | Google Hotels search URL with the query pre-filled. Always populated. |

`booking_url` synthesis (parallel to flights):

```
https://www.google.com/travel/hotels?q=Hotels+in+{location}+from+{check_in_date}+to+{check_out_date}
```

URL-encode using `urllib.parse.quote_plus`. The user lands on a Google Hotels search results page; they click through to the specific property and to a booking provider from there.

**Tool description:**

Required blocks (same pattern as `search_flights`):

- A concise summary of what the tool does and what it returns
- **PRE-CALL ELICITATION** block requiring Claude to confirm with the user before calling:
  - Specific location (city + neighborhood if the user has a preference)
  - Check-in and check-out dates
  - Number of adults, children, and rooms (do not assume)
  - Budget per night, if any
  - Must-have amenities (wifi, breakfast, parking, gym, pool, pet-friendly)
  - Star rating or review score floor
  - Sort priority (price, rating, location)
- **RESULT PRESENTATION** block guiding Claude to render results as an interactive artifact with cards. Each card shows photo, name, star rating, review score with count, price per night with total, top 3-4 amenities, and a "Book on Google Hotels" button linking to `booking_url`. Sort cards per the `sort_by` parameter.

**Error contract (same shape as `search_flights`):**

| Code | When |
|---|---|
| `no_results` | Library returns empty list |
| `invalid_input` | Pydantic validation fails (bad dates, etc.) |
| `rate_limited` | Library raises rate-limit error or 429 from upstream |
| `upstream_error` | Network failures, 5xx, parse errors, library exceptions |

**Server registration:**

Register `search_hotels` in `server.py` alongside the existing `search_flights` and `search_cheapest_dates`. Three MCP tools total after this phase.

### Out of scope (deliberately)

- A combined `plan_trip(flights + hotels)` tool. Cross-tool workflows can be Phase 3+ later if the value proves out.
- Booking URL deep-linking to specific properties or specific OTA offers. Same trade-off as flights: would require an extra upstream call per offer; not worth the cost for V1.
- Vacation rentals, Airbnb, Booking.com direct integration. fast-hotels covers Google Hotels which already aggregates these where Google has them. Out of scope to integrate them individually.
- Price monitoring / alerts for hotels. Same future-work bucket as the flights monitoring layer.
- Renaming the repo to `travel-mcp`. Deferred until the project scope clearly justifies it.

---

## Phase 2: Tests and cleanup

### Tests

- Mirror the fli_backend test layout for fast_hotels_backend
- Required fixtures: `fast_hotels_success.json` (from Phase 0), `fast_hotels_empty_results.json`, `fast_hotels_rate_limited.json`, `fast_hotels_upstream_error.json`
- Unit tests:
  - `normalize.py` correctly maps library response to `HotelOffer`
  - `offer_id` hash is stable and unique per (name, address, dates)
  - Date validation: check_in_date in future, check_out_date after check_in_date
  - Required amenity post-filter works if implemented at our layer
- Orchestration tests:
  - `search_hotels` returns sorted list per sort_by parameter
  - Error states return structured errors, never raise
- End-to-end fixture test:
  - Same MockSearcher pattern as fli_backend; tests run without hitting live API

### Cleanup

- Update README to document the new tool
- Update `.env.example` if any new env vars are introduced (likely none for fast-hotels; if pivoting to SerpAPI, document the key)
- Verify all three tools show in MCP Inspector with full descriptions

---

## Acceptance criteria

Phase 1 and 2 done when:

1. `fastmcp run server.py` starts cleanly with no new required env vars
2. MCP Inspector shows three tools: `search_flights`, `search_cheapest_dates`, `search_hotels`
3. `search_hotels(location="Tampere", check_in_date="2026-06-15", check_out_date="2026-06-18", adults=2)` returns at least one `HotelOffer` with the documented shape
4. Sort order respects the `sort_by` parameter (verifiable by calling with two different sort_by values and checking the first result differs)
5. All filter parameters either apply server-side (passed to fast-hotels) or post-filter in normalize (documented per-param in code comments)
6. `HotelOffer.booking_url` is populated on every offer, never null, never empty
7. Error states match the contract, no exceptions leak to Claude
8. PRE-CALL ELICITATION and RESULT PRESENTATION blocks present in the tool description
9. All existing tests still pass (existing 135 from flights work)
10. New tests pass and run against fixtures only, no live API calls in test suite

---

## Risks and known issues

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `fast-hotels` is dead or partially broken | Medium-High | High (would force pivot to SerpAPI mid-build) | Phase 0 catches this before any production code lands. If broken, restart Phase 1 against SerpAPI; the structural pattern is the same. |
| Library response shape doesn't carry all the fields we want | Medium | Low-Medium | Make fields nullable. Document what's missing. Don't fail offers because of partial data. |
| Currency control isn't supported | High | Low | Same as flights; surface whatever the library returns in `currency`. Document in the tool description. |
| Google Hotels rate-limits aggressively | Medium | Medium | Map to `rate_limited` cleanly. Implement basic backoff in the client wrapper. |
| The `required_amenities` filter is mushy due to free-text matching | Medium | Low | Post-filter on substring match (case-insensitive). Document as "best effort" in the tool description. |
| fast-hotels has 0 GitHub stars; might be untested in the wild | High | Medium | Pin to a specific version in pyproject.toml. Tag pre-hotels commit (`pre-hotels-extension`) before deletion so revert is fast. |

---

## How to hand this to Claude Code

In a fresh session:

> Read HOTELS-EXTENSION-SPEC.md from the project root. We're starting Phase 0. Install `fast-hotels` from PyPI, write `scripts/verify_fast_hotels.py` per the spec, run it for Tampere 2026-06-15 to 2026-06-18 with 2 adults, save the fixture, and stop. Show me the structure summary, the five Phase 0 verification points, and let me decide whether to proceed with fast-hotels or pivot to SerpAPI before any backend code gets written.

Pause between phases. Same pattern: shape mismatches caught at fixture time are 10x cheaper to fix than after the normalizer is written.
