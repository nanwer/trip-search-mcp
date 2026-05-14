# Trip Planning Expansion: Weather, Currency, Events, Activities, Drill-downs

**Owner:** Nophil
**Status:** Draft (this revision), ready for Claude Code one track at a time.
**Last updated:** 2026-05-13 (validated rewrite of the original 5-track expansion)

---

## What changed in this revision (vs the original draft)

This spec is a corrected revision of an earlier draft. The fixes:

1. **Track F's `get_hotel_details` is REMOVED.** It was already shipped last
   session as `get_stay_details` (commit `656d57c`) — Track F now covers only
   `get_activity_details`.
2. **`SEARCH-ACTIVITIES-SPEC.md` is INLINED** into Track D. The original
   draft referenced an external file that doesn't exist.
3. **Geocoding consolidates on the existing Nominatim helper** in
   `src/trip_search_mcp/airbnb_backend/geocode.py`. The original draft
   proposed adding SerpAPI Google Local for weather; we don't need another
   geocoder when we already have one that's free and key-less.
4. **Track B (currency) defaults to the ECB feed** (free, no API key, no
   signup) instead of ExchangeRate-API. Preserves the project's "personal
   use needs no keys for core features" property.
5. **Track C (weather) evaluates Open-Meteo first** alongside NWS, replacing
   the NWS+OpenWeatherMap pairing. Open-Meteo is key-less, global, and
   sufficient for our 7-day cap.
6. **Module structure aligned with existing convention.** Backend packages
   stay top-level (`exchange_rate_backend/`, `open_meteo_backend/`, …),
   not nested under a new `backends/` namespace.
7. **Sequencing reordered** to value-first (weather → currency → events →
   activities → drill-down) instead of size-first.
8. **Quota math recomputed.** Heavy session post-expansion is ~7 SerpAPI
   calls (was undercounted in the original draft).
9. **Provider-drift risk added** to the cross-cutting risks table.

---

## Context

After reviewing `skarlekar/mcp_travelassistant` and validating against the
current trip-search-mcp codebase, five capabilities are worth adding to
turn search-only into full trip-planning:

1. **Weather forecasts** — drives packing, activity scheduling, date choice.
2. **Currency conversion** — flights/hotels/activities mix currencies; one
   tool fixes the mental math.
3. **Events search** — concerts, festivals, sports, time-bound experiences.
4. **Activities search** — tours, attractions, "things to do".
5. **Activity drill-down** — pricing + Viator URL for a specific activity.

After all five ship, trip-search-mcp covers: getting there (flights),
where to stay (search_stays / get_stay_details), what to do (activities),
what's happening (events), what's the weather, what it costs in your
currency. That's the full trip-planning loop.

**What we explicitly do NOT borrow from skarlekar's project:**

- Multi-server architecture. trip-search-mcp stays a single MCP server.
- Dedicated geocoder tools. We have Nominatim already; SerpAPI hotel
  results carry lat/lon; fli handles airport codes natively.
- Finance beyond currency (stock lookups, market overviews). Out of scope.
- Filter-by-X as separate MCP tools. Filtering happens at Claude's
  response layer, not as extra tool surface.

---

## Sequencing (value-first)

```
search_stays (DONE ✅ — Phases 1+2+3 shipped)
    │
    ▼
Track C — get_weather_forecast       (~3h Claude Code; highest user value)
    │
    ▼
Track B — convert_currency           (~1h; smallest win, builds momentum)
    │
    ▼
Track E — search_events              (~3h; time-sensitive use cases)
    │
    ▼
Track D — search_activities         (~5h; most complex; pattern established)
    │
    ▼
Track F — get_activity_details      (~2h; drill-down; depends on Track D)
```

Total ~14 Claude Code hours across 5 separate sessions. Each track is its
own PR + its own live-test cycle. Resist parallelizing across tracks —
that's where context drift hurts.

**Relationship to BACKLOG.md (post-current-state):**
- Closes BACKLOG #4 (was `get_hotel_details`, shipped as `get_stay_details`).
- Adds future items: `nearby_events` cross-tool combo, provider-preference
  filtering on activity `sources`.
