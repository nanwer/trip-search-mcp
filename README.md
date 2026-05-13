# trip-search-mcp

**Let Claude plan trips for you, in plain English.** Flights, hotels,
vacation rentals (including Airbnb), and persistent price watches —
all from one chat.

This is a small program that runs on your computer alongside Claude Desktop.
Once it's set up, you can ask Claude things like:

> *"Find me round-trip flights from Helsinki to Washington DC for May 18,
> returning May 29. One stop or fewer. Cheapest first."*
> *(Tip: just say "Washington DC" — Claude expands it to IAD/DCA/BWI in parallel.)*

> *"What's the cheapest week to fly from London to Tokyo in March for a
> 10-day trip?"*

> *"Find me a place to stay in Tampere from June 15 to 18, 2 adults, at
> least 4 stars or strong reviews, with pool and wifi. Cheapest first."*

> *"Find me an Airbnb in Lisbon for 4 nights from October 12, 2 bedrooms minimum."*

> *"Watch flights HEL → IAD on May 18 and tell me if it drops below €600."*
> *(Later: "any deals?" — Claude re-runs all your watches and reports alerts.)*

Claude does the search live (no stale data), summarizes the results, and
gives you clickable booking links. Seven tools total — see "What can I
ask?" below for the full set.

---

## Before you start

You need:

