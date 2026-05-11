# Flight Search MCP: Migration to `fli` Backend

**Owner:** Nophil
**Status:** Draft, ready for Claude Code
**Last updated:** 2026-05-12

---

## Context

Third data source for this MCP (Amadeus → SerpAPI → fli). The architectural isolation pattern that survived the first migration carries this one too. Most of the surface area stays.

**Current state:** SerpAPI's Google Flights endpoint, 100 free searches/month, requires `SERPAPI_KEY`, round-trip costs N+1 upstream calls per query.

**New state:** `punitarani/fli` Python library (PyPI: `flights`). Direct Google Flights API access via reverse-engineered endpoints. MIT licensed, v0.8.1 released April 2026, actively maintained.

**Trade-offs:**

- Removes the quota limit entirely
- Removes the third-party API dependency
- Adds a maintenance risk: if Google changes their internal API, fli breaks until upstream ships a fix
- Acceptable for personal use given fli's release cadence (10 releases in 2026)

**Bonus:** fli ships a `SearchDates` capability that maps to your planned `find_cheapest_dates` feature. We pick that up in Phase 2 for free.

---

## Phase 0: Verify fli library

Gates. Do not start Phase 1 until each is green.

### 0.1 Install and call SearchFlights

Install: `uv add flights` or `pip install flights`.

Write `scripts/verify_fli.py` that calls `fli.search.SearchFlights` for HEL → IAD, departure 2026-05-18, return 2026-05-29, 1 adult, Economy. Save the response (or `model_dump` of FlightResult objects) to `tests/fixtures/fli_hel_iad_success.json`. Print a structure summary.

**STOP for review.**

Four things to verify before Phase 1:

1. **Round-trip representation.** fli might return one FlightResult with all legs in a single list, or two separate FlightResults (outbound and inbound). The normalize layer's design depends on this.
2. **Datetime format.** fli uses Python `datetime`. Confirm whether they're timezone-naive (local airport time) so the existing ISO 8601 local-time contract still holds when we serialize.
3. **Currency.** fli's filter signatures don't appear to expose currency selection. Verify what currency the price comes back in. If USD, keep your `currency` input param for forward compatibility and document that it's currently ignored.
4. **Cabin class enum.** fli uses `SeatType` (ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST). Confirm 1:1 mapping with your existing cabin_class strings.

### 0.2 Call SearchDates

Same script, additional call: `fli.search.SearchDates` for HEL → IAD across a 7-day departure window with 11-day trip duration. Save as `tests/fixtures/fli_hel_iad_dates.json`.

Gives you the response shape for Phase 2 before any code lands.

### 0.3 Sanity-check the upstream project

- Open https://github.com/punitarani/fli/issues. Search for "broken," "Google," "500," "API changed." If there's a pattern of multi-week outages, reconsider.
- Note response latency in 0.1. fli claims fast; verify it's actually faster than SerpAPI on the same query.

If anything in 0.1, 0.2, or 0.3 is unexpected, pause and re-evaluate.

---

## Phase 1: Swap the data layer

Goal: replace `src/flights_mcp/serpapi/` with `src/flights_mcp/fli_backend/`. The `search_flights` tool gains three filter params it didn't have, otherwise its surface stays.

### In scope

**New code:**

- `src/flights_mcp/fli_backend/` package containing:
  - `client.py`: thin wrapper around `fli.search.SearchFlights` and (Phase 2) `SearchDates`, with the injectable pattern so tests can substitute fixtures
  - `normalize.py`: adapts `FlightResult` → existing `FlightOffer` model

**Input contract additions to `search_flights`:**

| New param | Type | Required | Notes |
|---|---|---|---|
| `departure_window` | string \| null | no | Format "HH-HH" e.g. "6-20" for 6am to 8pm. Local to departure airport. |
| `max_stops` | enum string \| null | no | One of: ANY, NON_STOP, ONE_STOP, TWO_PLUS_STOPS. Defaults to ANY. |
| `airlines` | list[string] \| null | no | IATA codes to filter to. Empty/null means no filter. |

**Input contract removals:**