- Doesn't touch BACKLOG #1 (flight deep-linking blocked on fli upstream),
  #2 (multi-airport — shipped), #3 (monitoring — shipped).

---

# Track C: `get_weather_forecast`

**Why this is the first track.** Weather is the most decision-shaping piece
of trip-planning context the MCP doesn't currently provide. "Rain on
Thursday so bias to museums that day" / "the second week looks rainy,
shift the trip" — these are things Claude can't say today.

## Phase 0: Verify Open-Meteo (and NWS as optional supplement)

Two free providers; verify which is sufficient.

### 0.1 Why these two and not OpenWeatherMap

OpenWeatherMap's "One Call API 3.0" moved to a credit-card-required tier
in 2024 (1000 free calls/day, but signup requires a card). The free
`weather` / `forecast` endpoints (5-day / 3-hour resolution) are still
free but require aggregating up to daily on our side. **Open-Meteo and NWS
are both genuinely free with no API key.**

### 0.2 Write `scripts/verify_weather_providers.py`

Make live calls to both providers for two test locations:

- **Reston, VA** (38.96, -77.36) — US, so both providers should work.
- **Tampere, Finland** (61.50, 23.79) — non-US, so NWS will fail and
  Open-Meteo must work.

Save 4 fixtures to `tests/fixtures/`:
- `openmeteo_forecast_reston.json`
- `openmeteo_forecast_tampere.json`
- `nws_forecast_reston.json`
- `nws_forecast_tampere_error.json` (the expected "outside forecast area"
  response)

Print a structure summary covering the five Phase 0 questions below.

### 0.3 Questions to answer

1. **Open-Meteo field parity.** Does the daily response carry: max temp,
   min temp, precipitation probability, weather code, sunrise, sunset?
   What are the exact JSON keys? Confirm metric units.
2. **NWS field parity.** Does NWS's `periods` array map cleanly to a
   daily aggregation? NWS chunks into "Tuesday Night / Wednesday" pairs.
3. **WMO weather code → human-readable.** Open-Meteo returns numeric WMO
   codes (0=clear, 61=rain, …). Build a small code-to-string map in
   normalize.py.
4. **NWS non-US behavior.** What does NWS return for Tampere coordinates?
   Error JSON? HTTP 404? Identifying this drives the "is this US?"
   fallback logic.
5. **Latency.** Open-Meteo should be sub-second. NWS may need 2 round-
   trips (`/points` → `/forecast`).

**Verdict to deliver:** "Single-provider Open-Meteo is sufficient" OR
"Hybrid Open-Meteo + NWS, with NWS preferred for US locations because X".

### 0.4 Decision baked in (pending Phase 0 confirmation)

Default expectation: **single-provider Open-Meteo**. NWS becomes a fallback
or a per-request alternative only if its US forecasts are meaningfully
better than Open-Meteo's. Simpler is better; less surface to maintain.

## Phase 1: Tool implementation

### Module structure

Top-level backend package (matches existing convention):

- `src/trip_search_mcp/open_meteo_backend/__init__.py`
- `src/trip_search_mcp/open_meteo_backend/client.py`
- `src/trip_search_mcp/open_meteo_backend/normalize.py`
- `src/trip_search_mcp/open_meteo_backend/raw.py`
- `src/trip_search_mcp/tools/get_weather_forecast.py`

If Phase 0 forces the hybrid design, add `nws_backend/` alongside.

### Input contract

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `location` | string | yes if `latitude`/`longitude` absent | Free-text city. Resolved via the existing Nominatim helper in `airbnb_backend/geocode.py` (extended with a `geocode_to_point(location)` variant returning just the centroid). NO new geocoder. |
| `latitude` | float | yes if `location` absent | Direct coordinates skip the geocode step. |
| `longitude` | float | yes if `location` absent | Same. |
| `start_date` | date | no | Default today (UTC). |
| `end_date` | date | no | Default `start_date + 6 days`. Hard cap: 7 days from today. |
| `units` | enum | no | `"metric"` (default) or `"imperial"`. Open-Meteo accepts `temperature_unit=celsius|fahrenheit`, `windspeed_unit=kmh|mph`, etc. |

### Output model