- **A computer** running **macOS, Windows, or Linux**.
- **[Claude Desktop](https://claude.ai/download)** installed and signed in.
- **About 5 minutes** the first time.

You'll also install Python 3.12 along the way if you don't already have it.
We'll walk you through that.

You do **NOT** need:

- An account anywhere except Claude.
- A credit card.
- An API key — *unless* you want hotel search (free SerpAPI account,
  100 searches/month, takes 2 minutes — covered at the end as an
  optional step).

---

## Install — step by step

Everything below happens in your computer's **Terminal app** (macOS/Linux)
or **PowerShell** (Windows). Open it before you start.

> **Don't know what a terminal is?**
> macOS: press ⌘+Space, type "Terminal", press Enter.
> Windows: press Win key, type "PowerShell", press Enter.
> Linux: you know.

### 1. Install Python 3.12 (skip if you already have it)

Check first:

```bash
python3 --version
```

If you see `Python 3.12.x` or higher, skip to step 2.

If not, the easiest installer is **uv** — one line:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

This installs `uv`, which manages Python versions for you. Open a fresh
terminal window after it finishes so the `uv` command is on your path.

### 2. Download this project

```bash
git clone https://github.com/nanwer/trip-search-mcp.git
cd trip-search-mcp
```

> **Don't have `git`?**
> macOS: run `xcode-select --install` and try again.
> Windows: [git-scm.com](https://git-scm.com/download/win) → install → reopen PowerShell.

### 3. Install the program

```bash
uv venv
uv pip install -e .
```

This creates a small, isolated Python environment inside the project folder
(`.venv/`) and installs everything Claude will run. Takes about 30 seconds.

### 4. Find the install path (you'll paste this into Claude in the next step)

```bash
# macOS / Linux:
echo "$(pwd)/.venv/bin/python"
```

```powershell
# Windows PowerShell:
echo "$(pwd)\.venv\Scripts\python.exe"
```

Copy what it prints. It looks something like
`/Users/you/trip-search-mcp/.venv/bin/python`. **Keep this handy** — you'll
paste it into Claude Desktop's config in a moment.

### 5. Tell Claude Desktop about it

Open Claude Desktop's config file. The fastest way:

**macOS:**

```bash
open -e "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

If that says "file does not exist", create it first:

```bash
mkdir -p "$HOME/Library/Application Support/Claude"
echo '{}' > "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
open -e "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

**Windows:** open File Explorer and paste this in the address bar:
`%APPDATA%\Claude\claude_desktop_config.json` — open with Notepad.

Replace the file's contents with this (with **your** path from step 4
substituted in):

```json
{
  "mcpServers": {
    "trip-search": {
      "command": "/PASTE/YOUR/PATH/FROM/STEP-4/HERE",
      "args": ["-m", "trip_search_mcp.server"]
    }
  }
}
```

If you already have other MCP servers configured, just add the
`"trip-search"` block alongside them (don't forget the comma after the
previous entry).

Save the file.

### 6. Restart Claude Desktop

This is the most-forgotten step. **Fully quit Claude Desktop with ⌘Q
(Cmd+Q) on macOS or Alt+F4 on Windows, then reopen it.** Closing the
window isn't enough — the program keeps running in the background.

### 7. Try it

Open a new chat in Claude Desktop. Click the **hammer / tools icon** at
the bottom of the message box. You should see `trip-search` listed with
**six tools without a SerpAPI key, seven with one**:

- `search_flights` — specific-date flight search
- `search_cheapest_dates` — flexible-date price grid
- `search_stays` — hotels + vacation rentals (needs SERPAPI_KEY)
- `search_stays` with `category="airbnb"` — direct Airbnb (no key needed!)
- `get_stay_details` — drill into one property (needs SERPAPI_KEY)
- `watch_flight_price` — register a price watch
- `list_active_watches` — see active watches + alerts
- `cancel_watch` — stop watching

Now ask:

> *"Find me round-trip flights from JFK to LHR, leaving July 12 and
> returning July 22, one adult, economy. Summarize the cheapest options."*

Claude will pause for ~5–10 seconds (it's calling Google Flights live),
then come back with a summary and a "Book on Google Flights" link.

**If it works, you're done.** Skip to "What can I ask?" below.

---

## Optional: turn on hotel search

The flight tools (`search_flights`, `search_cheapest_dates`) work
without any API keys. **Hotel search needs a free SerpAPI key** (100
searches/month — plenty for personal trip planning).

1. Sign up at [serpapi.com](https://serpapi.com) (Google login is fine).
2. Copy your key from <https://serpapi.com/manage-api-key>.
3. Open Claude Desktop's config file again (step 5 above) and add an
   `env` block:

   ```json
   {
     "mcpServers": {
       "trip-search": {
         "command": "/PASTE/YOUR/PATH/FROM/STEP-4/HERE",
         "args": ["-m", "trip_search_mcp.server"],
         "env": {
           "SERPAPI_KEY": "paste-your-serpapi-key-here"
         }
       }
     }
   }
   ```

4. **⌘Q Claude Desktop and reopen.** A third tool, `search_stays`,
   appears in the tools menu. It covers **both hotels AND vacation
   rentals** in one call (Google's aggregation of short-term rentals
   from OTAs like Booking.com, Hotels.com, Vrbo.com — note: **Airbnb is
   not in Google's aggregation**).

Test it:

> *"Find me a place to stay in Lisbon, June 20 to 23, 2 adults, at
> least 4 stars or 4+ review score, under €200/night."*

---

## If something doesn't work

The four problems you're most likely to hit, with one-line fixes:

| Symptom | Fix |
|---|---|
| The `trip-search` server doesn't appear in the tools menu | You forgot ⌘Q. Fully quit Claude Desktop (not just close the window) and reopen. |
| `search_stays` says "SERPAPI_KEY is not set" | The `env` block is missing from your config, or you ⌘Q'd before saving. Re-check step 3 of the optional section, then ⌘Q + reopen. |
| Claude says "I called the tool and got a timeout" | Run `pkill -f trip_search_mcp` in a terminal, then ⌘Q + reopen Claude Desktop. The program got stuck on stale code. |
| "ModuleNotFoundError: No module named 'trip_search_mcp'" | The `command` path in your config points to the wrong Python. Re-run step 4 and paste that exact path into the config. |

If you're still stuck, [docs/SETUP.md](./docs/SETUP.md) has a verbose
troubleshooting walkthrough.

---

## What can I ask?

Once it's working, here are real questions Claude can answer. Pick a style
and adapt to your trip.

### Flights — specific dates

> *"Find me round-trip flights from JFK to LHR, leaving July 12 and
> returning July 22."*

> *"Show me only direct (non-stop) flights from Helsinki to JFK on May 18."*

> *"HEL to IAD May 18 returning May 29. Morning outbound (8am–noon),
> evening return (8pm–11pm)."*

> *"Find flights to Bangkok in November, prefer Star Alliance — United,
> Lufthansa, Singapore, or Thai."*

> *"Business-class round-trip from Boston to Singapore, January 15 to
> January 30."*

> *"4 of us flying SFO to MCO in December — 2 adults and 2 kids under 12."*

### Flights — flexible dates

> *"I want to fly from London to Tokyo for about 10 days sometime in
> March. Which dates are cheapest?"*

> *"What's the cheapest day to fly one-way from Helsinki to Barcelona
> between May 15 and June 5?"*

> *"Compare HEL → IAD round-trip prices for May 18 ± 3 days, all 11-night
> trips."*

> *"I want a 3-month trip to Australia leaving sometime between June and
> September. When's it cheapest?"*

### Stays — hotels + vacation rentals (requires SERPAPI_KEY)

> *"Find me a place to stay in Tampere from June 15 to June 18, 2 adults."* — returns mixed hotels + vacation rentals

> *"Just hotels in Lisbon next weekend, 2 adults, under €150/night, at
> least 4 stars."* — `category="hotels"`

> *"Find me a 2-bedroom rental in Lisbon for a week starting July 5,
> sleeps 4."* — `category="vacation_rentals"`, `min_bedrooms=2`

> *"Stays in central London for 3 nights starting October 12, must have
> pool and gym."*

> *"Best-reviewed hotels in Kyoto for the first week of November, 1
> traveler."*

> *"Family hotel in Orlando from July 5–12: 2 adults, 2 kids, one room."*

### Airbnb specifically (uses `search_stays` with `category="airbnb"`)

> *"Find me an Airbnb in Lisbon for 4 nights from October 12, 2 bedrooms minimum."*

> *"What's on Airbnb near Tampere June 15–18, sleeps 6 or more, under €200/night?"*

### Deal hunting (uses `watch_flight_price` + `list_active_watches`)

> *"Watch flights from HEL to IAD on May 18 and tell me if it drops below €600."* → registers a watch

> *"Any deals on my watches yet?"* → re-runs all active watches and reports alerts

> *"Show me everything I've cooked up, including the ones I cancelled."* → `list_active_watches(include_cancelled=true)`

> *"Cancel the Lisbon watch."* → finds it via `list_active_watches`, then `cancel_watch(watch_id=...)`

### Drilling into one stay (uses `get_stay_details`)

> *"Tell me more about [hotel name] — what's nearby, and which sites are offering it?"* → Claude pulls the `property_token` from the prior `search_stays` result and calls `get_stay_details`

### City codes (uses `search_flights` with city → airport expansion)

> *"Round-trip to Washington DC for May 18, return May 29."* → `origin="HEL"`, `destination="WAS"` (auto-expands to IAD, DCA, BWI in parallel)

> *"Cheapest week to fly from NYC to LON in March, 10-night trip."* → date-flex grid across 3×3 = 9 airport pairs

### Trip planning (combining flights + stays)

> *"I want to spend two weeks in Lisbon. When's the cheapest time to go
> in the next 3 months, and what does the cheapest itinerary look like?"*

> *"I want to spend 3 nights in Tampere in June. Find me a flight and a
> place to stay — flexible on hotel or rental, but keep it cheap and at
> least 4 stars or strong reviews."*

---

## What this can't do (yet)

- **It doesn't book for you.** Claude returns a "Book on Google Flights"
  or "Book on Google Hotels" link per result — you click it to finish on
  the airline / booking partner's site.
- **Airbnb-only listings.** Google's vacation-rental aggregation
  surfaces OTAs (Booking.com, Hotels.com, Vrbo.com, Bluepillow.com) but
  not Airbnb directly. If a property is Airbnb-only, it won't appear.
- **No multi-city / open-jaw itineraries in one shot.** Workaround: ask
  Claude to search one leg at a time.
- **No "Washington DC" → all-airports expansion.** Use the specific
  airport code (`IAD`, `DCA`, or `BWI`). Claude usually picks the best one
  from context.

---

## After you `git pull` for an update

The MCP server gets launched by Claude Desktop **once at startup** and
keeps running. Pulling new code doesn't reload the running server. To
pick up updates:

1. `git pull` and `uv pip install -e .` (re-install if dependencies
   changed).
2. **⌘Q Claude Desktop and reopen.**

---

## For developers

If you want to read the code, extend the tools, or run the test suite:

- [docs/SETUP.md](./docs/SETUP.md) — verbose install walkthrough +
  troubleshooting.
- [AGENTS.md](./AGENTS.md) — notes for AI coding agents working on this
  repo (deployment topology, hallucination traps to avoid).
- [BACKLOG.md](./BACKLOG.md) — known open ideas, sized for a fresh
  session to pick up cold.

Run the test suite:

```bash
.venv/bin/pytest -q          # 258 tests, all fixture-driven, no live API calls
```

Capture fresh real-data fixtures (uses live APIs):

```bash
.venv/bin/python scripts/verify_fli.py            # 1 SearchFlights + 1 SearchDates call
.venv/bin/python scripts/verify_serpapi_hotels.py # 1 SerpAPI call
```

---

## Tool reference (for the curious)

Claude reads richer descriptions than these tables; this is the short
version for humans.

### `search_flights`

| Parameter | Default | Notes |
|---|---|---|
| `origin` | required | 3-letter IATA **airport** code (`HEL`, `JFK`) OR **city** code (`WAS`, `NYC`, `LON`, …). City codes expand to the metro's busiest 3 airports and fan out in parallel. |
| `destination` | required | Same format as origin. |
| `departure_date` | required | `YYYY-MM-DD`. Today (UTC) or later. |
| `return_date` | optional | Omit for one-way. |
| `adults` | 1 | 1–9. |
| `children` | 0 | 0–9, age 2–11. |
| `infants` | 0 | 0–9, lap infants (must be ≤ adults). |
| `cabin_class` | `ECONOMY` | `ECONOMY` / `PREMIUM_ECONOMY` / `BUSINESS` / `FIRST`. |
| `max_stops` | `ANY` | `ANY` / `NON_STOP` / `ONE_STOP_OR_FEWER` / `TWO_OR_FEWER_STOPS`. |
| `departure_window` | none | `"HH-HH"` 24-hour local time, e.g. `"8-20"`. Outbound leg only. Inclusive of start hour, exclusive of end (`"8-20"` matches 08:00–19:59). |
| `inbound_window` | none | Same format, applied to the return leg. |
| `airlines` | none | List of IATA airline codes, e.g. `["AY", "FI"]`. Inclusion-only — matches offers where at least one segment is operated by one of these carriers. |
| `max_results` | 20 | 1–50. Applies to the merged result across all expanded airport pairs. |

**City codes supported:** `NYC`, `WAS`, `CHI`, `DFW`, `HOU`, `MIA`,
`QLA` (LA), `SFO`, `YTO`, `YMQ`, `BOS`, `LON`, `PAR`, `BER`, `MIL`,
`ROM`, `STO`, `MOW`, `IST`, `TYO`, `OSA`, `SEL`, `BJS`, `SHA`, `TPE`,
`JNB`, `BUE`, `RIO`, `SAO`, `DUB`, `MEL`, `SYD`, `HEL`. Each expands
to its constituent airport list (max 3). See `cities.py` for the
full map.

### `search_cheapest_dates`

| Parameter | Default | Notes |
|---|---|---|
| `origin` | required | 3-letter IATA airport code OR city code (same expansion as `search_flights`). |
| `destination` | required | Same format as origin. |
| `start_date` | required | Earliest acceptable departure date. |
| `end_date` | required | Latest acceptable departure date. |
| `trip_duration` | conditional | Days. Required when `is_round_trip=true`, 1–365. |
| `is_round_trip` | `false` | When true, output pairs each departure date with a return date. |
| `passengers` | 1 | 1–9. |
| `cabin_class` | `ECONOMY` | Same enum as `search_flights`. |
| `max_stops` | `ANY` | Same enum as `search_flights`. |
| `departure_window` | none | Same format and semantics. |
| `airlines` | none | Same semantics. |

### `search_stays` *(requires `SERPAPI_KEY`)*

Covers both hotels AND vacation rentals. Default `category="all"` makes
**2 SerpAPI calls in parallel** and merges; `category="hotels"` or
`category="vacation_rentals"` makes 1 call.

| Parameter | Default | Notes |
|---|---|---|
| `location` | required | City, neighborhood, or area. Free-text. |
| `check_in_date` | required | `YYYY-MM-DD`. Today (UTC) or later. |
| `check_out_date` | required | Must be strictly after `check_in_date`. |
| `adults` | 2 | 1–10. |
| `children` | 0 | 0–10. |
| `rooms` | 1 | 1–10. |
| `category` | `"all"` | `"all"` / `"hotels"` / `"vacation_rentals"` / `"airbnb"`. `"all"` costs 2 SerpAPI calls per query. `"airbnb"` bypasses SerpAPI and hits Airbnb directly (no API key, but slower + more fragile). |
| `min_rating` | none | Star rating 1–5. **Hotels-only filter.** Vacation rentals pass through unfiltered (they have no hotel class). |
| `min_bedrooms` | none | **Vacation-rental-only filter.** Hotels pass through. |
| `min_bathrooms` | none | Same scoping as `min_bedrooms`. |
| `min_review_score` | none | Google's native 0–5 review score (NOT 0–10). |
| `max_price_per_night` | none | Per-night ceiling, in the response currency. |
| `required_amenities` | none | List of free-text amenity names. Case- and punctuation-insensitive substring match ("wifi" matches "Free Wi-Fi"). |
| `sort_by` | `BEST` | `BEST` / `PRICE_LOW` / `PRICE_HIGH` / `RATING` / `REVIEW_SCORE`. |
| `max_results` | 10 | 1–25. Applies to the *merged* result set when `category="all"`. |
| `currency` | `EUR` | ISO 4217 code (`"USD"`, `"JPY"`, `"GBP"`, …). Match it to the units the user spoke in for `max_price_per_night`. |

Each `StayOffer` carries: `offer_id`, `name`, `category` (`"hotel"` or
`"vacation_rental"`), `nights`, `price_total`, `price_per_night`,
`currency`, `star_rating` (hotels only), `review_score` (0–5 scale),
`review_count`, GPS coordinates, `amenities`, `images`, `description`
(hotels only), `bedrooms` / `bathrooms` / `sleeps` (vacation rentals
only, parsed from SerpAPI's `essential_info`), `sources` (per-OTA price
comparison; empty list for hotels in current data), `hotel_type`, and
`booking_url` (deep link to the property's Google Hotels entity page).

**`sources`**: per-offer list of `{name, price_per_night,
before_taxes_fees}` entries showing the same property listed across
booking partners. For vacation rentals this surfaces OTAs like
Booking.com, Hotels.com, Vrbo.com — **NOT Airbnb** (Google's
aggregation doesn't include it).

**`address` is always null** — SerpAPI's google_hotels list endpoint
doesn't carry per-property addresses.

The success envelope is `{"results": [...], "warnings": [...]}`.
`warnings` is populated only on the partial-failure path (when
`category="all"` and one of the two SerpAPI calls fails but the other
succeeds).

### `get_stay_details` *(requires `SERPAPI_KEY`)*

Drill into one stay (hotel or vacation rental) using a `property_token`
from a prior `search_stays` result. Returns long-form description, full
booking-partner list with direct booking-flow URLs, and ~14
`nearby_places`.

| Parameter | Default | Notes |
|---|---|---|
| `property_token` | required | Copy from any offer in a `search_stays` response. |
| `check_in_date` / `check_out_date` | required | Same constraints as `search_stays`. |
| `adults` | 2 | 1–10. |
| `currency` | `EUR` | ISO 4217. |

**`address` is NOT in the response.** Use the lat/long plus the
`nearby_places` list for location signal.

### `watch_flight_price`, `list_active_watches`, `cancel_watch`

Deal-hunting layer. Register a price threshold for a route; ask later
("any deals?") to refresh the watch and surface alerts when the price
drops to or below the threshold.

Watches live in SQLite at `~/.trip-search-mcp/watches.db` and persist
across Claude Desktop restarts. Refresh is **lazy**: a call to
`list_active_watches` re-runs any watch whose latest check is older
than `refresh_after_hours` (default 6h). No background daemon.

| Tool | Inputs | Returns |
|---|---|---|
| `watch_flight_price` | route + departure_date (+ optional return_date) + `threshold_price` + `currency` (+ optional `note`) | `watch_id` (12 hex chars) + confirmation message |
| `list_active_watches` | `refresh_after_hours` (default `6.0`), `include_cancelled` (default `false`) | list of watches with `last_price`, `gap` (last_price - threshold), `status` (`active` / `alerted`), `last_checked_at` |
| `cancel_watch` | `watch_id` | `{watch_id, status: "cancelled"}` |

A watch's `status` flips from `active` → `alerted` the first time its
refresh observes a price ≤ threshold. `alerted_at` records when it
fired. Cancelled watches stay in the DB (so the user can recall what
they cancelled) but are filtered out of the default
`list_active_watches` response.

### Errors

Every tool returns either a success envelope on success, or an error
envelope on failure:

```json
{"error": {"code": "...", "message": "...", "retryable": true}}
```

Codes: `invalid_input`, `no_results`, `rate_limited`, `upstream_error`,
`auth_failed` (the last only fires from `search_stays` /
`get_stay_details` when `SERPAPI_KEY` is missing or rejected).

---

## License

MIT.
