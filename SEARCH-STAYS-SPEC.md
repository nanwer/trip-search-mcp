# Search Stays: Unify Hotels and Vacation Rentals into One Tool

**Owner:** Nophil
**Status:** Phase 0 complete (this session). Ready for Phase 1 implementation.
**Last updated:** 2026-05-13

---

## Context

The repo is `trip-search-mcp` (Python package `trip_search_mcp`) and exposes
three tools today: `search_flights`, `search_cheapest_dates`, `search_hotels`.
The `search_hotels` tool returns hotel-class properties from SerpAPI's
`google_hotels` endpoint, with deep-linking already shipped via
`property_token` and per-call currency configurable.

SerpAPI's same `google_hotels` engine returns vacation rentals when
`vacation_rentals=true` is passed. We want one tool that covers both
categories so a "find me a place to stay" question doesn't need two manual
calls.

Goal: rename `search_hotels` to `search_stays`, add a `category` parameter
(`hotels` / `vacation_rentals` / `all`), and when `all` is requested, fan
out to two SerpAPI calls in parallel, dedup, sort, and return one merged
list. Surface per-property OTA price attribution via a new `sources` field.

**Net result:** one tool covers the whole "where do we stay" question.
Card rendering visually distinguishes hotels from vacation rentals and
shows OTA price comparison where present.

**What's not in scope here:** a `pyairbnb_backend` for direct Airbnb data.
Google's aggregation through SerpAPI does NOT include Airbnb in the
`prices` array — see "Phase 0 findings" below. If that gap becomes a real
problem, that's a future phase.

**Relationship to existing BACKLOG.md items:**