- `non_stop_only` boolean (superseded by `max_stops`)

**Tool description updates:**

- Document the three new params in the existing description block
- Remove SerpAPI-specific notes (the 100/month quota line, the round-trip N+1 explanation, the cache-as-protection note)
- Keep PRE-CALL ELICITATION block. Update the connection-tolerance line to reference `max_stops` explicitly.
- Keep RESULT PRESENTATION block. No changes.
- Keep "times are local to the airport, do not do timezone math" block. No changes.

**Error contract updates:**

| Old code | New status | Notes |
|---|---|---|
| `no_results` | Kept | fli returns empty list when nothing found |
| `invalid_input` | Kept | Pydantic validation at MCP boundary |
| `rate_limited` | Kept | fli's `ratelimit` lib + Google's 429s |
| `upstream_error` | Kept | Network failures, 5xx, parse errors, Google API changes |
| `quota_exceeded` | Removed | No quota concept |
| `auth_failed` | Removed | No auth |

**Configuration:**

- Remove `SERPAPI_KEY` from `.env.example` and from `_require_env` call in `server.py`
- No new required env vars

**Dependency changes:**

- Remove the SerpAPI library/requests dependency from `pyproject.toml`
- Add `flights` (the PyPI package name for fli)

### Out of scope

- Changes to `FlightOffer`, `Itinerary`, `Segment` model shape (preserve backward compatibility)
- `booking_url` synthesis (kept from previous work; regression-check that it still populates)
- The `search_cheapest_dates` tool (Phase 2)
- Currency conversion beyond passing through whatever fli returns
- Multi-airport / city-code support

### Notes for normalize.py

- `total_price` maps from `FlightResult.price`. Verify in Phase 0 whether this is per-passenger or total; multiply by `passengers` if per-passenger
- `outbound` and `inbound` Itineraries: split `legs[]` by which slice each belongs to (determined in Phase 0)
- `Itinerary.duration`: sum leg durations + layovers, format as ISO 8601 (`PT10H30M`)
- `Segment.departure_time_local` and `arrival_time_local`: format datetimes as ISO 8601 strings via `isoformat()`, assuming naive datetimes per local-airport convention
- `offer_id`: fli doesn't return a booking_token. Generate a stable hash: SHA256 of `(sorted_airline_codes, sorted_flight_numbers, departure_date, return_date_or_empty)`. Document that this is stable per query but not globally meaningful, and is suitable for the future `flight_price_confirm` tool only within a result set
- `booking_url`: regenerated the same way as before (Google Flights URL with the search pre-filled)

### Cleanup

- Delete `src/flights_mcp/serpapi/` after `fli_backend/` is verified working end-to-end
- Tag the last SerpAPI commit (`git tag pre-fli-migration`) before deletion in case you need to revert

---

## Phase 2: Add `search_cheapest_dates` tool

Goal: expose `fli.search.SearchDates` as a second MCP tool that returns date-price pairs across a flexible range.

### Tool signature

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `origin` | string | yes | IATA airport code |
| `destination` | string | yes | IATA airport code |
| `start_date` | string | yes | ISO date, earliest acceptable departure |
| `end_date` | string | yes | ISO date, latest acceptable departure |
| `trip_duration` | integer | conditional | Days. Required if `is_round_trip` is true. |
| `is_round_trip` | boolean | no | Default false |
| `passengers` | integer | no | Default 1 |
| `cabin_class` | enum | no | Same as `search_flights` |
| `max_stops` | enum | no | Same as `search_flights` |
| `departure_window` | string | no | "HH-HH" format |
| `airlines` | list[string] | no | IATA codes |

### Output shape

New model `DatePriceOffer`:

| Field | Type | Notes |
|---|---|---|
| `departure_date` | string | ISO date |
| `return_date` | string \| null | ISO date if round-trip, null otherwise |
| `price` | number | In `currency` |
| `currency` | string | ISO 4217 |

Tool returns `{ results: list[DatePriceOffer] }` sorted by price ascending. Same error envelope as `search_flights`.

### Tool description

Includes a clear use-case disambiguation block:

