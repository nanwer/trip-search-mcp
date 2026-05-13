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

## 5. Track flight prices — "tell me if it gets cheaper"

*(uses `watch_flight_price`)*

Register a price threshold on a specific route + date. The watch persists forever (it's saved to a database on your computer) until you cancel it.

**You can ask Claude:**

- *"Watch flights from HEL to IAD on May 18 and tell me if the price drops below €600."*
- *"Set up a watch on JFK → NRT for any date in November, alert me if it goes below $700."*
- *"Track Vienna to Helsinki for early March, threshold €200, note: 'for parents' anniversary'."*

Claude tells you a `watch_id` you can use to cancel later.

### 5a. Check on your watches

*(uses `list_active_watches`)*

Whenever you ask "any deals?", Claude re-runs all your active watches against the latest Google Flights data and reports which ones (if any) have hit their threshold.

- *"Any deals on my watches yet?"*
- *"What's the price doing on the Lisbon trip?"*
- *"Did anything trigger overnight?"*
- *"Show me everything, including the watches I cancelled."*

You'll see each watch's current price, how far it is from your threshold ("€53 below target — alerted!" or "€120 above target — still watching"), and when it was last checked.

> **How fresh is "fresh"?** By default, watches refresh every 6 hours. Within that window, the answer comes from cache (no extra API calls). If you want a forced refresh, ask "force a refresh on all my watches".

### 5b. Cancel a watch

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
| "Watch this route for me" | `watch_flight_price` |
| "Any deals on my watches?" | `list_active_watches` |
| "Stop watching the [...] one" | `cancel_watch` |

---

## Where the data comes from

- **Flights** → [fli](https://github.com/punitarani/fli), a community library that talks to Google Flights' public endpoints directly. No API key required.
- **Hotels & vacation rentals** → [SerpAPI's google_hotels endpoint](https://serpapi.com/google-hotels-api). Free tier 100 searches/month.
- **Airbnb** → [pyairbnb](https://github.com/johnbalvin/pyairbnb), a community library that talks to Airbnb's GraphQL search endpoint. No API key required.
- **Geocoding** (for the Airbnb category) → [OpenStreetMap Nominatim](https://nominatim.openstreetmap.org/). Free, no key. Used only for `category="airbnb"`.

All providers are queried live — no stale cached results from yesterday's prices.