- Doesn't touch flight deep-linking (#1), city codes (#2), monitoring (#3).
- Hotel `get_hotel_details` follow-up (#4) becomes the `get_stay_details`
  follow-up post-rename — still on the backlog, becomes more valuable
  after this lands.

---

## Phase 0 findings (completed in spec-validation session)

The verify script `scripts/verify_vacation_rentals.py` captured two
fixtures against the live SerpAPI endpoint for the same Tampere query
(2026-06-15 → 2026-06-18, 2 adults):

- `tests/fixtures/serpapi_vacation_rentals_tampere.json` (18 properties)
- `tests/fixtures/serpapi_hotels_tampere_compare.json` (15 properties)

### Q1: Response-shape parity

Vacation rentals and hotels share these fields:
`name`, `type`, `property_token`, `gps_coordinates`, `rate_per_night`,
`total_rate`, `images`, `amenities`, `nearby_places`, `overall_rating`,
`reviews`, `location_rating`, `check_in_time`, `check_out_time`, `link`.

**Hotel-only fields:** `description`, `hotel_class`, `extracted_hotel_class`,
`deal`, `deal_description`, `ratings`, `reviews_breakdown`.

**Rental-only fields:** `essential_info` (list, e.g. `["Entire apartment",
"Sleeps 8", "2 bedrooms", "2 bathrooms", "5 beds", "786 sq ft"]`),
`excluded_amenities`, `health_and_safety`, `prices`.

**Implication:**
- `description` will be null on rentals — that's normal. Card rendering
  uses `essential_info` as a description equivalent for rentals.
- We model both `description` and `essential_info` on the offer; whichever
  is populated is what the renderer shows.

### Q2: `prices` array contents

The `prices` array exists **only on rentals** in this query — hotel
results returned NO `prices` field at all. For each rental property,
`prices` was a 1-entry list.

Sources observed across 18 rentals: **Hotels.com, Booking.com,
Bluepillow.com**. **No Airbnb. No VRBO. No Vacasa.** Google's vacation
rentals are aggregated via OTAs, not from primary platforms.

Per-entry shape:
```json
{
  "source": "Booking.com",
  "logo": "https://...",
  "num_guests": 4,
  "rate_per_night": {
    "lowest": "€176",
    "extracted_lowest": 176,
    "before_taxes_fees": "€155",
    "extracted_before_taxes_fees": 155
  }
}
```

**Implication:** the `sources` field on `StayOffer` is real and valuable
(OTA comparison shopping), but the marketing story is "Booking.com vs
Hotels.com pricing", not "Airbnb vs VRBO". Be honest about this in
README / tool description so Claude doesn't oversell.

### Q3: `property_token` stability

**Cannot be tested empirically** with the captured fixtures — the 18
rentals and 15 hotels for Tampere had **zero name overlap**. They're
entirely disjoint sets.

**Implication:** for this market, dedup is purely defensive. We still
implement it (other markets may differ) with a two-tier strategy:
1. `property_token` equality (cheapest if SerpAPI is stable)
2. Fallback: `(name.casefold(), round(latitude, 4), round(longitude, 4))`
   tuple — 4 decimal places ≈ 11m at the equator, tight enough to avoid
   collapsing different properties on the same block.

### Q4: Filter scoping

**SerpAPI rejects mismatched filters with HTTP 400**, NOT silent ignore:

```
400 Bad Request
{"error": "You're not allowed to enable `hotel_class` for Vacation Rentals search."}
```

The same is expected for the reverse direction (`bedrooms` with
`vacation_rentals=false`).

**Implication — this is the most important Phase 0 finding:** Phase 1
orchestration MUST construct two distinct param sets when `category="all"`:

- Hotel call param set: common filters + `vacation_rentals=false` +
  hotel-only filters (`hotel_class`, `free_cancellation`, …).
- Rental call param set: common filters + `vacation_rentals=true` +
  rental-only filters (`bedrooms`, `bathrooms`).

A single "pass everything to both" approach fails immediately.

### Q5: Latency

- `vacation_rentals=true`: ~2.94s wall-clock (SerpAPI metadata: 2.66s)
- `vacation_rentals=false`: ~2.76s wall-clock (SerpAPI metadata: 2.49s)
- **Parallel-fanout merged path: ~max(2.9, 2.8) ≈ 3s.** NOT the sum.

**Implication:** `category="all"` is acceptable as the default from a UX
latency perspective. It costs ~0.2s more than a single call, not ~3s.

---

## Decisions baked into this spec (open questions resolved)

| Question | Decision | Rationale |
|---|---|---|
| Default for `category` | `"all"` | Latency cost is negligible (~3s same as single call). Quota cost is 2x (100/mo free tier still covers 50 merged queries). Eli's "find me a place" framing is the dominant use case. |
| Dedup strategy | Two-tier: `property_token` first, `(name.casefold(), round(lat,4), round(lon,4))` fallback | Token stability is unknown; defensive coding. Real-world overlap appears rare anyway. |
| `description` on rentals | Stays null. New structured `essential_info` field carries the rental's facts list. | Honest representation of the data SerpAPI returns. |
| Provider names in `sources` | Canonical-name map for known OTAs (Booking.com, Hotels.com, Bluepillow.com, Expedia, Agoda, Hotels.com), title-case fallback for everything else. | Avoids `"Booking.Com"` artifacts from naïve title-casing. |
| Partial failure | Success envelope with `warnings: list[str]` populated | Returning a partial-success-with-warnings is more useful than failing the whole call. The LLM is instructed to surface warnings verbatim above results. |
| Tool description rewrite | Edit-in-place from current `search_hotels` description, NOT clean-slate rewrite | Preserves the "review_score is 0-5 not 0-10" and other behavioral guards already proven to work. |

---

## Phase 1: Backend changes (multi-category fan-out)

### 1.1 Raw response models — add `prices` and `essential_info`

File: `src/trip_search_mcp/serpapi_hotels_backend/raw.py`

New model `SerpHotelPrice`:

| Field | Type | Notes |
|---|---|---|
| `source` | `str` | OTA name, e.g. "Booking.com". |
| `num_guests` | `int \| None` | Per-source capacity, when surfaced. |
| `rate_per_night` | `SerpHotelRate \| None` | Reuses existing nested rate type. |

Additions to `SerpHotelProperty`:

| Field | Type | Notes |
|---|---|---|
| `prices` | `list[SerpHotelPrice]` (default `[]`) | Empty on hotels in captured fixtures; populated on rentals. `extra="ignore"` already allows the field to be absent. |
| `essential_info` | `list[str]` (default `[]`) | Rental-only structured facts list. |

### 1.2 Output model — `HotelOffer` → `StayOffer`

File: `src/trip_search_mcp/models.py`

Rename `HotelOffer` → `StayOffer`, `SearchHotelsInput` → `SearchStaysInput`,
`SearchHotelsResult` → `SearchStaysResult`.

New fields on `StayOffer`:

| Field | Type | Notes |
|---|---|---|
| `category` | `Literal["hotel", "vacation_rental"]` | Mirrored from raw `type`; null-safe with explicit branch. |
| `sources` | `list[Source]` (default `[]`) | One entry per booking partner. Empty when SerpAPI's response didn't carry `prices`. |
| `bedrooms` | `int \| None` | Parsed from `essential_info` (rentals only). |
| `bathrooms` | `int \| None` | Parsed from `essential_info` (rentals only). |
| `sleeps` | `int \| None` | Parsed from `essential_info` (e.g. "Sleeps 8"). |

Where `Source` is:

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Canonicalized via known-OTA map; title case fallback. |
| `price_per_night` | `float \| None` | In response currency. |
| `before_taxes_fees` | `float \| None` | When SerpAPI exposes it (was on every rental row in captured data). |

New `SearchStaysResult`:

```python
class SearchStaysResult(BaseModel):
    results: list[StayOffer]
    warnings: list[str] = []   # populated only on partial-failure path
```

The top-level `StayOffer.price_per_night` continues to reflect the
lowest available source (whatever SerpAPI surfaces as the headline rate).

### 1.3 Input contract additions

New on `SearchStaysInput`:

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `category` | enum string | `"all"` | One of `"all"` / `"hotels"` / `"vacation_rentals"`. |
| `min_bedrooms` | `int \| None` | none | Vacation-rental filter. Routed to the rental SerpAPI call only. |
| `min_bathrooms` | `int \| None` | none | Same scoping as `min_bedrooms`. |

Existing parameters all stay. The `min_rating` (star count) filter applies
**only** to the hotel call when `category="all"`, since rentals carry no
hotel class. Common filters (`min_review_score`, `max_price_per_night`,
`required_amenities`, `sort_by`, `max_results`, `currency`) apply to
whichever calls are made.

### 1.4 Client orchestration

File: `src/trip_search_mcp/serpapi_hotels_backend/client.py`

Two new helpers:

- `_build_query_hotels(params)` — sets `vacation_rentals=false`, applies
  hotel-only filter inputs (`min_rating` → `hotel_class` if we ever expose
  it natively; for now post-filter as today). Does NOT include
  `bedrooms` / `bathrooms`.
- `_build_query_rentals(params)` — sets `vacation_rentals=true`, applies
  `bedrooms` / `bathrooms` natively (SerpAPI supports them as filters).
  Does NOT include `hotel_class`.

`SerpAPIHotelsClient.search()` becomes a dispatcher:

```python
async def search(self, params: SearchStaysInput) -> SearchStaysResult:
    if params.category == "hotels":
        offers = await self._search_one(params, mode="hotels")
        return SearchStaysResult(results=offers, warnings=[])
    if params.category == "vacation_rentals":
        offers = await self._search_one(params, mode="rentals")
        return SearchStaysResult(results=offers, warnings=[])
    return await self._search_merged(params)
```

`_search_merged()` uses `asyncio.gather(..., return_exceptions=True)` so
one failure doesn't cancel the other:

```python
hotels_task = self._search_one(params, mode="hotels")
rentals_task = self._search_one(params, mode="rentals")
results = await asyncio.gather(hotels_task, rentals_task, return_exceptions=True)
```

For each result that's a `ToolError`, append a warning string and skip;
keep the successful side's offers. If BOTH sides are errors, re-raise the
more informative one (prefer the hotel-side error on tie).

### 1.5 Merge logic (when `category="all"`)

After both calls return:

1. Combine offers from both responses.
2. **Dedup** with two-tier matching:
   - Pass 1: bucket by `property_token`. Within each bucket, keep the
     entry with the lower `price_per_night`.
   - Pass 2 (fallback for entries without `property_token` or for entries
     where the tokens differ but the property is plausibly the same):
     bucket by `(name.casefold(), round(latitude, 4), round(longitude, 4))`.
     Tolerance of 4 decimal places ≈ 11m.
3. **Sort** by the user's `sort_by`. `BEST` uses price ascending as the
   tie-breaker (SerpAPI has no globally meaningful merged rank). Document
   this explicitly in the tool description.
4. **Truncate** to `max_results`. If both calls returned 20 each, 40
   candidates collapse to dedup pass, then truncate.

### 1.6 Partial failure handling

When `category="all"` and one side errors:

```python
results = SearchStaysResult(
    results=successful_side_offers,
    warnings=[
        f"Vacation rental search failed: {err.code.value}. Showing hotels only."
    ],
)
```

Both error envelope codes (`RATE_LIMITED`, `UPSTREAM_ERROR`, etc.) flow
through. The tool wrapper still returns success because the user got SOME
useful data. The `warnings` list lets the LLM tell the user transparently.

When both error: standard `error_response()` envelope as today.

### 1.7 Out of scope for Phase 1

- A separate `pyairbnb` backend for direct Airbnb data
- Provider-preference filtering at the request level (SerpAPI doesn't
  expose that; future post-fetch enhancement on `sources`)
- The `get_stay_details` follow-up tool (BACKLOG.md item 4, renamed)

---

## Phase 2: Rename to `search_stays`, update tool description

### 2.1 File / symbol renames

- `src/trip_search_mcp/tools/search_hotels.py` → `tools/search_stays.py`
- Function: `search_hotels` → `search_stays`
- Server registration in `server.py`: register as `search_stays`, remove
  the `search_hotels` registration. The FastMCP `mcp.tool(name="...")`
  decorator updates.
- Pydantic class renames in `models.py` (covered in 1.2).
- Module name in `tools/__init__.py` — update any re-exports.

### 2.2 Tool description rewrite (edit-in-place, NOT clean slate)

Start from the existing `search_hotels` TOOL_DESCRIPTION. Preserve:
- The "review_score is 0-5 not 0-10" callout (proven necessary).
- The currency-localization elicitation (just shipped this session).
- The no-photos rendering rule (just shipped this session).
- The address-is-null disclaimer.

Add/edit:

**Header paragraph**: change "Search Google Hotels" → "Search Google's
hotel and vacation rental listings". Mention `category` parameter and
that the default is `"all"` (returns mixed results).

**`category` parameter section**:
```
`category` accepts: "all" (default), "hotels", "vacation_rentals".
- "all" makes two SerpAPI calls in parallel, merges, dedupes, and sorts.
  Costs 2 API calls instead of 1 per query. Latency is unchanged (~3s
  parallel). Burns SERPAPI quota twice as fast.
- "hotels" returns only hotel-class properties (~current search_hotels).
- "vacation_rentals" returns short-term rentals aggregated by Google
  from OTAs like Booking.com, Hotels.com, Bluepillow.com. NOTE: Airbnb
  and VRBO are NOT included in Google's aggregation — they do not
  appear in the response.
```

**Filter scoping section**:
```
- `min_rating` (1-5 star count) applies only to hotels. When category="all"
  it filters the hotel side; rentals pass through.
- `min_bedrooms` and `min_bathrooms` apply only to vacation rentals. When
  category="all" they constrain the rental side; hotels pass through.
- All other filters (min_review_score, max_price_per_night,
  required_amenities, sort_by) apply uniformly.
```

**PRE-CALL ELICITATION additions**:
- Add a "Type of stay" question: default to `category="all"` unless the
  user signals "hotel," "Airbnb," "rental," "STR," "apartment," etc.
- If the user mentions bedroom/bathroom counts, set `min_bedrooms` /
  `min_bathrooms` and remember they apply to the rental side only.

**RESULT PRESENTATION additions**:
- Each card shows a small type badge: `Hotel` or `Vacation rental`.
- For rentals, surface bedrooms / bathrooms / sleeps inline if present.
- For rentals with `sources`, show the lowest source inline (e.g. "€176
  on Booking.com") and a smaller "also on Hotels.com" note for 2+ sources.
- If `warnings` is non-empty in the response, surface them verbatim
  above the card grid. DO NOT silently swallow them.

---

## Phase 3: Tests, fixtures, docs

### 3.1 Test renames + additions

- `tests/test_search_hotels.py` → `tests/test_search_stays.py`. Update
  test names from `test_hotels_*` to `test_stays_*` where appropriate.
- `tests/test_hotels_client.py` → `tests/test_stays_client.py`.
- `tests/test_hotels_normalize.py` → `tests/test_stays_normalize.py`.

### 3.2 New fixtures

- `tests/fixtures/serpapi_vacation_rentals_success.json` (synthesized
  3-property rental fixture, similar to the existing hotels success
  fixture) for unit-level normalize tests.
- `tests/fixtures/serpapi_vacation_rentals_tampere.json` (Phase 0
  capture) — already exists from this session. Use for end-to-end
  shape regression tests.
- `tests/fixtures/serpapi_hotels_tampere_compare.json` (Phase 0
  capture) — already exists. Used for the compare-mode test.

### 3.3 New tests

- `category="hotels"` produces one SerpAPI call with `vacation_rentals=false`.
- `category="vacation_rentals"` produces one SerpAPI call with
  `vacation_rentals=true`.
- `category="all"` produces two parallel calls. Inspect the captured
  mock requests; the hotel call must NOT include `bedrooms`, the rental
  call must NOT include `hotel_class`.
- Filter scoping at request-build time (regression for the 400 finding):
  passing `min_rating=4` with `category="vacation_rentals"` does NOT
  include `hotel_class` in the request.
- Merge dedup: a synthesized scenario where the same `property_token`
  appears in both responses collapses to one offer at the lower price.
- Merge dedup fallback: a scenario where tokens differ but
  `(name, lat, lon)` matches collapses to one offer.
- Sort applies to merged set, not per-source.
- Partial failure: one call fails (mocked 500), other succeeds, returns
  success envelope with `warnings` populated and only successful-side
  offers.
- Both fail: standard error envelope returned.
- `sources` correctly populated from the `prices` array; OTA names
  canonicalized (e.g. lowercase input `"booking.com"` → `"Booking.com"`).
- `bedrooms` / `bathrooms` / `sleeps` parsed correctly from
  `essential_info`. Test handles missing values (sleeps but no bedroom
  count) gracefully → null.

Expected new test count: ~18, taking total from 193 → ~211.

### 3.4 Documentation

- README.md:
  - Rename `search_hotels` → `search_stays` in the tool reference table.
  - Document the new `category` / `min_bedrooms` / `min_bathrooms`
    parameters.
  - Update the "What can I ask?" section with vacation rental examples.
  - **Honestly state in the docs that `sources` shows OTAs (Booking.com,
    Hotels.com, etc.), not Airbnb / VRBO directly.**
- AGENTS.md:
  - Rename references.
  - Note that `category="all"` makes 2 SerpAPI calls per query and
    affects quota burn.
- BACKLOG.md:
  - Rename "Hotel property_details follow-up" to "Stay property_details
    follow-up" and rename the proposed tool to `get_stay_details`.
- HOTELS-EXTENSION-SPEC.md and MIGRATION-FLI-SPEC.md:
  - Leave as historical artifacts (same precedent as the old phase plan).

### 3.5 Verify MCP Inspector

After the rename: three tools should appear under `trip-search` —
`search_flights`, `search_cheapest_dates`, `search_stays`. No
`search_hotels`.

---

## Acceptance criteria

1. `.venv/bin/python -m trip_search_mcp.server` starts cleanly
2. Claude Desktop's tools menu shows three tools: `search_flights`,
   `search_cheapest_dates`, `search_stays`
3. `search_stays(location="Tampere", check_in_date="2026-06-15",
   check_out_date="2026-06-18", adults=2)` returns a mixed list of
   hotels and vacation rentals
4. `search_stays(..., category="hotels")` returns only hotels, makes
   one SerpAPI call
5. `search_stays(..., category="vacation_rentals")` returns only
   vacation rentals, makes one SerpAPI call
6. `search_stays(..., category="all", min_bedrooms=2)` makes two calls;
   the inspected hotel request has NO `bedrooms` param, the rental
   request DOES
7. `search_stays(..., category="vacation_rentals", min_rating=4)` does
   NOT crash with a 400 from SerpAPI (regression for the Q4 finding)
8. Each offer has a `sources` field (`list[Source]`, empty for hotels
   in current fixtures)
9. Each rental offer has `bedrooms` / `bathrooms` / `sleeps` populated
   from `essential_info` when present
10. Results are deduped: a property appearing in both responses (or
    matching by `(name, lat, lon)` fallback) appears exactly once at
    the lower price
11. Results are sorted per `sort_by` across the merged set; `BEST` uses
    price ascending as tie-break
12. Partial failures surface in `warnings` on the success envelope;
    standard error envelope only when BOTH fail
13. README explicitly states that `sources` shows OTAs, not Airbnb/VRBO
14. ~211 tests pass (193 baseline + ~18 new); test count delta
    documented in the commit message
15. README, AGENTS.md, BACKLOG.md all updated

---

## Risks and known issues

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `category="all"` doubles quota burn | Certain | Low for personal use | 100/mo covers 50 merged queries before single-category queries eat in. TTLCache (300s) deduplicates repeated identical queries within a session. Documented in tool description. |
| `BEST` sort under merge is ambiguous | High | Low | Use price-ascending as tie-breaker. Documented in tool description so behavior is predictable. |
| `prices` array surfaces OTAs, not Airbnb/VRBO directly | Certain (per Phase 0 fixtures) | Medium | Be honest in README and tool description. Reframe the value as "cross-OTA price comparison" not "Airbnb vs VRBO". |
| Filter scoping bug → SerpAPI 400 if request-building is wrong | High during development, near-zero after | Medium | Phase 1 regression test (acceptance #7) covers this directly. |
| Token instability between modes (theoretical, untested) | Unknown | Low | Two-tier dedup covers both stable and unstable cases. Worst case: a few dupes show through, which the LLM can describe ("Same property listed twice with different prices"). |
| Renaming `HotelOffer` → `StayOffer` ripples through every test | Certain | Low | Mechanical rename. Run tests after; fix breaks immediately. |
| Renaming the tool breaks cached schemas in Claude Desktop and claude.ai | Certain | Low | Standard `⌘Q` + reopen, per AGENTS.md. |

---

## How to hand this to Claude Code

Phase 0 is done. Phase 1 is ready to execute. Fresh session prompt:

> Read SEARCH-STAYS-SPEC.md from the project root. Phase 0 is complete —
> the findings are baked into this spec, and the captured fixtures live
> at `tests/fixtures/serpapi_vacation_rentals_tampere.json` and
> `tests/fixtures/serpapi_hotels_tampere_compare.json`.
>
> Start Phase 1. Begin with section 1.1 (raw response model changes —
> add `SerpHotelPrice`, add `prices` and `essential_info` to
> `SerpHotelProperty`). Then 1.2 (output model rename + new fields).
> Stop and show me the diff before moving to 1.3 (the client
> orchestration changes) — that's where the filter-scoping regression
> needs the most care.
>
> Run the test suite after each section to catch regressions early.
> Existing 193 tests must keep passing throughout.

After Phase 1: pause for review, then proceed to Phase 2 (tool rename
and description rewrite), then Phase 3 (docs + acceptance checks).