```python
class WeatherDay(BaseModel):
    date: IsoDate
    high_temp: float
    low_temp: float
    temp_unit: Literal["C", "F"]
    condition_summary: str           # "Partly cloudy with afternoon rain"
    weather_code: int                # raw WMO code for the LLM to reason about
    precipitation_probability_percent: int | None
    sunrise: str | None              # ISO time string
    sunset: str | None
    wind_speed: float | None
    wind_speed_unit: Literal["kmh", "mph"] | None

class GetWeatherForecastResult(BaseModel):
    location: str                    # echo the resolved location label
    latitude: float
    longitude: float
    timezone: str                    # e.g. "Europe/Helsinki"
    units: Literal["metric", "imperial"]
    days: list[WeatherDay]
```

### Tool description elicitation

- Ask for a date range only if the user is vague AND the question implies
  multi-day decisions (packing, "which week is better"). For single-day
  questions ("weather in Tampere on Friday"), default `start_date` and
  `end_date` to that one day.
- For "weather in X" with no date hint, default to a 7-day forecast
  starting today.

### Tool description result presentation

- For 4+ days, render a small artifact: one row per day with date, high/
  low, condition (with emoji from a small WMO map), precip%.
- For 1-3 days, prose is fine.
- Always disclose the units. ("All temperatures in °C.")

### Acceptance criteria

1. `get_weather_forecast(location="Tampere, Finland", start_date="2026-06-15", end_date="2026-06-18")` returns 4 `WeatherDay` entries via Open-Meteo.
2. `get_weather_forecast(latitude=38.96, longitude=-77.36)` returns 7 entries (default range) via Open-Meteo (or NWS in the hybrid design).
3. Invalid location returns `error: invalid_input` with a "couldn't find on the map" message.
4. Date ranges beyond 7 days from today are rejected at the input validator.
5. README documents zero new env vars for the Open-Meteo path.
6. ~10 new tests pass; existing 258 tests untouched.

---

# Track B: `convert_currency`

**Why next.** Flights from fli come back in EUR (locale-dependent). Hotels
pin to whatever the user requests (default EUR). Activities (Track D) and
events will likely surface mixed currencies. A dedicated conversion tool
saves the user from mental FX.

## Phase 0: Verify the ECB free feed

The European Central Bank publishes daily reference rates at
https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml — **no API
key, no rate limit, no signup**. Rates are quoted against EUR; cross-rates
are one division.

Write `scripts/verify_ecb_rates.py`:

- GET the daily XML feed.
- Parse the response. Save to `tests/fixtures/ecb_eurofxref_daily.xml`.
- Print: currency count, presence of USD/EUR/JPY/GBP/CAD/AUD/CHF/SEK/NOK/DKK/INR/MXN/BRL/SGD/KRW/CNY/THB/HKD/NZD, last-update timestamp.

### Questions to answer

1. **Coverage.** Does ECB carry all 19 currencies above? (Spoiler: yes, it
   carries 30+.)
2. **Update frequency.** ECB updates daily ~16:00 CET. Document that
   weekend / holiday queries return the previous business day's rates.
3. **Precision.** ECB returns 4 decimal places — sufficient for any
   conversion the user will care about.

If ECB doesn't cover a needed currency (unlikely), fall back to
ExchangeRate-API as a separate Phase 1 task. Don't make ExchangeRate-API
the default just to avoid the EUR-pivot math.

## Phase 1: Tool implementation

### Module structure

- `src/trip_search_mcp/ecb_backend/__init__.py`
- `src/trip_search_mcp/ecb_backend/client.py`  (httpx GET + XML parse)
- `src/trip_search_mcp/ecb_backend/cache.py`   (24h in-memory cache; ECB
  updates daily)
- `src/trip_search_mcp/tools/convert_currency.py`

### Input contract

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `amount` | float | yes | Numeric value to convert. |
| `from_currency` | string | yes | ISO 4217 code. |
| `to_currency` | string | yes | ISO 4217 code. |

### Output

```python
class CurrencyConversionResult(BaseModel):
    amount: float
    from_currency: IsoCurrency
    to_currency: IsoCurrency
    converted_amount: float
    rate: float
    rate_timestamp: str              # ECB update time, ISO
    source: str = "ECB"
```

