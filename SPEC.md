# Flight Search MCP: Phase 1 Functional Spec

**Owner:** Nophil
**Status:** Approved, ready for execution
**Last updated:** 2026-05-11

## Phase 0: Prerequisites (do these before writing any code)

These are gates. Do not start Phase 1 until each one is green.

### 0.1 Verify Amadeus test environment covers HEL-IAD

Amadeus test environment uses a cached subset of real data. Many origin-destination pairs return empty results. Confirm HEL-IAD is in the cache before building.

**Check:** Sign up for Amadeus Self-Service. From the test environment using a tool like Postman, call Flight Offers Search with:
- origin: HEL
- destination: IAD
- departure date: 2026-05-18
- return date: 2026-05-29
- adults: 1

**Pass criteria:** API returns at least one flight offer with non-empty `data[]`.

Record this response as `tests/fixtures/hel_iad_round_trip.json` (or whichever route is used). This becomes the first fixture and the basis for all Phase 1 dev work. All tool implementation runs against this fixture, not against the live API.

Also verify in the same response: the actual format of `segments[].departure.at` and `segments[].arrival.at` values. The spec assumes ISO 8601 with no offset (local airport time). If Amadeus returns offsets, the normalize step needs an explicit decision documented here before Phase 1 starts.

**If HEL-IAD fail:** three options, in order of preference:
1. Pick any known-good test route (any pair in amadeus4dev/data-collection's cached list), record that response as the fixture, build the entire Phase 1 pipeline against fixtures, repoint at HEL-IAD when production credentials are available. Decouples dev velocity from Amadeus's approval queue.
2. Apply for production credentials in parallel and continue with the fixture-driven approach.
3. Move directly to production. Approval typically takes 1 to 7 business days. Plan for a week.

### 0.2 Decide auth path for Phase 2

Three options, with downstream implications:

| Path | Where MCP works | Effort | Status |
|------|-----------------|--------|--------|
| stdio | Claude Desktop, Claude Code | Low | Stable |
| HTTP + static bearer | Claude Code, Anthropic API | Medium | Stable |
| HTTP + OAuth 2.1 | claude.ai web + mobile + Desktop + Code | High | Upstream bugs as of April 2026 |

**Recommendation:** Pick HTTP + static bearer for Phase 2. Treat claude.ai web access as Phase 5, deferred. Confirm this is acceptable before proceeding.

### 0.3 Confirm Amadeus Self-Service can never issue tickets

This MCP will search and re-price flights, but cannot book them. Booking requires a separate consolidator agreement that is not available through Self-Service signup. Final user action is always a deep-link to airline.com or Google Flights, not a confirmed reservation. Confirm this is acceptable before proceeding.

## Phase 1: Goal

Stand up a local FastMCP server with one working tool, `search_flights`, that takes an origin, destination, date range, and passenger count, and returns a structured list of flight offers from Amadeus Flight Offers Search. The server runs locally over stdio for development, with all the contracts (errors, time formats, caching) in place that Phase 2 will need when it goes remote.

**End state:** the user can call the tool from MCP Inspector pointed at the local stdio server and get back a list of flight offers for HEL-IAD that looks plausible.

### In scope for Phase 1

- One tool: `search_flights`
- Amadeus test environment integration with OAuth2 client-credentials and token caching
- TTL response cache to protect the 2,000 monthly call budget during iteration
- Pydantic schemas for tool input and output
- Structured error contract (no exceptions leaking to Claude)
- Time format contract (ISO 8601 with explicit local-airport annotation)
- City code support (WAS, NYC, LON etc) passes through to Amadeus, which handles multi-airport expansion server-side. No expansion logic in our code.
- Airport code support (IAD, JFK, LHR etc) as the other accepted input shape
- Currency parameter, defaulted to USD
- Local stdio transport only
- Structured logging to file
- MCP Inspector verification

### Out of scope for Phase 1 (explicitly deferred)

- HTTP transport, public URL, Cloudflare Tunnel (Phase 2)
- Auth, bearer tokens, OAuth (Phase 2)
- Other tools: `airport_search`, `flight_price_confirm`, `fare_calendar` (Phase 3-4)
- Booking, payment, ticket issuance (out of scope entirely)
- Production Amadeus credentials (Phase 3)
- Tests against the live API on every CI run (use recorded fixtures)

## Tool specification: `search_flights`

### Tool description (the natural-language string the LLM reads)

The tool description is the single most important piece of prompt engineering in this project. Claude decides whether to use the tool based on this text. It must accurately describe what comes back, including caveats.

**Recommended description:**

> Search live flight offers for a given route and date range using the Amadeus GDS feed.
>
> Returns a ranked list of flight options with prices, airlines, segment details, and fare information. Does not book flights, only searches.
>
> Times in the response are local to the departure or arrival airport, with the airport's IATA code attached so the timezone can be derived. Do not perform timezone math on these times without first converting them.
>
> Origin and destination can be either airport IATA codes (IAD, DCA, BWI) or city IATA codes (WAS, LON, NYC). City codes return offers across all airports in that city; Amadeus handles the multi-airport expansion server-side.
>
> Results from identical searches are cached for up to 5 minutes. Prices may move within minutes, so a returned price may be up to 5 minutes old. If the user is about to act on a specific offer, re-run the search or use `flight_price_confirm` before committing to a number.
>
> Several fields are nullable because Amadeus does not always populate them. Most importantly, a null `baggage_allowance` means "the airline did not return this information," not "no checked bag is included." Do not state that a fare excludes checked bags based on a null value. The same applies to `last_ticketing_date` and `seats_available`.

### Input parameters

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| origin | string | yes | IATA code, 3 uppercase letters. Airport or city. |
| destination | string | yes | IATA code, 3 uppercase letters. Airport or city. |
| departure_date | string | yes | ISO date, YYYY-MM-DD. Must be today (UTC) or later. |
| return_date | string | no | ISO date, YYYY-MM-DD. Must be on or after departure_date. Omit for one-way. |
| adults | integer | no | Default 1. Range 1 to 9. |
| children | integer | no | Default 0. Age 2 to 11 at time of travel. |
| infants | integer | no | Default 0. Must be <= adults (lap infants). |
| cabin_class | string | no | One of: ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST. Default ECONOMY. |
| currency | string | no | ISO 4217. Default USD. |
| non_stop_only | boolean | no | Default false. |
| max_results | integer | no | Default 20. Max 50. Implementation note: must be passed to Amadeus as the `max` query parameter; Amadeus's own default is 250, which would otherwise mean fetching 250 offers and discarding 230. |

### Output shape: success

Returns an object with one field, `results`, containing an array of offer objects. Each offer:

| Field | Type | Notes |
|-------|------|-------|
| offer_id | string | Amadeus-assigned ID, opaque. Required for price confirm in later phases. |
| total_price | number | Total for all passengers, in currency. |
| currency | string | Echoes the input. |
| price_per_adult | number | Reference price. |
| airlines | array of strings | IATA airline codes operating segments (e.g. ["AY", "FI"]). |
| validating_airline | string | The airline that issues the ticket. May differ from operating. |
| outbound | object | See itinerary shape below. |
| inbound | object or null | Null for one-way. |
| seats_available | integer or null | If Amadeus returns it, otherwise null. |
| last_ticketing_date | string or null | ISO date, the deadline to ticket this fare. |
| fare_basis | string | The technical fare code (e.g. "VLOWFI"). Not user-facing but useful for debugging. |
| baggage_allowance | string or null | Short summary like "1 checked bag" or "no checked bag", null if not in response. |

### Itinerary shape (outbound or inbound)

| Field | Type | Notes |
|-------|------|-------|
| duration | string | ISO 8601 duration, e.g. "PT10H30M". |
| stops | integer | Number of intermediate stops. 0 = non-stop. |
| segments | array | Ordered list of flight segments. |

Each segment:

| Field | Type | Notes |
|-------|------|-------|
| airline | string | IATA code (e.g. "AY"). |
| flight_number | string | E.g. "AY15". |
| departure_airport | string | IATA code. |
| departure_time_local | string | ISO 8601 datetime, no offset, local to departure_airport. |
| arrival_airport | string | IATA code. |
| arrival_time_local | string | ISO 8601 datetime, no offset, local to arrival_airport. |
| cabin | string | ECONOMY / PREMIUM_ECONOMY / BUSINESS / FIRST. |
| booking_class | string | Single letter code (Y, M, B, etc). Debugging field. |

### Output shape: error states

The tool never raises. Errors come back as a structured error object with a known set of codes. Document these in the tool description so Claude knows what to do with each.

| Error code | When | What Claude should do |
|------------|------|----------------------|
| no_results | Amadeus returns empty `data[]`. In test env, may also mean the route isn't cached. The message text varies by `AMADEUS_ENV` so Claude has context. | Tell the user no flights found. In test env, suggest the route may not be cached and to retry in production. In prod, suggest adjusting dates or airports. |
| invalid_input | Pydantic validation fails. | Tell the user which field is wrong. |
| quota_exceeded | 429 from Amadeus, monthly quota hit. | Tell the user the quota is exhausted, retry next month or move to prod. |
| rate_limited | 429 from Amadeus, transient (10 TPS). | Auto-retry with backoff (handled internally), only surface if backoff exhausted. |
| upstream_error | 5xx from Amadeus or network error, including failures during token fetch. | Tell the user the upstream is having issues, try again shortly. |
| auth_failed | 401 from Amadeus on either token fetch or search call, credentials invalid. | Surface to logs, return error to Claude with a generic message. |

**Error shape:**

```json
{
  "error": {
    "code": "no_results",
    "message": "No flights found for HEL to IAD on 2026-05-18.",
    "retryable": false
  }
}
```

## Supporting components

### Amadeus API client

A thin wrapper, not a comprehensive SDK. Three responsibilities:

1. **OAuth2 client-credentials flow.** POST to `/v1/security/oauth2/token` with `grant_type=client_credentials`, `client_id`, `client_secret`. Cache the returned `access_token` in memory for `expires_in` seconds minus a 60-second safety buffer.
2. **Build query parameters from the tool input.** Map `cabin_class` strings to Amadeus's expected format. Handle the `originDestinations` array structure for round-trip.
3. **Parse the response,** surface known error codes, normalize the verbose Amadeus JSON into the simpler shape above.

**Concurrency note:** the token refresh must be async-safe. If two simultaneous tool calls both find the token expired, only one should refresh; the other should wait. Standard async lock pattern. Note that stdio + MCP Inspector serializes calls, so this lock will not be exercised in Phase 1. Implement it correctly anyway; Phase 2's HTTP transport is where it matters.

**Testability requirement:** the HTTP layer must be substitutable. Tests must be able to run the full search and normalization pipeline against fixture files in `tests/fixtures/` without hitting the live Amadeus API. The implementation pattern (dependency injection, monkeypatching, a transport abstraction, etc.) is the developer's call; the constraint is that fixture-driven tests work.

### Response cache

5-minute TTL, keyed on a canonical hash of all input parameters (sorted keys, lowercase IATA codes). In-memory only for Phase 1. The cache exists to protect the 2,000 monthly call quota during iteration. Cache TTL is mentioned in the tool description so Claude doesn't promise the user "live" data when it's 4 minutes stale.

### Logging

Structured logs (JSON lines) to the path defined by `LOG_FILE_PATH`. Each tool invocation logs:
- Timestamp, tool name, input parameters
- Cache hit or miss
- Amadeus call duration if made
- Result count or error code
- Anonymized: no user identifiers (there are none in Phase 1 anyway)

This is for Nophil to inspect when Claude does something weird. Not for production observability.

### Pydantic models

Three model groups:
1. `SearchFlightsInput` (validates the tool input). **Field validators must enforce IATA format (`^[A-Z]{3}$`), date >= today UTC, and cabin_class enum membership — not just type annotations.**
2. `FlightOffer`, `Itinerary`, `Segment` (the output shape above)
3. `AmadeusFlightOfferRaw` (matches the verbose Amadeus response, used internally before normalization)

## Configuration

Environment variables only, no config files in Phase 1.

| Variable | Required | Notes |
|----------|----------|-------|
| AMADEUS_CLIENT_ID | yes | From Amadeus Self-Service workspace. |
| AMADEUS_CLIENT_SECRET | yes | From Amadeus Self-Service workspace. |
| AMADEUS_ENV | yes | `test` or `production`. Determines the base URL. Also influences user-facing error messages. |
| LOG_FILE_PATH | no | Default `~/.trip-search-mcp/logs/flight-search.log`. Must be an absolute path. Do not use CWD-relative paths; stdio transport inherits CWD from the MCP client and the resulting path is unpredictable. |
| LOG_LEVEL | no | Default INFO. |
| CACHE_TTL_SECONDS | no | Default 300. |

`.env.example` shipped in the repo. Real `.env` gitignored.

## Acceptance criteria

Phase 1 is done when all of the following are true:

1. `fastmcp run server.py` starts the server cleanly on stdio.
2. MCP Inspector connects and shows the `search_flights` tool with the full description.
3. Calling `search_flights(origin="HEL", destination="IAD", departure_date="2026-05-18", return_date="2026-05-29", adults=1)` from MCP Inspector returns a `results` array with at least one offer.
4. The offer's `outbound` and `inbound` objects match the documented shape.
5. Calling with malformed input (lowercase code like "hel", wrong length like "HELS", digits like "H1L", or a past date) returns the `invalid_input` error shape, not an exception.
6. Calling with two valid but unconnected IATA codes (e.g. a small regional airport pair with no service, such as INV to KUO on an arbitrary date) returns the `no_results` error shape. Do not use sequential strings like ABC / XYZ, as those may collide with real codes and produce unpredictable results.
7. Two successive identical calls within 5 minutes: the second one is served from cache, confirmed by a log line.
8. Token refresh lock exists in the code but is not exercised by stdio + MCP Inspector (which serializes calls). Mark as "implemented, not yet verified" until Phase 2 brings concurrent HTTP traffic.
9. The repo has a README that documents how to run, configure, and call the tool.

## Risks and known issues

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|-----------|
| HEL-IAD not in Amadeus test cache | Medium | High (blocks dev iteration) | Phase 0.1 check. Fallback to a known-good test route or move to prod early. |
| Production access takes up to a week to approve | Medium | Medium (delays Phase 3) | Production credentials are not used until Phase 3, but the application takes up to a week. Start the paperwork during Phase 1 so approval is not blocking later. |
| Amadeus output schema drifts over time | Low | Low (one-time fix) | Tests against recorded fixtures, easy to refresh. |
| FastMCP 2.x API changes | Low | Medium | Pin to a specific minor version in pyproject.toml. |
| Claude misinterprets local-airport times | Medium | High (wrong recommendations) | Tool description is explicit, segment fields are named *_time_local. |
| 2,000 free calls/month feels generous but gets eaten fast | Medium | Medium | Aggressive caching, recorded fixtures for development. |

## File layout

```
trip-search-mcp/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── src/trip_search_mcp/
│   ├── __init__.py
│   ├── server.py              # FastMCP app, tool registration
│   ├── tools/
│   │   └── search_flights.py  # Tool function and description
│   ├── amadeus/
│   │   ├── client.py          # HTTP client, token cache
│   │   ├── normalize.py       # Raw Amadeus → clean output
│   │   └── errors.py          # Error code mapping
│   ├── models.py              # Pydantic models
│   ├── cache.py               # TTL response cache
│   └── logging_config.py
├── tests/
│   ├── fixtures/              # Recorded Amadeus responses. Must include at minimum: hel_iad_success.json (or whichever route was used for Phase 0.1), empty_results.json, and auth_failed.json. Error paths must be testable without hitting the live API.
│   ├── test_normalize.py
│   ├── test_models.py
│   └── test_search_flights.py
└── logs/                      # gitignored
```
