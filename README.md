# Flight Search MCP

A Model Context Protocol server that lets Claude search live Google Flights
data. Bring your own SerpAPI key (free tier covers 100 searches/month). Plug
the server into Claude Desktop, Claude Code, or any MCP-aware client, then ask
Claude to find you flights in plain English.

```
You:   Find me cheap round-trips from Helsinki to Washington DC for May 18 – 29.
Claude:[calls search_flights, summarizes the cheapest options, asks if you want details]
```

---

For a verbose, step-by-step walkthrough including troubleshooting see
[docs/SETUP.md](./docs/SETUP.md).

## Install (one-time, ~5 minutes)

### 1. Get a SerpAPI key

1. Sign up at <https://serpapi.com>. The free tier gives 100 searches/month —
   enough for a few trips' worth of planning.
2. Copy your API key from <https://serpapi.com/manage-api-key>.

### 2. Install the server

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
pip install -e .
```

### 3. Configure environment

Copy the example file and paste your SerpAPI key into it:

```bash
cp .env.example .env
# Edit .env, replace `your-serpapi-key-here` with the key from step 1
```

### 4. Connect Claude

#### Claude Desktop (macOS / Windows)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows). Add a
`mcpServers` entry, replacing the two ABSOLUTE paths with your own:

```json
{
  "mcpServers": {
    "flights": {
      "command": "/ABSOLUTE/PATH/TO/flights-mcp/.venv/bin/python",
      "args": ["-m", "flights_mcp.server"],
      "env": {
        "SERPAPI_KEY": "paste-your-key-here"
      }
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
  --env SERPAPI_KEY=paste-your-key-here \
  -- /ABSOLUTE/PATH/TO/flights-mcp/.venv/bin/python -m flights_mcp.server
```

Then start a Claude Code session and the tool is available.

#### Other MCP clients

Any client that supports stdio transport. Point it at
`/ABSOLUTE/PATH/TO/flights-mcp/.venv/bin/python -m flights_mcp.server`
with `SERPAPI_KEY` set in the environment.

---

## Tool reference

One tool, `search_flights`. Claude reads a richer description than this; the
short version:

| Parameter | Default | Notes |
|---|---|---|
| `origin` | required | 3-letter IATA airport code (`HEL`, `JFK`) or city code (`NYC`, `LON`, `WAS`). |
| `destination` | required | Same format as origin. |
| `departure_date` | required | `YYYY-MM-DD`. Must be today (UTC) or later. |
| `return_date` | optional | Omit for one-way. |
| `adults` | 1 | 1–9. |
| `children` | 0 | 0–9, age 2–11. |
| `infants` | 0 | 0–9, lap infants (must be ≤ adults). |
| `cabin_class` | `ECONOMY` | `ECONOMY` / `PREMIUM_ECONOMY` / `BUSINESS` / `FIRST`. |
| `currency` | `USD` | ISO 4217 currency code. |
| `non_stop_only` | `false` | Filter to direct flights only. |
| `max_results` | 3 (round-trip) / 20 (one-way) | Round-trip is capped at 5 (each result costs 1 extra API call); one-way is capped at 50. |

Responses come back as a `results` array of offers. Each offer has
`offer_id`, `total_price`, `currency`, `airlines`, `validating_airline`,
`outbound`, `inbound` (null for one-way), plus nullable fields like
`baggage_allowance`, `last_ticketing_date`, `seats_available`.

Errors are structured: `{"error": {"code": ..., "message": ..., "retryable": ...}}`.
The codes are documented in `src/flights_mcp/errors.py` —
`invalid_input`, `no_results`, `auth_failed`, `quota_exceeded`,
`rate_limited`, `upstream_error`.

---

## Quota math

SerpAPI free tier = 100 searches/month. Cost per `search_flights` call:

| Query shape | Upstream calls |
|---|---|
| One-way | 1 |
| Round-trip, `max_results=1` | 2 |
| Round-trip, `max_results=3` (default) | 4 |
| Round-trip, `max_results=5` (cap) | 6 |

A 5-minute response cache means identical follow-up queries within a session
are free. Most flight-planning sessions iterate on dates and stay within the
free tier comfortably.

---

## Architecture

```
search_flights() (MCP tool)
    │
    ├── SearchFlightsInput (Pydantic validation, round-trip cap enforcement)
    ├── TTLCache (canonical-key, 5-min TTL)
    └── SerpAPIClient
            ├── 1 GET /search?engine=google_flights        (one-way OR outbound)
            ├── N GET /search?...&departure_token=...      (round-trip return legs, parallel)
            └── normalize → list[FlightOffer]
```

For a round-trip search, SerpAPI's Google Flights endpoint returns a list of
outbound options each carrying a `departure_token`. To assemble each
round-trip offer we make a follow-up call per outbound to fetch its matching
return leg. Return-leg calls run in parallel, so a round-trip search is
~1× single-call latency, not N×.

---

## Development

```bash
pytest                       # 73 tests, all fixture-driven, no live API calls
.venv/bin/python scripts/verify_serpapi.py   # capture a fresh real-data fixture (1 SerpAPI call)
```

See [SPEC.md](./SPEC.md) for the original Phase 1 functional spec and
[docs/superpowers/plans/](./docs/superpowers/plans/) for the implementation plan.

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
└── serpapi/
    ├── client.py          HTTP layer + error mapping
    ├── normalize.py       SerpAPI raw → clean output models
    └── raw.py             Pydantic types for parsing SerpAPI's response
```

---

## Limitations & roadmap

- **Search only, no booking.** This MCP returns search results with an
  `offer_id` (SerpAPI booking_token). To actually book, follow the airline
  link in Google Flights or a travel agency.
- **stdio transport only.** Works with Claude Desktop, Claude Code, and any
  local stdio MCP client. HTTP transport (for claude.ai web) is a future
  phase.
- **Single tool.** `airport_search`, `flight_price_confirm`, `fare_calendar`
  are planned for later phases.
