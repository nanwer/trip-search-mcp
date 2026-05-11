# Flight Search MCP

Local-first MCP server wrapping the Amadeus Flight Offers Search API.

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

Copy `.env.example` to `.env` and fill in your Amadeus Self-Service credentials.

```bash
cp .env.example .env
# Edit .env
```

| Variable | Required | Notes |
|---|---|---|
| `AMADEUS_CLIENT_ID` | yes | From the Amadeus Self-Service workspace. |
| `AMADEUS_CLIENT_SECRET` | yes | From the same workspace. |
| `AMADEUS_ENV` | yes | `test` or `production`. |
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

Expect a `results` array. If you see `{"error": {"code": "no_results", ...}}`
in the test environment, the route may not be in Amadeus's cached subset — try
JFK, LAX, LHR, CDG, or any pair from amadeus4dev/data-collection.

## Architecture

See [SPEC.md](./SPEC.md) for the full functional spec.

```
search_flights() (MCP tool)
    │
    ├── SearchFlightsInput (Pydantic validation)
    ├── TTLCache (canonical-key, 5-min TTL)
    └── AmadeusClient
            ├── TokenCache (OAuth, async-lock-protected refresh)
            ├── GET /v2/shopping/flight-offers
            └── normalize_offers() → list[FlightOffer]
```

## Phase 1 scope

In:
- Single tool, `search_flights`
- stdio transport
- Test-env Amadeus integration
- Structured error contract
- Local-time-with-IATA timestamp contract
- Response caching, token caching with refresh lock

Out (deferred):
- HTTP transport, Cloudflare Tunnel (Phase 2)
- Auth (Phase 2)
- `airport_search`, `flight_price_confirm`, `fare_calendar` (Phase 3–4)
- Booking — Self-Service cannot issue tickets, ever

## Project layout

```
src/flights_mcp/
├── server.py              FastMCP entry point
├── logging_config.py      JSON-line file logger
├── errors.py              Error codes and envelope
├── models.py              Pydantic I/O models
├── cache.py               TTL response cache
├── tools/search_flights.py
└── amadeus/
    ├── client.py          HTTP layer + error mapping
    ├── normalize.py       Raw → clean
    └── token.py           OAuth + async refresh lock
```