### Tool description

- **Elicitation:** essentially none. Both currencies and amount are required.
- **Presentation:** inline prose. *"¥30,000 = €182.45 (rate as of 13 May 2026)."* No card.

### Acceptance criteria

1. `convert_currency(amount=100, from_currency="EUR", to_currency="USD")` returns a numeric result with rate + timestamp.
2. `convert_currency(amount=30000, from_currency="JPY", to_currency="EUR")` works via the EUR-pivot math (`amount / jpy_rate * usd_rate` form).
3. Same-currency conversion (`EUR → EUR`) returns the input unchanged with rate=1.0.
4. Invalid currency codes return `error: invalid_input`.
5. Two identical calls in the same session result in one ECB fetch (cache hit on the second).
6. README documents NO new env vars.

---

# Track E: `search_events`

**Why third.** Events are time-bound — concerts and tournaments need to be
searched + reserved while planning. Sibling of activities (Track D) in
shape but with different user intent.

## Phase 0: Verify SerpAPI google_events

Write `scripts/verify_google_events.py`:

- Call `engine=google_events` with `q="Events in Lisbon"`.
- Call again with `q="Concerts in Lisbon June 2026"`.
- Call again with `q="BTS tour Paris July 2026"` (concrete query).
- Save 3 fixtures to `tests/fixtures/serpapi_events_*.json`.

### Questions to answer

1. **Date-filter mechanics.** SerpAPI docs mention `htichips` for date
   constraints. Empirically verify which exact strings work:
   `htichips=date:today` / `date:tomorrow` / `date:week` / `date:next_week` / `date:next_month`. If we need an arbitrary date range, what's the workaround? (Likely: bake the month/year into the `q` string.)
2. **Query phrasing.** "Concerts in Lisbon" vs "Music events in Lisbon" vs "Lisbon June 2026" — which yields higher-quality results? Compare the 3 fixtures.
3. **Field parity.** Confirm per-event: `title`, `address` (list of strings), `link` (third-party ticket page), `event_location_map`, `date.start_date`, `date.when` (formatted display string), optional `venue.name` / `venue.rating`. Map to `EventOffer` cleanly.
4. **Ticket-URL quality.** Where does `link` actually go? Ticketmaster? Eventbrite? Venue site? Per-event variance is OK as long as it lands on a buyable page.

## Phase 1: Tool implementation

### Module structure

- `src/trip_search_mcp/serpapi_events_backend/__init__.py`
- `src/trip_search_mcp/serpapi_events_backend/client.py`
- `src/trip_search_mcp/serpapi_events_backend/normalize.py`
- `src/trip_search_mcp/serpapi_events_backend/raw.py`
- `src/trip_search_mcp/tools/search_events.py`

### Input contract

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `location` | string | yes | Free-text. Combined with `query` into the SerpAPI `q` string. |
| `query` | string \| null | no | Optional event type ("concerts", "festivals", "sports", "comedy"). |
| `start_date` | date \| null | no | Optional date filter. Translated to `htichips` or query-string per Phase 0 findings. |
| `end_date` | date \| null | no | Optional. |
| `max_results` | int | no | 1–50. Default 15. |

### Output

```python
class EventOffer(BaseModel):
    offer_id: str                    # hash of (title, start_date, venue_name)
    title: str
    start_date: IsoDate | None       # may be None if SerpAPI returns unparseable date
    end_date: IsoDate | None
    when_text: str                   # original display string from SerpAPI ("Sat, Dec 6, 7:30 PM")
    venue_name: str | None
    venue_rating: float | None
    address: str | None              # flattened from SerpAPI's address list
    description: str | None
    thumbnail: str | None
    ticket_url: str                  # SerpAPI's `link` field

class SearchEventsResult(BaseModel):
    results: list[EventOffer]
```

### Tool description

- **Elicitation:** if user mentions an event type, bake it. If they
  mention dates, set the filter. If vague ("things happening in Lisbon"),
  default to "upcoming events" without a date filter.
- **Presentation:** card-based artifact, one card per event. Lead with
  `title` + `when_text` + venue. "Get Tickets" button → `ticket_url`.
  Same no-photos rule as stays — many third-party event CDNs are hotlink-
  protected.

