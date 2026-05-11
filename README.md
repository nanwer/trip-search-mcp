# Flight Search MCP

Local-first MCP server wrapping Google Flights data via SerpAPI.

Exposes one tool, `search_flights`, that returns a ranked list of flight offers
for a route and date range. Phase 1 runs on stdio for development; Phase 2
will add HTTP transport for remote access.

## Quickstart

### 1. Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

Sign up at <https://serpapi.com> and grab an API key. The free tier covers
100 searches/month — plenty for personal trip planning. Copy `.env.example`
to `.env` and paste the key in.

```bash
cp .env.example .env
# Edit .env
```

| Variable | Required | Notes |
|---|---|---|
| `SERPAPI_KEY` | yes | From <https://serpapi.com/manage-api-key>. |
| `LOG_FILE_PATH` | no | Default `~/.flights-mcp/logs/flight-search.log`. Must be absolute. |
| `LOG_LEVEL` | no | Default `INFO`. |
| `CACHE_TTL_SECONDS` | no | Default `300`. |

### 3. Run tests

```bash
pytest
```

### 4. Start the server

```bash
set -a; source .env; set +a
python -m flights_mcp.server
```

The server speaks the MCP protocol over stdio.

### 5. Verify with MCP Inspector

```bash
npx @modelcontextprotocol/inspector python -m flights_mcp.server
```

Inspector will show the `search_flights` tool. Call it with:

```json
{
  "origin": "HEL",
  "destination": "IAD",
  "departure_date": "2026-05-18",
  "return_date": "2026-05-29",
  "adults": 1
}
```

Expect a `results` array with up to 3 round-trip offers (`max_results` is
capped at 5 for round-trip queries to keep upstream API costs predictable;
default is 3). For one-way queries omit `return_date` and you can ask for up
to 50 results in a single call.

## Architecture

See [SPEC.md](./SPEC.md) for the full functional spec.

```
search_flights() (MCP tool)
    │
    ├── SearchFlightsInput (Pydantic validation, round-trip cap enforcement)
    ├── TTLCache (canonical-key, 5-min TTL)
    └── SerpAPIClient
            ├── 1 GET /search?engine=google_flights        (one-way OR outbound)
            ├── N GET /search?...&departure_token=...      (round-trip return legs)
            └── normalize → list[FlightOffer]
```

For a round-trip search, SerpAPI's Google Flights endpoint returns a list of
outbound options each carrying a `departure_token`. To assemble each
round-trip offer we make a follow-up call per outbound to fetch its matching
return leg — so a round-trip search costs `1 + max_results` upstream calls,
which is why round-trip `max_results` is capped at 5.

## Phase 1 scope

In:
- Single tool, `search_flights`
- stdio transport
- SerpAPI Google Flights integration (one-way + round-trip)
- Structured error contract
- Local-time-with-IATA timestamp contract (ISO 8601, no offset)
- Response caching

Out (deferred):
- HTTP transport, Cloudflare Tunnel (Phase 2)
- Auth (Phase 2)
- `airport_search`, `flight_price_confirm`, `fare_calendar` (Phase 3–4)
- Booking — this server returns search results only

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
