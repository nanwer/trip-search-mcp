# Backlog

All 5 items from the original backlog (Phases 1, 2, 2.5) have shipped.
This file records what landed and surfaces any new follow-ups that
came out of the work. Add new items to the bottom; mark them with
`### N. Title` headers as before.

---

## Shipped during the "do them all" pass

### 1. Multi-airport / city codes — SHIPPED (`53444f4`)

Origin and destination on `search_flights` and `search_cheapest_dates`
now accept 3-letter city codes (`WAS`, `NYC`, `LON`, `TYO`, …) in
addition to airport codes. City codes auto-expand to the metro's
busiest 3 airports and fan out in parallel; results merge under one
ranked list (cheaper variant wins on dedup). 27 cities covered in
`src/trip_search_mcp/cities.py`; adding more is a one-line edit.

### 2. Booking URL deep-linking (flights) — DRAFTED, NOT FILED

Auto-mode classifier blocked filing a GitHub issue under Nophil's
identity to an external repo. The drafted issue body is committed at
`docs/upstream/fli-booking-token-issue.md`; one-line filing command:

```bash
gh issue create --repo punitarani/fli \
  --title "Feature: Expose Google Flights booking token..." \
  --body-file docs/upstream/fli-booking-token-issue.md
```

Until fli surfaces the booking token, `FlightOffer.booking_url` stays
as a generic search URL.

### 3. Stay property_details follow-up — SHIPPED (`656d57c`)

New `get_stay_details` tool — pass a `property_token` from a prior
`search_stays` result, get back rich detail: long-form description,
~14 nearby places, and a `booking_partners` list where each entry
includes a `link` straight to the partner's booking flow (via
Google's `/travel/clk?` redirector).

**Scope correction from the original backlog framing:** the response
does NOT carry a postal address (SerpAPI's property_details endpoint
simply doesn't expose one). The deliverable is "per-partner direct
booking URLs + rich detail", not "address + booking partners".

### 4. Direct Airbnb backend — SHIPPED (`225bcf8`)

New `category="airbnb"` on `search_stays` bypasses SerpAPI entirely
and queries Airbnb directly via `pyairbnb`. Geocoding (location → bbox)
uses OpenStreetMap Nominatim (free, no API key). Default
`category="all"` continues to use SerpAPI only — Airbnb is opt-in to
avoid degrading the common case with pyairbnb's higher fragility.

`pyairbnb` is a solo-maintainer scraper, so this is intentional risk;
pinned to `>=2.2.0,<3.0` to prevent breaking-release surprises.

### 5. Monitoring layer for deal hunting — SHIPPED (`953e04a`)

Three new tools — `watch_flight_price`, `list_active_watches`,
`cancel_watch` — back by SQLite at `~/.trip-search-mcp/watches.db`.
Watches persist across Claude Desktop restarts.

**Design decision:** no separate background daemon. The MCP server is
stdio-only — it only runs while Claude Desktop has the subprocess
alive. Instead we use lazy refresh: every call to
`list_active_watches` re-runs any watch whose latest check is older
than `refresh_after_hours` (default 6h) and surfaces alerts in the
response.

The server now exposes **7 MCP tools total**: search_flights,
search_cheapest_dates, search_stays, get_stay_details,
watch_flight_price, list_active_watches, cancel_watch.

---

## Follow-ups surfaced during the pass

### 6. Address from a separate geocoding step (low priority)

`StayOffer.address` and `StayDetails.address` are still null —
SerpAPI's google_hotels endpoints simply don't carry one. A future
enhancement could reverse-geocode the GPS coordinates we already have
(via Nominatim, which we now use for the Airbnb backend) to surface
the postal address as a best-effort field. Low priority — Claude can
already communicate location via lat/long + nearby_places.

### 7. Provider-preference filtering on `sources` (low priority)

Search-time `sources` is post-fetch; we could let users say "only show
me properties bookable through Booking.com" and filter after
normalize. Doable in ~30 lines of normalize code. Hold for a real
user request.

### 8. Watch alert via email or push (medium priority)

Today, alerts surface only when the user (or Claude) calls
`list_active_watches`. A daemon-mode that pushes alerts via email or
ntfy would be more "set and forget", but it requires either an
always-on process outside Claude Desktop or a webhook the MCP can
post to. Either way, this is a separate piece of infrastructure
(cron + Python entry point + SMTP/ntfy creds). Pick up when the
current "I'll ask Claude every morning" pattern gets tedious.

### 9. fli upstream — booking token, currency control, city codes

Three issues worth filing on `punitarani/fli` when there's a window:

- Expose Google's per-offer booking token (drafted at
  `docs/upstream/fli-booking-token-issue.md`).
- Expose Google Flights' currency override (today the response
  currency follows IP geolocation; users can't pick).
- Native city-code support (we work around with our own expansion
  layer; doing it upstream would be cleaner).


---

## Trip-planning expansion (5 tracks)

Tracked separately in `TRIP-PLANNING-EXPANSION-SPEC.md`. Status:

| Track | Tool | Status |
|---|---|---|
| C | `get_weather_forecast` | **SHIPPED** (Open-Meteo, no API key) |
| B | `convert_currency` | **SHIPPED** (ECB feed, no API key) |
| E | `search_events` | **SHIPPED** (SerpAPI google_events, ticket-vendor multi-source) |
| D | `search_activities` | **SHIPPED** (SerpAPI Tripadvisor ssrc=A) |
| F | `get_activity_details` | **NOT VIABLE** (Phase 0 verdict — see below) |

Each track is its own Claude Code session per the spec. See the spec's
"How to hand this to Claude Code" section for hand-off prompts.


---

### Track F closure note (added 2026-05-13)

`get_activity_details` was originally planned as the activity drill-down
tool — pass a `place_id` from a `search_activities` result, get back
price + duration + direct Viator URL + long-form description.

Phase 0 verification against SerpAPI's `engine=tripadvisor_place` for two
real place_ids (one ATTRACTION, one ATTRACTION_PRODUCT) returned only:

```
{
  "place_result": {
    "type": "hotel",          // always "hotel" — incorrect for activities
    "images": [<20 image URLs on hotlink-protected CDN>]
  }
}
```

NO price. NO duration. NO description. NO Viator URL. NO booking info.
The endpoint is essentially a photo-gallery endpoint, and the photos are
hotlink-protected (same CDN family as the hotel images we already chose
not to render).

**Verdict: not viable as scoped.** Shipping a drill-down tool that
returns only image URLs (which can't be rendered anyway) would be
misleading. Closed without code.

If a future SerpAPI release adds the rich fields, or if a different
provider (Viator's own API, Tripadvisor Content API directly) surfaces
them, this is worth revisiting. Fixture captured at
`tests/fixtures/serpapi_tripadvisor_place_details.json` for future
comparison.
