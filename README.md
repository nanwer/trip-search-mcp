# Flight Search MCP

A Model Context Protocol server that lets Claude search live Google Flights
data. **No API key required** — talks to Google Flights directly via the
[fli](https://github.com/punitarani/fli) library. Plug the server into Claude
Desktop, Claude Code, or any MCP-aware client, then ask Claude to find you
flights in plain English.

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

**Time-of-day window — outbound**
> *"I need to leave San Francisco between 6am and noon on Friday."* → `departure_window="6-12"`

**Time-of-day window — separate outbound and return**
> *"HEL to IAD May 18 returning May 29. I want morning outbounds (8am–noon) but I'm fine with red-eyes back (after 8pm)."* → `departure_window="8-12"`, `inbound_window="20-23"`

**Airline preference (loyalty programs)**
> *"Find flights to Bangkok in November, but only Star Alliance carriers — United, Lufthansa, Singapore, or Thai."* → `airlines=["UA", "LH", "SQ", "TG"]`

**Avoid specific carriers**
> *"Anything from LAX to SYD in August, but not Qantas."*
> (Claude will list the other major carriers as `airlines=[...]` — exclusion isn't a native filter)

**Premium cabins**
> *"Business-class round-trip from Boston to Singapore, January 15 to January 30."* → `cabin_class="BUSINESS"`

**Family travel**
> *"4 of us flying SFO to MCO in December — 2 adults and 2 kids under 12."* → `adults=2`, `children=2`

**Capped results for a focused list**
> *"Just give me the top 5 cheapest options from HEL to LHR for next weekend."* → `max_results=5`

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

One tool, `search_flights`. Claude reads a richer description than this; the
short version:

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
| `departure_window` | none | `"HH-HH"` 24-hour local time, e.g. `"6-20"`. Applies to both legs. |
| `airlines` | none | List of IATA airline codes to restrict to, e.g. `["AY", "FI"]`. |
| `max_results` | 20 | 1–50. |

Responses come back as a `results` array of offers. Each offer has
`offer_id`, `total_price`, `currency`, `airlines`, `validating_airline`,
`outbound`, `inbound` (null for one-way), `booking_url` (Google Flights
link), plus nullable fields (`baggage_allowance`, `last_ticketing_date`,
`seats_available` — these come up `null` because fli doesn't surface them).

The `currency` in the response is whatever Google Flights returns for your
region (typically EUR for European IPs, USD for US IPs). You can't pick it.

Errors are structured: `{"error": {"code": ..., "message": ..., "retryable": ...}}`.
The four codes are `invalid_input`, `no_results`, `rate_limited`,
`upstream_error`.

---

## Architecture

```
search_flights() (MCP tool)
    │
    ├── SearchFlightsInput (Pydantic validation)
    ├── TTLCache (canonical-key, 5-min TTL)
    └── FliClient
            ├── fli.search.SearchFlights (one upstream call, round-trip pairs in tuples)
            └── normalize → list[FlightOffer]
```

`fli` handles HTTP, retries, and rate-limit backoff internally. The MCP
server's only job is shaping requests and translating responses into our
provider-neutral output models.

---

## Development

```bash
pytest                                       # 79 tests, all fixture-driven, no live API calls
.venv/bin/python scripts/verify_fli.py       # capture fresh real-data fixtures (1 SearchFlights + 1 SearchDates call)
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