### Acceptance criteria

1. `search_events(location="Lisbon")` returns upcoming events.
2. `search_events(location="Paris", query="concerts", start_date="2026-07-15", end_date="2026-07-20")` returns concerts in that window.
3. Event dates parse correctly from SerpAPI's varied date strings; unparseable strings set `start_date=None` rather than crashing.
4. `ticket_url` is preserved verbatim and clickable.
5. ~10 new tests; existing 258 untouched.

---

# Track D: `search_activities`

**Why fourth.** Most complex of the five tracks. Pattern of weather +
currency + events is established by the time we get here.

## Phase 0: Verify SerpAPI Tripadvisor with ssrc=A

SerpAPI's `engine=tripadvisor` with `ssrc=A` returns "Things to Do" —
mixed sights (free attractions) and experiences (bookable tours).

Write `scripts/verify_tripadvisor_activities.py`:

- Call with `q="Lisbon"`, `ssrc=A`.
- Call with `q="cooking class Lisbon"`, `ssrc=A`.
- Call with explicit lat/lon for a specific neighborhood.
- Save 3 fixtures.

### Questions to answer

1. **`place_type` distribution.** Each result has a `place_type`. What
   values appear under `ssrc=A`? ("attraction"? "things to do"?
   "experience"?). How do they split sights vs bookable tours?
2. **Field population per place_type.** Do bookable experiences carry a
   different field set than free attractions? (Expect: experiences carry a
   booking_url / Viator URL; attractions don't.)
3. **Thumbnail accessibility in artifacts.** Same question as we asked for
   hotels — do Tripadvisor's CDN URLs load outside their hosts? If they're
   hotlink-protected like hotel CDNs, the activity cards skip photos.
4. **Deep-link target shape.** Where does the result's `link` go? Direct
   Viator booking page (good) or Tripadvisor's listing page (one click
   away from booking)?
5. **Free-text query expressiveness.** "Cooking classes in Lisbon" vs
   "Food experiences in Lisbon" vs "Things to do in Lisbon related to
   food" — does Tripadvisor's search handle natural language?
6. **Geographic precision via lat/lon.** Can we pass coordinates instead
   of a city name? (For more precise neighborhood-level queries.)
7. **Pagination.** What's the default result count? Can we request more?

## Phase 1: Tool implementation

### Module structure

- `src/trip_search_mcp/tripadvisor_backend/__init__.py`
- `src/trip_search_mcp/tripadvisor_backend/client.py`
- `src/trip_search_mcp/tripadvisor_backend/normalize.py`
- `src/trip_search_mcp/tripadvisor_backend/raw.py`
- `src/trip_search_mcp/tools/search_activities.py`

### Input contract

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `location` | string | yes | Free-text city or neighborhood. |
| `query` | string \| null | no | Free-text filter ("cooking classes", "boat tours", "museums"). |
| `place_type_filter` | enum \| null | no | `"sights"` / `"experiences"` / `null` (both). |
| `min_rating` | float \| null | no | 0.0–5.0 minimum review score. |
| `max_results` | int | no | 1–50. Default 15. |

### Output

```python
class ActivityOffer(BaseModel):
    offer_id: str                    # SerpAPI's stable place_id
    name: str
    activity_type: Literal["sight", "experience"]
    rating: float | None
    review_count: int | None
    description: str | None
    location: str | None             # e.g. "Lisbon, Portugal"
    latitude: float | None
    longitude: float | None
    thumbnail: str | None
    highlighted_review_text: str | None
    booking_url: str                 # Viator deep-link if available; Tripadvisor listing otherwise

class SearchActivitiesResult(BaseModel):
    results: list[ActivityOffer]
```

No `price` field in Phase 1 — Tripadvisor's list endpoint doesn't carry
it. Track F's `get_activity_details` surfaces price.

### Tool description: PRE-CALL ELICITATION (three branches)

The original draft refers to a "preferences-aware elicitation block with
three branches" — inlined here so this spec stands alone.

**Branch 1: User specifies an activity type.**
"Find cooking classes in Lisbon." → `query="cooking class"`, no
clarifying question, just search.

