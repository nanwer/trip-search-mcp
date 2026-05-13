# Flight & Hotel Search MCP

A Model Context Protocol server that lets Claude search live Google Flights
AND Google Hotels data. Plug it into Claude Desktop, Claude Code, or any
MCP-aware client, then ask in plain English.

- **Flights** — no API key required. Talks to Google Flights directly via
  the [fli](https://github.com/punitarani/fli) library.
- **Hotels** — optional, requires a free [SerpAPI](https://serpapi.com) key
  (free tier: 100 searches/month). The server starts fine without a key;
  the hotels tool surfaces a clear "set SERPAPI_KEY" message if called
  unconfigured, while flights keep working.

```
You:   Find me round-trips from Helsinki to Washington DC for May 18 returning May 29,
       one stop or fewer, leaving between 8am and 8pm.
Claude:[calls search_flights with max_stops="ONE_STOP_OR_FEWER" and
        departure_window="8-20", summarizes the cheapest options, offers
        Book on Google Flights links]
```

> **Heads up after every git pull / pip install:** Claude Desktop holds onto
> the MCP subprocess it spawned at launch. To pick up new code, **fully
> ⌘Q and reopen Claude Desktop** — closing the window isn't enough. See
> [docs/SETUP.md § Searches time out](./docs/SETUP.md#searches-time-out-for-4-minutes-or-hang-silently-after-pulling-new-code)
> for the diagnostic command.

---

## What you can ask Claude

Two tools (`search_flights` and `search_cheapest_dates`) cover a wide range
of real trip-planning questions. Ask in plain English; Claude picks the
right tool and fills in the filters.

### Specific-dates searches (uses `search_flights`)

**Simple round-trip**
> *"Find me round-trip flights from JFK to LHR, leaving July 12 and returning July 22."*

**One-way**
> *"What's the cheapest one-way from Seattle to Tokyo on October 4?"*

**Non-stop only**
> *"Show me only direct flights from Helsinki to JFK on May 18."* → `max_stops=NON_STOP`

**Time-of-day window — outbound only**
> *"I need to leave San Francisco between 6am and noon on Friday."* → `departure_window="6-12"` (matches 06:00–11:59; a noon flight is the cutoff, not included)

**Time-of-day window — separate outbound and return**
> *"HEL to IAD May 18 returning May 29. Morning outbound (8am–noon), evening return (8pm–11pm)."* → `departure_window="8-12"`, `inbound_window="20-23"` (outbound 08:00–11:59, inbound 20:00–22:59 — both are exclusive on the right edge)

**Return-leg-only constraint**
> *"Any outbound to Tokyo on March 5, but I need to land back in NYC before 6pm on the 19th — so the return must leave Tokyo in the morning."* → `inbound_window="6-12"`

**Airline preference (loyalty programs)**
> *"Find flights to Bangkok in November, prefer Star Alliance — United, Lufthansa, Singapore, or Thai."* → `airlines=["UA", "LH", "SQ", "TG"]` (returns offers where ANY leg is operated by one of these; mixed-carrier itineraries with non-Star-Alliance codeshares can still appear, so Claude will summarize each result's airlines)

**Avoiding a carrier isn't a native filter.** The `airlines` parameter is inclusion-only and matches "any segment operated by any of these," so even listing the carriers you DO want can't fully exclude a specific airline (it could still appear on a codeshare leg of a returned offer). Workaround: Claude reviews the response and skips offers whose `airlines` list contains the one you want to avoid.

**Premium cabins**
> *"Business-class round-trip from Boston to Singapore, January 15 to January 30."* → `cabin_class="BUSINESS"`

**Family travel**
> *"4 of us flying SFO to MCO in December — 2 adults and 2 kids under 12."* → `adults=2`, `children=2`

**Capped results for a focused list**
> *"Just give me the top 5 cheapest options from HEL to LHR for next weekend."* → `max_results=5`

### Hotel searches (uses `search_hotels`, requires SERPAPI_KEY)

**Simple city search**
> *"Find me hotels in Tampere from June 15 to June 18, 2 adults."*

**Budget + quality floor**
> *"Hotels in Lisbon next weekend, 2 adults, under €150/night, at least 4 stars."* → `max_price_per_night=150`, `min_rating=4`

**Amenity requirements**
> *"Hotels in central London for 3 nights starting October 12, must have pool and gym."* → `required_amenities=["pool", "gym"]` (case-insensitive substring match; "wifi" matches "Free Wi-Fi" because we strip punctuation)

**Sort by review score**
> *"Find the best-reviewed hotels in Kyoto for the first week of November, 1 traveler."* → `sort_by="REVIEW_SCORE"`

**Family**
> *"Looking for a family hotel in Orlando from July 5-12: 2 adults, 2 kids, one room."* → `adults=2`, `children=2`, `rooms=1`

### Flexible-dates searches (uses `search_cheapest_dates`)

**Cheapest week within a month**
> *"I want to fly from London to Tokyo for about 10 days sometime in March. Which dates are cheapest?"* → `start_date="2026-03-01"`, `end_date="2026-03-31"`, `trip_duration=10`

**One-way departures across a date range**
> *"What's the cheapest day to fly one-way from HEL to BCN between May 15 and June 5?"* → `is_round_trip=false`

**Tight window, see what shifting a day does**
> *"Compare HEL→IAD round-trip prices for May 18 ± 3 days, all 11-night trips."* → `start_date="2026-05-15"`, `end_date="2026-05-21"`, `trip_duration=11`

**Sabbatical / extended trip**
> *"I want a 3-month trip to Australia leaving sometime between June and September. When's it cheapest?"* → `trip_duration=90`

**Filtered date search**
> *"Cheapest dates from SFO to NRT in October, business class only, non-stop."* → combines `cabin_class="BUSINESS"` + `max_stops="NON_STOP"` with the date range

### Multi-step planning (combining both tools)

This is where the MCP earns its keep — Claude will naturally chain the two
tools when you give it a trip-planning problem.

> **You:** *"I want to spend two weeks in Lisbon. When's the cheapest time to go in the next 3 months, and what does the cheapest itinerary look like?"*
>
> **Claude:**
> 1. Calls `search_cheapest_dates` over the next 90 days, 14-night trip → identifies cheapest week.
> 2. Calls `search_flights` for those specific dates → returns the actual airlines, times, and a Google Flights booking link.
> 3. Summarizes both in one answer with a "shift dates by 2 days and save $X" callout.

> **You:** *"Help me plan a trip that connects two friends — Helsinki for the first week of May, then Tokyo for the second week."*
>
> **Claude:** Calls `search_flights` twice (origin → HEL, HEL → NRT or similar), summarizes the multi-leg plan.

> **You:** *"I want to spend 3 nights in Tampere in June. Find me a flight and a hotel — keep the hotel cheap but at least 4 stars."*
>
> **Claude:**
> 1. Calls `search_flights` for your origin → TMP / HEL with the dates you mention.
> 2. Calls `search_hotels(location="Tampere", check_in_date=..., check_out_date=..., min_rating=4, sort_by="PRICE_LOW")`.
> 3. Returns a combined plan: cheapest flight option + cheapest 4-star hotel + total trip cost.
> 4. (Requires `SERPAPI_KEY` configured. Without it, Claude still finds the flight and clearly says "hotel search isn't enabled — set SERPAPI_KEY to add hotels.")

### Things this MCP does NOT do (yet)

- **Booking.** Every offer returns a `booking_url` pointing at Google Flights' search page; click through and complete the booking with the airline or an OTA.
- **Hotels, cars, activities.** Search only. Different APIs.
- **Multi-airport "Washington DC" expansion.** Use the specific IATA airport code (`IAD`, `DCA`, or `BWI`). Claude usually picks the most-likely airport from context.
- **Open-jaw / multi-city itineraries** in a single call. Workaround: ask Claude to call `search_flights` once per leg.

---

For a verbose, step-by-step walkthrough including troubleshooting see
[docs/SETUP.md](./docs/SETUP.md).

## Install (one-time, ~3 minutes)

You need Python 3.12 or newer. The cleanest path is `uv`
(<https://docs.astral.sh/uv>), but plain `pip` works too.

**Option A — `uv` (recommended):**
```bash
git clone https://github.com/nanwer/flights-mcp.git
cd flights-mcp
uv venv
uv pip install -e .
```

**Option B — `pip`:**
```bash
git clone https://github.com/nanwer/flights-mcp.git
cd flights-mcp
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

No API key, no `.env` setup, no signup. The server talks directly to Google
Flights through fli's reverse-engineered endpoints.

### Connect Claude

#### Claude Desktop (macOS / Windows)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows). Add a
`mcpServers` entry, replacing the ABSOLUTE path with your own:

```json
{
  "mcpServers": {
    "flights": {
      "command": "/ABSOLUTE/PATH/TO/flights-mcp/.venv/bin/python",
      "args": ["-m", "flights_mcp.server"]
    }
  }
}
```

Restart Claude Desktop. The `search_flights` tool will appear in the
hammer/tools menu inside a chat. Ask: *"Find flights from HEL to IAD on May 18,
returning May 29."*

#### Claude Code (CLI)

```bash
claude mcp add flights \
  -- /ABSOLUTE/PATH/TO/flights-mcp/.venv/bin/python -m flights_mcp.server
```

Then start a Claude Code session and the tool is available.

---

## Tool reference

Three tools. Claude reads richer descriptions than these tables; this is the
short version.

### `search_flights` — flight options for specific dates

| Parameter | Default | Notes |
|---|---|---|
| `origin` | required | 3-letter IATA airport code (`HEL`, `JFK`). |
| `destination` | required | Same format as origin. |
| `departure_date` | required | `YYYY-MM-DD`. Must be today (UTC) or later. |
| `return_date` | optional | Omit for one-way. |
| `adults` | 1 | 1–9. |
| `children` | 0 | 0–9, age 2–11. |
| `infants` | 0 | 0–9, lap infants (must be ≤ adults). |
| `cabin_class` | `ECONOMY` | `ECONOMY` / `PREMIUM_ECONOMY` / `BUSINESS` / `FIRST`. |
| `max_stops` | `ANY` | `ANY` / `NON_STOP` / `ONE_STOP_OR_FEWER` / `TWO_OR_FEWER_STOPS`. "Or fewer" semantics. |
| `departure_window` | none | `"HH-HH"` 24-hour local time, e.g. `"8-20"`. **Outbound leg only.** See *Window semantics* below. |
| `inbound_window` | none | Same format as `departure_window`, applied to the **return leg**. Has no effect on one-way searches. |
| `airlines` | none | List of IATA airline codes, e.g. `["AY", "FI"]`. See *Airline filter semantics* below. |
| `max_results` | 20 | 1–50. |

### `search_cheapest_dates` — date-flex price grid

| Parameter | Default | Notes |
|---|---|---|
| `origin` | required | 3-letter IATA airport code. |
| `destination` | required | Same format as origin. |
| `start_date` | required | Earliest acceptable departure date. |
| `end_date` | required | Latest acceptable departure date. |
| `trip_duration` | conditional | Days. **Required when `is_round_trip=true`**, 1–365. |
| `is_round_trip` | `false` | When true, output includes a paired `return_date` per entry. |
| `passengers` | 1 | 1–9. (Single field; no per-traveler-type breakdown.) |
| `cabin_class` | `ECONOMY` | Same enum as `search_flights`. |
| `max_stops` | `ANY` | Same enum as `search_flights`. |
| `departure_window` | none | Same format and semantics as on `search_flights`. |
| `airlines` | none | Same list semantics as on `search_flights`. |

Returns a `results` array of `{departure_date, return_date, price, currency}`
entries sorted cheapest first. `return_date` is `null` for one-way.

### `search_hotels` — Google Hotels for specific dates *(requires SERPAPI_KEY)*

| Parameter | Default | Notes |
|---|---|---|
| `location` | required | City, neighborhood, or area. Free-text. |
| `check_in_date` | required | `YYYY-MM-DD`. Today (UTC) or later. |
| `check_out_date` | required | Must be **strictly after** `check_in_date`. |
| `adults` | 2 | 1–10. |
| `children` | 0 | 0–10. |
| `rooms` | 1 | 1–10. |
| `min_rating` | none | Star rating 1–5. Properties without a star rating are excluded when set. |
| `min_review_score` | none | Google's native 0–5 review score (NOT 0–10). Properties without a review score are excluded when set. |
| `max_price_per_night` | none | Per-night ceiling in the response currency (EUR by default — matches flights' typical response currency for European-IP users). |
| `required_amenities` | none | List of free-text amenity names. Best-effort substring match, case- and punctuation-insensitive ("wifi" matches "Free Wi-Fi"). |
| `sort_by` | `BEST` | `BEST` / `PRICE_LOW` / `PRICE_HIGH` / `RATING` / `REVIEW_SCORE`. |
| `max_results` | 10 | 1–25. |
| `currency` | `EUR` | ISO 4217 three-letter code (`"USD"`, `"JPY"`, `"GBP"`, …). Pass it to match the user's stated location/budget — `max_price_per_night` is interpreted in this currency, so mixing them silently corrupts the budget filter. |

Returns a `results` array of `HotelOffer` entries with `offer_id`, `name`,
nights, `price_total`, `price_per_night`, `currency`, `star_rating`,
`review_score` (0–5 scale), `review_count`, GPS coordinates, `amenities`,
`images` (up to 5 URLs), `description`, `hotel_type`, and `booking_url`
(deep link to the specific property's Google Hotels entity page, with
check-in/check-out pre-filled).

**`address` is always null** on hotel offers — SerpAPI's google_hotels
list endpoint doesn't carry per-property addresses. Use `latitude` /
`longitude` for location; a future property_details follow-up call would
surface postal addresses (tracked in BACKLOG).

### Window semantics

`departure_window` and `inbound_window` are **inclusive of the start hour
and exclusive of the end hour**:

- `"8-20"` matches departures from `08:00` through `19:59` local time.
- A `20:00` or `20:30` departure does NOT match `"8-20"`. Use `"8-21"` if
  you want to include the 20:00 hour.

This matches how most people read "between 8 and 8" — "8pm" is the cutoff,
not the last hour included.

### Airline filter semantics

`airlines=["FI"]` returns offers where **at least one segment** is operated
by Icelandair. It does NOT restrict to Icelandair-only itineraries:

- Pure Finnair: appears if and only if `"AY"` is in the list (won't appear if you only listed `"FI"`).
- Mixed Icelandair + American codeshare: appears if `"FI"` OR `"AA"` is in the list.

There is no native "exclude this airline" filter. To bias against a carrier,
list the carriers you DO want.

### Response shape

Each `search_flights` offer has: `offer_id`, `total_price`, `currency`,
`price_per_adult`, `airlines`, `validating_airline`, `outbound`, `inbound`
(null for one-way), `booking_url` (Google Flights link with the search
pre-filled), plus nullable fields `baggage_allowance`, `last_ticketing_date`,
`seats_available` (these come back `null` because fli doesn't surface them).

The `currency` field reflects whatever Google Flights returns for your
request region (typically EUR for European IPs, USD for US IPs). You can't
pick it.

Errors on any tool: `{"error": {"code": ..., "message": ..., "retryable": ...}}`.
The five codes are `invalid_input`, `no_results`, `rate_limited`,
`upstream_error`, and `auth_failed` (the last only fires from `search_hotels`
when `SERPAPI_KEY` is missing or rejected).

---

## Architecture

```
search_flights()    search_cheapest_dates()    search_hotels()
        │                    │                       │
        └── Pydantic input validation, tool-namespaced cache key ──┐
                                                                   │
        FliClient (no auth, fli library)            SerpAPIHotelsClient
              │                                       (optional, SERPAPI_KEY)
              ├── fli.SearchFlights → list[FlightOffer]      │
              └── fli.SearchDates   → list[DatePriceOffer]   └── google_hotels → list[HotelOffer]
```

The flights backend (`fli`) handles HTTP, retries, and rate-limit backoff
internally. The hotels backend uses `httpx` directly against SerpAPI's
google_hotels endpoint.

The server starts even when `SERPAPI_KEY` is unset — flights work key-free.
When the key is missing, `_HOTELS_CLIENT` is `None` and the `search_hotels`
tool returns a structured `auth_failed` envelope at call time. Same
process, three tools, two backends.

---

## Development

```bash
pytest                                            # 186 tests, all fixture-driven, no live API calls
.venv/bin/python scripts/verify_fli.py            # capture fresh real-data fixtures (1 SearchFlights + 1 SearchDates call)
.venv/bin/python scripts/verify_serpapi_hotels.py # capture a fresh hotels fixture (1 SerpAPI call)
```

See [SPEC.md](./SPEC.md) for the original Phase 1 spec and
[MIGRATION-FLI-SPEC.md](./MIGRATION-FLI-SPEC.md) for the fli migration plan.

---

## Project layout

```
src/flights_mcp/
├── server.py              FastMCP entry point
├── logging_config.py      JSON-line file logger
├── errors.py              Error codes and envelope
├── models.py              Pydantic I/O models
├── cache.py               TTL response cache
├── tools/search_flights.py
└── fli_backend/
    ├── client.py          fli wrapper + filter construction + error mapping
    └── normalize.py       FlightResult → FlightOffer
```

---

## Limitations & roadmap

- **Search only, no booking.** This MCP returns search results with a
  `booking_url` pointing at Google Flights' search page. To actually book,
  click through and follow the airline link.
- **stdio transport only.** Works with Claude Desktop, Claude Code, and any
  local stdio MCP client. HTTP transport (for claude.ai web) is a future
  phase.
- **Currency not user-controllable.** Google Flights picks based on your IP
  region. The `currency` field on each offer tells you what you got.
- **fli depends on Google's internal API.** If Google changes their
  endpoints, fli ships a fix (typically within days based on their release
  cadence). For the typical week of personal trip planning this is
  reasonable. To roll back to the previous SerpAPI-based version,
  `git checkout pre-fli-migration`.
- **`search_cheapest_dates` tool** (date-flex grid) is planned next.
