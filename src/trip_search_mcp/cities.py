"""City-code to airport-code expansion for multi-airport metro areas.

Google Flights accepts city codes like `WAS` (Washington DC),
`NYC` (New York), `LON` (London) as origin/destination. fli's
`Airport` enum is airport-specific — `Airport["WAS"]` raises KeyError.

This module fills the gap. The tools call `expand_to_airports(code)`
just before invoking the fli client. If the code is a known city,
it returns the list of constituent airport IATAs and the tool fans
out to N parallel fli calls + merges. If the code is unknown to this
map, it's passed through unchanged — fli either accepts it (airport
code) or raises InvalidAirport (which we translate to a clean
`invalid_input` envelope).

A pair like (`NYC`, `LON`) would expand to 3 × 4 = 12 search calls
which is unreasonable. We cap each side at 3 airports — the busiest
3 by passenger traffic. Users can always pass a specific airport
code (`LHR`) to bypass the cap.

The list is hand-curated and biased toward routes Nophil and Eli
actually fly. Adding new entries is one line; no rebuild required.
"""
from __future__ import annotations


# Curated city → airports map. Each list is ORDERED by busiest first;
# tools truncate to MAX_AIRPORTS_PER_SIDE entries when fanning out.
#
# Sources for the picks: Google Flights' own city-code groupings, IATA
# multi-airport-city catalog, and Wikipedia airport traffic rankings.
# Lists are intentionally short. Adding a city later is one line.
CITY_TO_AIRPORTS: dict[str, list[str]] = {
    # North America
    "NYC": ["JFK", "EWR", "LGA"],
    "WAS": ["IAD", "DCA", "BWI"],
    "CHI": ["ORD", "MDW"],
    "DFW": ["DFW", "DAL"],
    "HOU": ["IAH", "HOU"],
    "MIA": ["MIA", "FLL"],
    "QLA": ["LAX", "BUR", "LGB"],          # IATA city code for Los Angeles
    "SFO": ["SFO", "OAK", "SJC"],          # also serves "Bay Area"
    "YTO": ["YYZ", "YTZ"],                 # Toronto
    "YMQ": ["YUL", "YHU"],                 # Montreal
    "BOS": ["BOS"],                        # single-airport metros included for round-tripping

    # Europe
    "LON": ["LHR", "LGW", "STN", "LCY"],   # London — 4 candidates; will truncate to top-3
    "PAR": ["CDG", "ORY", "BVA"],
    "BER": ["BER"],
    "MIL": ["MXP", "LIN", "BGY"],
    "ROM": ["FCO", "CIA"],
    "STO": ["ARN", "BMA", "NYO"],          # Stockholm
    "MOW": ["SVO", "DME", "VKO"],          # Moscow
    "IST": ["IST", "SAW"],

    # Asia / Pacific
    "TYO": ["HND", "NRT"],                 # Tokyo
    "OSA": ["KIX", "ITM", "UKB"],          # Osaka
    "SEL": ["ICN", "GMP"],                 # Seoul
    "BJS": ["PEK", "PKX"],                 # Beijing
    "SHA": ["PVG", "SHA"],                 # Shanghai
    "TPE": ["TPE", "TSA"],                 # Taipei

    # Middle East / Australasia
    "JNB": ["JNB"],
    "BUE": ["EZE", "AEP"],                 # Buenos Aires
    "RIO": ["GIG", "SDU"],                 # Rio de Janeiro
    "SAO": ["GRU", "CGH", "VCP"],          # São Paulo
    "DUB": ["DXB", "DWC"],                 # Dubai
    "MEL": ["MEL"],
    "SYD": ["SYD"],

    # Helsinki (Nophil's home base — single airport but listed for
    # symmetry with how users may type it)
    "HEL": ["HEL"],
}

# Cap each side at 3 airports to bound the combinatorial blowup.
# A worst case (NYC → LON, 3 × 4) would otherwise be 12 fli calls.
# At 3 × 3 the worst case is 9 — still expensive but bounded.
MAX_AIRPORTS_PER_SIDE = 3


def expand_to_airports(code: str) -> list[str]:
    """Resolve a 3-letter IATA code to its constituent airport list.

    - Known city code → list of airports (truncated to MAX_AIRPORTS_PER_SIDE).
    - Anything else (airport code, or city code not in our map) →
      passed through as a single-element list. fli decides at call time
      whether it's valid; the tool surfaces fli's error if not.

    Pure function. No I/O. Safe to call freely.
    """
    if code in CITY_TO_AIRPORTS:
        return CITY_TO_AIRPORTS[code][:MAX_AIRPORTS_PER_SIDE]
    return [code]


def is_known_city(code: str) -> bool:
    """True if the code is a city in our map (not an airport)."""
    return code in CITY_TO_AIRPORTS
