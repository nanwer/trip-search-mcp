# Backlog

Items surfaced during live testing of Phases 1, 2, and 2.5 that didn't
block the migration close-out. Pick any of these up in a future phase.
Each entry is sized so a fresh Claude Code session can act on it cold.

---

## 1. Booking URL deep-linking (flights AND hotels)

**Today (flights):** Every `FlightOffer.booking_url` is the same generic
Google Flights search URL for the (origin, destination, dates) tuple.
Clicking it lands the user on the search results page; they then have to
find "the same offer" Claude told them about and click through.

**Today (hotels):** Every `HotelOffer.booking_url` is the same generic
Google Hotels search URL for the (location, check-in, check-out) tuple,
even though SerpAPI returns a `property_token` per result. A
`serpapi_property_details_link` follow-up call would surface direct
booking partner URLs for the specific property.

**Wanted:** A URL that opens the offer's specific booking flow on the
airline (or Google Flights') booking page.

**Why it's hard:** fli's `FlightResult` doesn't carry a `booking_token`
the way SerpAPI's response did. Google Flights' per-offer URL encodes a
booking token in its `?tfs=...` parameter, which fli would need to expose.
Two paths:

1. Upstream PR to fli to surface the booking token field (it's in the
   raw response). Cleanest, but blocks on someone else's review.
2. Synthesize the `?tfs=...` value ourselves from origin/destination/
   dates/airline-and-flight-numbers. Possible — Google's encoding is
   documented in their internal API — but fragile against changes.

Recommended: file an issue on `punitarani/fli` asking to expose the
booking token. While that's pending, leave `booking_url` as the generic
search URL.

---

## 2. Multi-airport / city codes

**Today:** Origin and destination must be 3-letter IATA airport codes
(`IAD`, `DCA`, `BWI`). The Pydantic validator regex `^[A-Z]{3}$` accepts
3-letter strings but rejects city codes (Google's `WAS`, `NYC`, `LON`)
because fli's `Airport` enum is airport-specific — `Airport["WAS"]`
raises `KeyError`.

**Wanted:** A user can ask "round-trip to Washington DC" and Claude can
pass `WAS` (city code), `IAD/DCA/BWI` (any of the airports), or have the
MCP auto-expand a city code to its constituent airports and merge results.

**Implementation sketch:**

- Maintain a small mapping `CITY_TO_AIRPORTS = {"WAS": ["IAD", "DCA", "BWI"], "NYC": ["JFK", "LGA", "EWR"], "LON": ["LHR", "LGW", "STN", "LCY"], ...}`.
- On the input side, accept either an airport IATA (existing path) or a
  city IATA (new path). Use a separate validator type or a model
  validator that resolves the city code at input time.
- Multi-airport searches require N searches (one per (origin, destination)
  combination if both are city codes) and a merge step. Cap at a sensible
  number of airports per side to avoid combinatorial blowup.
- Tool descriptions update to document the city-code support.

Watch out: SerpAPI accepted city codes natively, fli does not — this is
genuine new work, not just exposing a hidden flag.

---

## 3. Monitoring layer for deal hunting

**Today:** Each `search_flights` / `search_cheapest_dates` call is a
one-shot query. Users can't say "watch this route for the next two weeks
and tell me if the price drops below €600."

**Wanted:** A persistent monitor that:

- Captures a query (route, date range, max acceptable price, optional
  filters) and an alert threshold.
- Re-runs the query on a schedule (e.g., every 6h).
- Stores the price history per (route, dates) tuple.
- Emits an alert (MCP tool result, email, Slack, …) when the latest
  price crosses the threshold or hits a multi-day low.

**Implementation sketch:**

- New MCP tool: `watch_flight_price(query: ..., threshold: float, ...) -> watch_id`.
- New MCP tool: `list_active_watches() -> list[Watch]`.
- New MCP tool: `cancel_watch(watch_id)`.
- Backing store: SQLite next to the JSON log (`~/.flights-mcp/watches.db`).
- Scheduler: a separate background task in the FastMCP server, or a cron
  job that talks to the MCP via a subprocess invocation. Background task
  is cleaner — FastMCP supports app-level lifespan hooks.
- Quota-conscious: a watch is a recurring API call. With fli having no
  quota this is fine; if we ever swap providers again, watches become
  the dominant cost driver and need a budget control.

This is the largest of the three backlog items. It moves the project
from "search tool" to "deal-hunting agent."

---

## Suggested order

1. **Multi-airport / city codes** — small, contained, materially improves UX.
2. **Deep-linking** — depends on upstream; file the issue early so it can
   bake while you build other things.
3. **Monitoring** — most ambitious; tackle after the previous two land or
   when the personal motivation is high (it's the one most likely to pay
   for itself on a single good deal).
