# Features

Everything `trip-search-mcp` can do, in plain English, with example
prompts you can paste straight into Claude.

Tools are organized by what they DO, not by the technical "MCP tool"
name — those names appear in italics so you can find them in the
README's tool reference if you want the parameter detail.

> **Setup reminder:** flights and Airbnb work with no API key. Hotels
> and the vacation-rental side need a free SerpAPI key (100 searches/
> month). See [README.md → Optional: turn on hotel search](./README.md#optional-turn-on-hotel-search).

---

## 1. Search flights for specific dates

*(uses `search_flights`)*

Find live Google Flights options when the traveler has dates in mind.

**You can ask Claude:**

- *"Find me round-trip flights from JFK to LHR, leaving July 12 and returning July 22."*
- *"One-way HEL to NRT on May 18, business class, non-stop only."*
- *"4 of us flying SFO to MCO in December — 2 adults and 2 kids under 12."*
- *"Show me flights from Boston to Singapore in January 15-30, business class."*

### 1a. Time-of-day windows

You can constrain departure hours on the outbound, the return, or both.

- *"HEL to IAD May 18, returning May 29. Morning outbound (8am–noon), evening return (8pm–11pm)."*
- *"I need to leave San Francisco between 6am and noon on Friday."*
- *"Any outbound to Tokyo on March 5, but I need to land back in NYC before 6pm on the 19th — so the return must leave Tokyo in the morning."*

### 1b. Airline preference

For loyalty-program travelers or alliance preferences.

- *"Find flights to Bangkok in November, prefer Star Alliance — United, Lufthansa, Singapore, or Thai."*
- *"Helsinki to JFK May 18, Finnair or Icelandair only."*

### 1c. City codes (no need to specify an airport)

Type a city code instead of a specific airport — Claude searches all the major airports for that city in parallel and merges the results.

- *"Find me flights to Washington DC for May 18."* → searches IAD, DCA, AND BWI
- *"NYC to LON in March."* → searches all 9 combinations of (JFK, EWR, LGA) × (LHR, LGW, STN)
- *"From Paris to Tokyo next month."* → PAR (CDG, ORY, BVA) → TYO (HND, NRT)

Supported city codes: `NYC`, `WAS`, `CHI`, `DFW`, `HOU`, `MIA`, `QLA` (LA), `SFO`, `YTO`, `YMQ`, `BOS`, `LON`, `PAR`, `BER`, `MIL`, `ROM`, `STO`, `MOW`, `IST`, `TYO`, `OSA`, `SEL`, `BJS`, `SHA`, `TPE`, `JNB`, `BUE`, `RIO`, `SAO`, `DUB`, `MEL`, `SYD`, `HEL`.

---

## 2. Find the cheapest dates to fly

*(uses `search_cheapest_dates`)*

When you're flexible on dates and want to know which days are cheapest within a range.

**You can ask Claude:**

- *"I want to fly from London to Tokyo for about 10 days sometime in March. Which dates are cheapest?"*
- *"What's the cheapest day to fly one-way from Helsinki to Barcelona between May 15 and June 5?"*
- *"Compare HEL → IAD round-trip prices for May 18 ± 3 days, all 11-night trips."*
- *"I want a 3-month trip to Australia leaving sometime between June and September. When's it cheapest?"*
- *"Cheapest dates from SFO to NRT in October, business class only, non-stop."*

City codes work here too: *"NYC to LON next quarter, 10-night trip"* runs the date grid across all 9 airport pairs and gives you the cheapest day per pair.

---

## 3. Search hotels and vacation rentals together

*(uses `search_stays` with `category="all"`, the default)*

Find a place to stay — hotels OR short-term rentals, mixed and ranked together.

**You can ask Claude:**

- *"Find me a place to stay in Tampere from June 15 to June 18, 2 adults."*
- *"Stays in Lisbon next weekend, 2 adults, under €150/night, at least 4 stars or strong reviews."*
- *"Family-friendly stays in Orlando from July 5–12: 2 adults, 2 kids, one room."*
- *"Find somewhere in central London for 3 nights starting October 12, must have pool and gym."*

### 3a. Hotels only

When the traveler specifically wants a hotel (concierge, daily housekeeping, single check-in).

- *"Find me a nice 4-star hotel in Kyoto for the first week of November."*
- *"Hotels in Singapore in January 18–22, budget $300/night, must have pool."*

### 3b. Vacation rentals only (via Google's OTA aggregation)

For multi-bedroom apartments and houses across Booking.com, Hotels.com, Vrbo.com, Bluepillow.com.

- *"Find a vacation rental in Tampere for a week starting July 5, sleeps 6, 2 bedrooms minimum."*
- *"Apartment rental in Berlin, June 1–7, kitchen and washer required."*

### 3c. Airbnb specifically

When the traveler asks for Airbnb by name. Bypasses SerpAPI and queries Airbnb directly — no API key needed, but slower and more fragile.

- *"Find me an Airbnb in Lisbon for 4 nights from October 12, 2 bedrooms minimum."*
- *"What's on Airbnb near Kyoto in November, sleeps 4 or more, under ¥30000/night?"*

> **Honest limitation:** Google's main aggregation (categories `"all"` / `"vacation_rentals"`) does NOT include Airbnb listings. If the user specifically wants Airbnb, use `category="airbnb"`. Caveat: pyairbnb is a community scraper; Airbnb sometimes pushes back during high traffic.

### 3d. Cross-OTA price comparison

For vacation rentals that show up across multiple booking sites, each card surfaces "from €X on Booking.com, also on Hotels.com" so the traveler can comparison-shop.

- *"Find me a stay in Tampere for next weekend, and tell me which sites have the best price for each option."*

---

## 4. Drill into one specific property

*(uses `get_stay_details`)*

After Claude shows you a list of stays, ask for more detail on the one you like.

**You can ask Claude:**

- *"Tell me more about [hotel name from the list]."*
- *"Which sites can I book the Lillan through, and what's the price difference?"*
- *"What's near that apartment in Pyynikki? Walking distance to anything?"*
- *"Is the Solo Sokos Torni refundable? What's their cancellation policy?"*

What you'll get back:
- **Long-form description** of the property.
- **Direct "Book on X" links** for every partner offering this property (Booking.com, Hotels.com, Expedia, Vrbo, etc.) — each link lands you on the partner's actual booking flow, not a search page.
- **~14 nearby places** (airports, transit, restaurants, landmarks) with their distances.
- The full amenities list (not just the top 3).
- Check-in / check-out times, free-cancellation flags per partner.

> One thing it WON'T return: a postal address. SerpAPI doesn't expose addresses for hotels or rentals. Use the GPS coordinates and nearby-places list for location signal.

---

## 5. Search activities — things to do at a destination

*(uses `search_activities`, requires SERPAPI_KEY)*

Find sights and bookable experiences (cooking classes, boat tours, museums, walking tours, …) at a destination. Powered by Tripadvisor's Things-to-Do listing via SerpAPI.

**You can ask Claude:**

- *"What should I do in Lisbon?"* → Claude asks a clarifying question if your interests aren't known, then searches.
- *"Find cooking classes in Lisbon."* → `query="cooking class"`.
- *"Boat tours in Lisbon, only bookable ones."* → `place_type_filter="experiences"`.
- *"Top-rated museums in Paris."* → `query="museums"`, `min_rating=4.5`.
- *"Things to do in Notting Hill, London."*

Each card shows: name + sight/experience badge, rating + review count, location, a 1-line highlighted review, and a "Find on Tripadvisor" button.

### Sights vs experiences

- **Sights** (`activity_type="sight"`) — free or just-walk-in attractions: museums, viewpoints, neighborhoods.
- **Experiences** (`activity_type="experience"`) — bookable tours and classes with a Tripadvisor / Viator listing.

`place_type_filter` accepts `"sights"`, `"experiences"`, or `"both"` (default).

> **Heads-up on what's not in the response:** no `price`, no exact GPS coordinates, no direct Viator booking URL. Tripadvisor's search endpoint doesn't surface those — you have to click the listing link to see them. A `get_activity_details` drill-down was planned but Phase 0 verification revealed SerpAPI's Tripadvisor place_details endpoint only returns image URLs (no price, duration, or Viator URL), so that tool wasn't built. See BACKLOG.md.

---

## 6. Search events — what's happening while I'm there

*(uses `search_events`, requires SERPAPI_KEY)*

Find time-bound events (concerts, festivals, sports, comedy, theatre) at a location. Distinct from `search_activities`: events are date-specific things to attend; activities are ongoing things to do.

**You can ask Claude:**

- *"What's happening in Lisbon this weekend?"* → `date_filter="weekend"`
- *"Any concerts in Paris next month?"* → `query="concerts"`, `date_filter="next_month"`
- *"Is BTS playing anywhere in July 2026?"* → `query="BTS tour July 2026"`
- *"Festivals in Berlin in summer"*
- *"Sports games in Helsinki this week"* → `query="sports"`, `date_filter="week"`

Each event card surfaces title, full date string (e.g. "Fri, Jul 17, 8 – 11 PM GMT+2"), venue + rating, address, and one "Tickets on X" button per source. The same event is often available across multiple ticket vendors (Viagogo, StubHub, Eventbrite, Spotify Concerts, Feverup, …) — Claude shows all of them so you can comparison-shop.

> **`date_filter` accepts named ranges only:** `today`, `tomorrow`, `week`, `weekend`, `next_week`, `month`, `next_month`. For a specific calendar month, bake it into the query string (`query="concerts June 2026"`).

---

## 7. Convert currencies

*(uses `convert_currency`)*

Convert any amount between major currencies using the [European Central Bank's daily reference rates](https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml) — free, no API key, updates once a day.

**You can ask Claude:**

- *"How much is ¥30,000 in euros?"*
- *"What's $200 in pounds?"*
- *"Convert €450 (the hotel) plus $180 (the activity) to GBP for me."* → two calls + sum
- *"My total trip is €1240 flights + $890 hotel + £120 activities. What's that in CAD?"* → three calls + sum

Supports 29+ currencies: USD, EUR, JPY, GBP, CAD, AUD, CHF, SEK, NOK, DKK, INR, MXN, BRL, SGD, KRW, CNY, THB, HKD, NZD, plus CZK, HUF, IDR, ILS, ISK, MYR, PHP, PLN, RON, TRY, ZAR.

> Weekend / holiday queries return the previous business day's rates — Claude discloses the date in its reply. Same-currency conversions short-circuit instantly without hitting the ECB feed.

---

## 8. Check the weather

*(uses `get_weather_forecast`)*

Get a 7-day daily forecast for any city or specific coordinates. Powered by [Open-Meteo](https://open-meteo.com/) — free, global, no API key.

**You can ask Claude:**

- *"What's the weather in Tampere from June 15 to 18?"*
- *"Will it rain in Lisbon next week?"*
- *"Show me the forecast for Tokyo for the first week of November in Fahrenheit."*
- *"Compare weather in Lisbon vs Barcelona for the second week of June — which is sunnier?"* → Claude calls the tool twice and compares.
- *"Forecast for these coordinates: 38.96, -77.36"* → skips the geocoding step.

Each day comes back with: max/min temp, condition summary (clear / partly cloudy / rain / thunderstorm / …), precipitation probability, sunrise, sunset, and the IANA timezone for the location.

> Forecast horizon is capped at 7 days from today. For longer-range "what's the weather typically like in October" questions, the tool isn't the right answer — Claude can answer those from general knowledge.

---

## 9. Track flight prices — "tell me if it gets cheaper"

*(uses `watch_flight_price`)*

Register a price threshold on a specific route + date. The watch persists forever (it's saved to a database on your computer) until you cancel it.

**You can ask Claude:**

- *"Watch flights from HEL to IAD on May 18 and tell me if the price drops below €600."*
- *"Set up a watch on JFK → NRT for any date in November, alert me if it goes below $700."*
- *"Track Vienna to Helsinki for early March, threshold €200, note: 'for parents' anniversary'."*

Claude tells you a `watch_id` you can use to cancel later.

### 9a. Check on your watches

*(uses `list_active_watches`)*

Whenever you ask "any deals?", Claude re-runs all your active watches against the latest Google Flights data and reports which ones (if any) have hit their threshold.

- *"Any deals on my watches yet?"*
- *"What's the price doing on the Lisbon trip?"*
- *"Did anything trigger overnight?"*
- *"Show me everything, including the watches I cancelled."*

You'll see each watch's current price, how far it is from your threshold ("€53 below target — alerted!" or "€120 above target — still watching"), and when it was last checked.

> **How fresh is "fresh"?** By default, watches refresh every 6 hours. Within that window, the answer comes from cache (no extra API calls). If you want a forced refresh, ask "force a refresh on all my watches".

### 9b. Cancel a watch

*(uses `cancel_watch`)*

- *"Stop watching the Lisbon route — I already booked it."*
- *"Cancel the watch I set up yesterday for Helsinki to JFK."*
- *"Drop all my watches except the one to Tokyo."*

---

## Combined workflows — where this MCP earns its keep

Claude naturally chains the tools when you give it a trip-planning problem instead of a narrow search request.

### Workflow A: "Plan me a trip"

**You ask:** *"I want to spend two weeks in Lisbon. When's the cheapest time to go in the next 3 months, and what's the cheapest flight + hotel combination?"*

**Claude does:**
1. Calls `search_cheapest_dates` for HEL → LIS over 90 days, 14-night trip → identifies the cheapest week.
2. Calls `search_flights` for those specific dates → returns the actual airlines, times, booking link.
3. Calls `search_stays` for Lisbon on those check-in/out dates.
4. Quotes one total trip cost (flight + hotel × nights) in EUR and calls out the cheapest combined plan.

### Workflow B: "Find me a deal"

**You ask:** *"I want to go somewhere warm in February for a week. Anywhere cheap from Helsinki."*

**Claude does:**
1. Asks you for a list of candidate destinations (Lisbon? Barcelona? Tenerife? Malta?).
2. Calls `search_cheapest_dates` for each candidate.
3. Picks the cheapest 2–3, then `search_flights` for those specific dates.
4. Offers to register watches on the cheapest-but-not-yet-cheap-enough ones: *"Vienna in week 2 of Feb is €320; I'll watch it for under €280?"*

### Workflow C: "Combine flights + Airbnb"

**You ask:** *"I want to spend 4 nights in Kyoto in November with 6 friends. Find a flight and an Airbnb."*

**Claude does:**
1. Calls `search_flights` for HEL → KIX or NRT.
2. Calls `search_stays(category="airbnb", min_bedrooms=3, location="Kyoto")` to get listings that can sleep 7.
3. Quotes a combined per-person cost.

### Workflow D: "Same trip, two people from different cities"

**You ask:** *"My friend and I want to meet in Lisbon for the first week of June. I'm flying from Helsinki, they're flying from NYC."*

**Claude does:**
1. Calls `search_flights` for HEL → LIS.
2. Calls `search_flights` for NYC → LIS (expands NYC → JFK/EWR/LGA in parallel).
3. Tries to align arrival dates within a day of each other.
4. Calls `search_stays` for Lisbon for the overlap.
5. Returns a single 3-leg plan.

### Workflow E: "Watch a route, drill in when it triggers"

**You ask:** *"Watch HEL → IAD on May 18 for under €600. When it hits, find me a hotel for the same dates."*

**Claude does:**
1. Calls `watch_flight_price` to register the watch.
2. (Later, when you ask "any deals?") `list_active_watches` shows the watch alerted at €580.
3. *"Want me to find a hotel in DC for May 18–22 to go with it?"* → `search_stays(location="Washington DC", check_in_date="2026-05-18", ...)`.
4. Quotes the combined trip cost.

### Workflow J: "Plan a themed day"

**You ask:** *"I'm in Lisbon next week and I love food. Build me a half-day food itinerary — somewhere to eat lunch (maybe a cooking class), then a walking tour, then drinks."*

**Claude does:**
1. Calls `search_activities(location="Lisbon", query="cooking class")` for the cooking class.
2. Calls `search_activities(location="Lisbon", query="food walking tour")` for the walking tour.
3. Optionally `search_events(location="Lisbon", query="wine tasting", date_filter="next_week")` for an evening event.
4. Sequences the three into a half-day with travel-time gaps and presents as one itinerary.

### Workflow I: "Anchor a trip around a specific event"

**You ask:** *"Is BTS playing anywhere I could realistically fly to in July? If yes, find me flights and a hotel for those dates."*

**Claude does:**
1. Calls `search_events(query="BTS tour July 2026", location="Europe")` — finds the Paris date.
2. Calls `search_flights(origin="HEL", destination="PAR", departure_date=<event-1>, return_date=<event+2>)` — books arrival the day before, depart the day after.
3. Calls `search_stays(location="Paris", check_in_date=<event-1>, check_out_date=<event+2>, max_price_per_night=200)`.
4. Returns a combined plan: BTS on the 17th, flight via Finnair, hotel for 3 nights, total in EUR.

### Workflow H: "What's the total in one currency"

**You ask:** *"My trip cost is €450 flights, $180 stay, ¥6000 in activities. What's that all in pounds?"*

**Claude does:**
1. Calls `convert_currency(450, "EUR", "GBP")`.
2. Calls `convert_currency(180, "USD", "GBP")`.
3. Calls `convert_currency(6000, "JPY", "GBP")`.
4. Sums them and presents the total with the ECB rate date once.

### Workflow G: "Bias the plan by weather"

**You ask:** *"Two-week trip to Lisbon in October — which week looks better weather-wise?"*

**Claude does:**
1. Calls `get_weather_forecast` for Lisbon for week 1 and week 2 of October.
2. Compares rainy days, average highs, conditions.
3. Calls `search_cheapest_dates` constrained to the better week.
4. Recommends the sunnier-AND-cheaper week (or surfaces the trade-off if they conflict).

### Workflow F: "Drill in before booking"

**You ask:** *"Find me a hotel in Tokyo for first week of November, then tell me more about your top pick."*

**Claude does:**
1. Calls `search_stays(location="Tokyo", ...)`, picks the top result.
2. Calls `get_stay_details(property_token=<top result's token>)` to get the long-form description, direct booking partner links, and nearby places.
3. Renders a rich card with "Book on Expedia €240/night", "Book on Hotels.com €235/night", etc., plus what's within walking distance.

---

## What this MCP CAN'T do (and probably never will)

- **Actually book.** Every offer comes with a "Book on X" link — clicking through to the airline/OTA's site to confirm and pay is on you. (Booking would mean storing payment credentials, which is a different threat model entirely.)
- **Multi-city / open-jaw itineraries in one call.** A query like "HEL → NRT → SIN → HEL" needs to be split into 3 separate flight searches. Claude can do this manually but doesn't get the combined-pricing benefits.
- **Loyalty-program-specific price filtering** ("only show me business class redeemable with Star Alliance miles"). Out of scope; airline loyalty data isn't exposed by Google Flights.
- **Postal addresses for hotels.** SerpAPI's endpoints just don't return them. GPS coordinates + nearby places carry the location signal instead.

---

## Quick reference: which tool for which question?

| If the user says... | Claude calls... |
|---|---|
| "Find me a flight from X to Y on date Z" | `search_flights` |
| "When's the cheapest week to go from X to Y?" | `search_cheapest_dates` |
| "Find me a place to stay" (vague) | `search_stays` (default `category="all"`) |
| "Find me a hotel" | `search_stays(category="hotels")` |
| "Find me an Airbnb" | `search_stays(category="airbnb")` |
| "Find me a vacation rental" | `search_stays(category="vacation_rentals")` |
| "Tell me more about [hotel]" | `get_stay_details` |
| "Things to do in X" / "cooking classes" / "tours" | `search_activities` |
| "What's happening in X?" / "concerts in X" / "any events" | `search_events` |
| "Will it rain in X?" / "weather for [dates]" | `get_weather_forecast` |
| "How much is X in Y?" / "convert price to my currency" | `convert_currency` |
| "Watch this route for me" | `watch_flight_price` |
| "Any deals on my watches?" | `list_active_watches` |
| "Stop watching the [...] one" | `cancel_watch` |

---

## Card + button rendering is baked into the server

You shouldn't have to ask for it per-prompt. The server publishes **server-level instructions** (via MCP's `serverInfo.instructions` field) at handshake time. Claude Desktop reads these once when the server connects and keeps them in scope for the whole chat. They say:

1. Multi-result responses MUST render as an HTML/React artifact with one card per item.
2. Each booking partner gets a button side-by-side; never collapse to a single "best" link.
3. Single-result responses may use prose; `convert_currency` always uses prose.

This is the right place for behavioral rules that apply to every tool — it's loaded once, not per-message, and Claude treats it as system-level guidance.

If for a specific query you want extra emphasis (Claude has discretion), append:

> *"Render every multi-result tool output as an HTML artifact card with prominent buttons — don't summarize as prose."*

Edit `src/trip_search_mcp/server.py` (look for `_SERVER_INSTRUCTIONS`) if you want to tune the directive for your own taste.

---

## Where the data comes from

- **Flights** → [fli](https://github.com/punitarani/fli), a community library that talks to Google Flights' public endpoints directly. No API key required.
- **Hotels & vacation rentals** → [SerpAPI's google_hotels endpoint](https://serpapi.com/google-hotels-api). Free tier 100 searches/month.
- **Airbnb** → [pyairbnb](https://github.com/johnbalvin/pyairbnb), a community library that talks to Airbnb's GraphQL search endpoint. No API key required.
- **Weather** → [Open-Meteo](https://open-meteo.com/). Free, no API key, global coverage, 7-day daily forecast.
- **Currency rates** → [European Central Bank daily reference rates](https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml). Free, no API key, 29+ currencies, updates daily ~16:00 CET.
- **Events** → [SerpAPI's google_events engine](https://serpapi.com/google-events-api). Requires the same SERPAPI_KEY as stays. Surfaces ticket vendor links (Viagogo, StubHub, Eventbrite, Spotify Concerts, etc.).
- **Activities** → [SerpAPI's Tripadvisor engine](https://serpapi.com/tripadvisor-api) (`ssrc=A` for Things to Do). Requires the same SERPAPI_KEY.
- **Geocoding** (for `category="airbnb"` and `get_weather_forecast`) → [OpenStreetMap Nominatim](https://nominatim.openstreetmap.org/). Free, no key.

All providers are queried live — no stale cached results from yesterday's prices.