**Branch 2: User asks for a recommendation.**
"What should I do in Lisbon?" → before searching, Claude SHOULD draw on
its own memory of the user's preferences (Claude.ai's native memory system
surfaces this; the MCP tool itself doesn't read memory — Claude does and
passes the result via `query`). If the user has past mentions of "food",
"wine", "history", whatever, bake that into `query`. If memory is empty,
fall to Branch 3.

> **Important wording correction from the original draft:** The original
> said *"Claude infers from `userMemories`"* — that's not a thing the MCP
> can read. The MCP only sees inputs Claude passes. The preference
> inference is Claude's job at prompt-construction time; the MCP just
> takes the resulting `query` string.

**Branch 3: User is vague and Claude has no preference signal.**
"Things to do in Lisbon?" with empty conversation context. ONE clarifying
question: *"Any particular interest — food, history, outdoors, nightlife?"*
Then search.

### Tool description: RESULT PRESENTATION

- Card-based artifact, one card per result.
- For Branch 2 (memory-driven), preamble: *"Based on your interest in food
  and wine, here are top-rated experiences in Lisbon."* — makes the
  inference legible.
- Card content: name, rating + review_count, location (neighborhood-level
  if available), 1-line highlighted review, "Find on Tripadvisor /
  Viator" button → `booking_url`.
- No photos (hotlink protection — same rule as stays/events).

### Acceptance criteria

1. `search_activities(location="Lisbon")` returns mixed sights and experiences.
2. `search_activities(location="Lisbon", query="cooking classes")` filters to relevant experiences.
3. `place_type_filter="experiences"` returns only bookable.
4. `place_type_filter="sights"` returns only free attractions.
5. Memory-driven recommendation mode (Branch 2) produces results consistent with the user's stated preferences in manual smoke tests.
6. `min_rating=4.0` excludes properties with lower scores or no review data.
7. ~12 new tests; existing tests untouched.

---

# Track F: `get_activity_details`

**Why last.** Drill-down on activities. Depends on Track D shipping first.

> **`get_hotel_details` is NOT part of Track F.** It already shipped as
> `get_stay_details` in commit `656d57c`. Skip if you see references in
> earlier drafts.

## Why activity drill-down matters

`search_activities` results carry NO price. Tripadvisor surfaces price only
on the per-place page, which SerpAPI exposes via a separate endpoint with
the `place_id` (or `data_id`) we already captured as `offer_id`.

The drill-down should surface:
- Price range or starting price
- Duration estimate ("3 hours", "Full day")
- Detailed description (longer than the search-time blurb)
- Direct Viator booking URL (vs the Tripadvisor listing page)
- Next available date / cancellation policy

## Phase 0: Verify SerpAPI Tripadvisor place_details

Write `scripts/verify_tripadvisor_place_details.py`:

- Pick a `place_id` from the Track D Phase 0 capture.
- Call SerpAPI with the place_id parameter for that specific activity.
- Save fixture.

### Questions to answer

1. **Price shape.** Is price a single number, a range, "from X", or sometimes absent?
2. **Viator URL presence.** Is there a direct Viator product URL distinct from the Tripadvisor listing URL? When (only for bookable activities)?
3. **Duration normalization.** Free text ("3 hours", "Half-day", "Full day, 8 hours")? Will we keep as a string or parse to minutes?
4. **Available-dates structure.** Discrete list of dates? Date range? Booking calendar? Probably not surfaced; if absent, document.

## Phase 1: Tool implementation

### Module structure

- Add to `src/trip_search_mcp/tripadvisor_backend/client.py`: a
  `get_place_details(place_id, ...)` method.
- Add to `tripadvisor_backend/normalize.py`: `build_activity_details(raw)`.
- New tool: `src/trip_search_mcp/tools/get_activity_details.py`.

### Input

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `place_id` | string | yes | `offer_id` from a prior `search_activities` result. |
| `currency` | string | no | ISO 4217. Default `"EUR"`. |

### Output