> Use `search_cheapest_dates` when the user is flexible on travel dates and wants to know which dates are cheapest across a range. Returns a list of (departure_date, return_date, price) entries sorted cheapest first.
>
> Use `search_flights` when the user has specific dates and wants flight details, airlines, times, layovers, and bookable offers for those dates.

Also includes PRE-CALL ELICITATION (adapted for date-flex queries: confirm the date range, confirm trip duration if round-trip, confirm whether weekends/weekdays matter) and RESULT PRESENTATION blocks (render as a date grid or sorted list, with the cheapest dates highlighted).

---

## Phase 3: Tests and cleanup

### Test updates

- Rewrite existing tests to use the new fixtures: `fli_hel_iad_success.json`, `fli_hel_iad_dates.json`
- Add fixtures for error states: `fli_no_results.json`, `fli_rate_limited.json`, `fli_upstream_error.json`
- The injectable client pattern stays. Tests substitute mock SearchFlights/SearchDates instances that return fixture data
- Add a new test file for `search_cheapest_dates` that asserts:
  - Returns `DatePriceOffer` list sorted by price ascending
  - `return_date` is null when `is_round_trip` is false
  - Invalid combinations (round-trip without `trip_duration`) return `invalid_input`
- All previous acceptance criteria still hold: booking_url populated, time format ISO 8601, no exceptions leak

### Final cleanup

- Remove `serpapi` from `pyproject.toml`
- Remove `SERPAPI_KEY` from `.env.example`
- Update README: remove all SerpAPI references, document the two MCP tools, document fli dependency
- Verify MCP Inspector shows both tools with clean descriptions, no leftover SerpAPI language

---

## Acceptance criteria

Migration is done when:

1. `fastmcp run server.py` starts cleanly with no `SERPAPI_KEY` in `.env`
2. MCP Inspector shows both `search_flights` and `search_cheapest_dates` with full descriptions
3. `search_flights(origin="HEL", destination="IAD", departure_date="2026-05-18", return_date="2026-05-29", adults=1)` returns at least one offer with the same `FlightOffer` output shape as before
4. The three new params (`departure_window`, `max_stops`, `airlines`) accept values and filter results
5. `search_cheapest_dates(origin="HEL", destination="IAD", start_date="2026-05-15", end_date="2026-05-25", trip_duration=11, is_round_trip=true)` returns a sorted list of `DatePriceOffer`
6. Error states match the revised contract; no exceptions leak to Claude
7. All tests pass against fixtures; no live API calls in the test suite
8. `src/flights_mcp/serpapi/` no longer exists in the codebase
9. PRE-CALL ELICITATION and RESULT PRESENTATION blocks still in both tools' descriptions
10. `FlightOffer.booking_url` still populated on every offer (regression check from previous phase)

---

## Risks and known issues

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| fli's reverse-engineered API breaks after a Google change | Medium | High | Accept for personal use. Tag `pre-fli-migration` commit before deletion so you can revert in 10 minutes if it breaks the day after migration. |
| fli's response shape doesn't match assumptions (datetimes, round-trip structure, currency) | Medium | Medium | Phase 0 verification catches this before any migration code. |
| Currency control isn't supported by fli | High | Low | Document the limitation in the tool description. Optionally add post-hoc FX conversion as a future enhancement. |
| Rate limit issues calling Google directly | Low | Medium | fli has built-in `ratelimit` + `tenacity`. Should handle 429s gracefully. Surface as `rate_limited` to Claude. |
| Upstream project goes quiet | Low | Medium | 963 stars, 10 releases this year, multiple contributors. Real but bounded risk. |

---

## How to hand this to Claude Code

Fresh terminal session:

> Read MIGRATION-FLI-SPEC.md from the project root. We're starting Phase 0. Install `flights` from PyPI, write `scripts/verify_fli.py` per the spec, run it for HEL-IAD round-trip on May 18 to May 29, and also call SearchDates for the same route across May 15 to May 25 with 11-day duration. Save both fixtures. Then stop and show me the structure summary before any migration code gets written.

Pause between phases. Same pattern as the SerpAPI migration: catch shape mismatches early, regret nothing.
