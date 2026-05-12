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