```python
class ActivityDetails(BaseModel):
    place_id: str
    name: str
    activity_type: Literal["sight", "experience"]
    rating: float | None
    review_count: int | None
    description: str                 # long-form, > the search blurb
    duration: str | None             # raw text from Tripadvisor
    price_from: float | None
    price_currency: IsoCurrency | None
    location: str | None
    latitude: float | None
    longitude: float | None
    viator_url: str | None           # direct booking, when present
    tripadvisor_url: str             # always present, fallback
    cancellation_policy: str | None
```

### Tool description

- **Elicitation:** none — `place_id` is opaque, only Claude has the
  context to choose.
- **Presentation:** single rich card. Lead with price (prominent). Show
  the Viator button if `viator_url` exists; otherwise show the
  Tripadvisor button.

### Acceptance criteria

1. `get_activity_details(place_id=<from prior search>)` returns a `Description`-grade card with price, duration, and a direct booking URL.
2. Activities without a Viator URL gracefully fall back to the Tripadvisor URL.
3. Cache: 6-hour TTL keyed on `(place_id, currency)`.
4. ~6 new tests.

---

## Cross-cutting risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Five tracks attempted in parallel cause context drift in Claude Code sessions | Certain | Medium-High | Ship one track at a time, in the recommended order. Each track its own PR, its own live-test cycle. Resist parallelization. |
| Post-expansion SerpAPI quota burn higher than original spec admitted | High | Medium | Recomputed worst case: stays(2) + 2× get_stay_details(2) + activities(1) + events(1) + get_activity_details(1) = **7 calls per heavy session**. At 100/mo free tier ≈ 14 such sessions/month. Document; if it ever becomes binding, upgrade or add longer cache TTLs. |
| Provider drift across 4 new vendors (ECB, Open-Meteo, NWS if used, SerpAPI google_events, SerpAPI Tripadvisor) | Medium | Medium | Each backend follows the existing injectable-transport pattern (httpx.MockTransport in tests, no live calls in CI). Phase 0 fixtures keep regression tests deterministic. |
| Track D's memory-driven recommendation branch fails because Claude's memory rarely contains relevant signal | Medium | Medium | Manual smoke test post-ship. Branch 3 catches the empty-memory case. |
| Open-Meteo / NWS becomes rate-limited or changes shape | Low | Medium | Standard error envelope. Pin to documented endpoint versions in the client. |
| Adding 4 backend packages bloats the import graph | Low | Low | Each is lazy — only imported when its tool is called. Startup time is unaffected. |

---

## How to hand this to Claude Code

Each track is a separate Claude Code session, in this order:

### Session 1 (current — Track C, weather)

> Read TRIP-PLANNING-EXPANSION-SPEC.md, Track C. Phase 0 has already been
> run by a parallel agent and the fixtures are at
> `tests/fixtures/openmeteo_*.json` / `nws_*.json`. Start with the verdict
> from the Phase 0 report, then implement Phase 1.

### Session 2 (after Track C lands)

> Read TRIP-PLANNING-EXPANSION-SPEC.md, Track B. Run Phase 0 against the
> ECB feed (`scripts/verify_ecb_rates.py`), confirm 3 of the 19
> currencies are present, then implement Phase 1. Stop before Track E.

### Session 3 (after Track B lands)

> Read TRIP-PLANNING-EXPANSION-SPEC.md, Track E. Run Phase 0 against
> SerpAPI's google_events engine for the 3 specified queries, answer
> the 4 Phase 0 questions, then implement Phase 1.

### Session 4 (after Track E lands)

> Read TRIP-PLANNING-EXPANSION-SPEC.md, Track D. Run Phase 0 against
> SerpAPI's Tripadvisor engine with ssrc=A, answer the 7 Phase 0
> questions (especially the thumbnail-hotlink one and the free-text-query
> one), then implement Phase 1.

### Session 5 (after Track D lands)

> Read TRIP-PLANNING-EXPANSION-SPEC.md, Track F. Pull a `place_id` from
> the Track D Phase 0 fixture, run Phase 0 verification on the
> place_details endpoint, then implement Phase 1.

After all five sessions: trip-search-mcp covers flights, stays + drill-
down, activities + drill-down, events, weather, and currency. The full
trip-planning loop, with persistent price watches and city-code expansion
as bonus features.
