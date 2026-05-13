#!/usr/bin/env python
"""Phase 0 (search_stays): verify SerpAPI's vacation_rentals=true mode.

Same Tampere query as the hotels Phase 0, but captured TWICE — once with
`vacation_rentals=true` and once with `vacation_rentals=false` (today's
default). Both fixtures land in tests/fixtures/:

  - serpapi_vacation_rentals_tampere.json
  - serpapi_hotels_tampere_compare.json

Then prints a structured summary covering all five design-relevant
questions for the search_stays merge work:

  1. Response shape parity — same field names as the hotel call?
  2. `prices` array contents — providers (Airbnb / VRBO / …), entry shape
  3. `property_token` stability — same property → same token across modes?
  4. Filter scoping — does SerpAPI silently ignore mismatched filters or
     return an error?
  5. Latency — how long does each call take?

Usage:
    set -a; source .env; set +a   # or export SERPAPI_KEY=...
    .venv/bin/python scripts/verify_vacation_rentals.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

FIXTURE_RENTALS = Path("tests/fixtures/serpapi_vacation_rentals_tampere.json")
FIXTURE_HOTELS = Path("tests/fixtures/serpapi_hotels_tampere_compare.json")
SERPAPI_URL = "https://serpapi.com/search"

BASE_PARAMS: dict[str, Any] = {
    "engine": "google_hotels",
    "q": "Tampere",
    "check_in_date": "2026-06-15",
    "check_out_date": "2026-06-18",
    "adults": "2",
    "currency": "EUR",
    "hl": "en",
}

# Filters we intentionally pass to BOTH calls so we can observe
# whether SerpAPI silently ignores mismatched filters or errors.
# - `hotel_class` is documented as hotel-only.
# - `bedrooms`    is documented as rental-only.
# Passing both into both calls answers Phase 0 question 4 directly.
HOTEL_ONLY_FILTERS = {"hotel_class": "4"}
RENTAL_ONLY_FILTERS = {"bedrooms": "2"}


def _call(api_key: str, extra_params: dict[str, Any]) -> tuple[dict, float]:
    params = {**BASE_PARAMS, **extra_params, "api_key": api_key}
    start = time.monotonic()
    with httpx.Client(timeout=30.0) as client:
        r = client.get(SERPAPI_URL, params=params)
    elapsed = time.monotonic() - start
    r.raise_for_status()
    return r.json(), elapsed


def _save(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Strip api_key from search_parameters if echoed back
    if isinstance(body.get("search_parameters"), dict):
        body["search_parameters"].pop("api_key", None)
    path.write_text(json.dumps(body, indent=2))


def _property_summary(p: dict) -> str:
    name = p.get("name", "<no name>")
    ptype = p.get("type", "<no type>")
    token = p.get("property_token", "<no token>")
    gps = p.get("gps_coordinates") or {}
    lat, lon = gps.get("latitude"), gps.get("longitude")
    return f"  - {name!r} type={ptype!r} token={token[:24]}... lat={lat} lon={lon}"


def _prices_summary(prices: list[dict]) -> str:
    if not prices:
        return "(no prices array or empty)"
    sources = []
    for entry in prices:
        src = entry.get("source", "<no source>")
        rpn = (entry.get("rate_per_night") or {}).get("extracted_lowest")
        sources.append(f"{src}=€{rpn}")
    return ", ".join(sources)


def main() -> int:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("ERROR: SERPAPI_KEY not set. Run `set -a; source .env; set +a` first.")
        return 2

    print("=" * 70)
    print("Phase 0 verification: vacation_rentals=true vs false (Tampere, same query)")
    print("=" * 70)

    # IMPORTANT (Q4 finding): SerpAPI does NOT silently ignore mismatched
    # filters — it returns HTTP 400 with `"error": "You're not allowed to
    # enable hotel_class for Vacation Rentals search."` Phase 1 orchestration
    # must scope filters at request-build time.

    # ----- Call 1: vacation_rentals=true (with rental-only filter only) -----
    print("\n[1/2] Calling SerpAPI with vacation_rentals=true (rental-scoped filters) ...")
    try:
        rentals_body, rentals_elapsed = _call(
            api_key,
            {"vacation_rentals": "true", **RENTAL_ONLY_FILTERS},
        )
    except httpx.HTTPStatusError as e:
        print(f"  FAILED: {e.response.status_code} {e.response.text[:200]}")
        return 1
    _save(FIXTURE_RENTALS, rentals_body)
    print(f"  → {FIXTURE_RENTALS}  ({rentals_elapsed:.2f}s wall-clock)")

    # ----- Call 2: vacation_rentals=false (with hotel-only filter only) -----
    print("\n[2/2] Calling SerpAPI with vacation_rentals=false (hotel-scoped filters) ...")
    try:
        hotels_body, hotels_elapsed = _call(
            api_key,
            {"vacation_rentals": "false", **HOTEL_ONLY_FILTERS},
        )
    except httpx.HTTPStatusError as e:
        print(f"  FAILED: {e.response.status_code} {e.response.text[:200]}")
        return 1
    _save(FIXTURE_HOTELS, hotels_body)
    print(f"  → {FIXTURE_HOTELS}  ({hotels_elapsed:.2f}s wall-clock)")

    # ----- Analysis ----------------------------------------------------------
    print("\n" + "=" * 70)
    print("ANALYSIS")
    print("=" * 70)

    rentals_props = rentals_body.get("properties") or []
    hotels_props = hotels_body.get("properties") or []
    print(f"\nResult counts:")
    print(f"  vacation_rentals=true  → {len(rentals_props)} properties")
    print(f"  vacation_rentals=false → {len(hotels_props)} properties")

    # Q1: Response shape parity
    print(f"\n--- Q1: Field-name parity (top 5 keys per side) ---")
    if rentals_props:
        rkeys = sorted(rentals_props[0].keys())
        print(f"  rentals[0] keys: {rkeys}")
    if hotels_props:
        hkeys = sorted(hotels_props[0].keys())
        print(f"  hotels[0]  keys: {hkeys}")
    if rentals_props and hotels_props:
        rset, hset = set(rentals_props[0].keys()), set(hotels_props[0].keys())
        only_rentals = rset - hset
        only_hotels = hset - rset
        print(f"  fields only on rentals: {sorted(only_rentals) or 'none'}")
        print(f"  fields only on hotels:  {sorted(only_hotels) or 'none'}")

    # Q2: Prices array
    print(f"\n--- Q2: `prices` array contents ---")
    print(f"vacation_rentals side:")
    for p in rentals_props[:5]:
        prices = p.get("prices") or []
        print(f"  {p.get('name', '?')!r}: {len(prices)} sources → {_prices_summary(prices)}")
    print(f"hotels side:")
    for p in hotels_props[:5]:
        prices = p.get("prices") or []
        print(f"  {p.get('name', '?')!r}: {len(prices)} sources → {_prices_summary(prices)}")

    # Q3: property_token stability across modes
    print(f"\n--- Q3: property_token stability across modes ---")
    rentals_by_name = {p.get("name"): p.get("property_token") for p in rentals_props}
    hotels_by_name = {p.get("name"): p.get("property_token") for p in hotels_props}
    overlap = set(rentals_by_name.keys()) & set(hotels_by_name.keys())
    if not overlap:
        print(f"  No name overlap between the two responses ({len(rentals_props)} rentals, {len(hotels_props)} hotels).")
        print(f"  Cannot directly test token stability — dedup fallback is the safe default.")
    else:
        print(f"  Name overlap: {len(overlap)} properties appear in both responses.")
        same_token = 0
        for name in overlap:
            rtok = rentals_by_name[name]
            htok = hotels_by_name[name]
            same = "SAME" if rtok == htok else "DIFFERENT"
            print(f"    {name!r}: {same}")
            if rtok == htok:
                same_token += 1
        print(f"  Token-stability rate: {same_token}/{len(overlap)} match across modes.")

    # Q4: Filter scoping — confirmed via the FIRST script run (which we
    # left commented above for posterity).
    print(f"\n--- Q4: Filter scoping (already empirically confirmed) ---")
    print(f"  Sending hotel_class with vacation_rentals=true returns HTTP 400:")
    print(f"  \"You're not allowed to enable hotel_class for Vacation Rentals search.\"")
    print(f"  → IMPLICATION: Phase 1 orchestration MUST scope filters at request-build time.")
    print(f"     - hotel_class, free_cancellation, special_offers, eco_certified → hotels call only")
    print(f"     - bedrooms, bathrooms → vacation_rentals call only")

    # Q5: Latency
    print(f"\n--- Q5: Latency ---")
    rmeta = (rentals_body.get("search_metadata") or {}).get("total_time_taken")
    hmeta = (hotels_body.get("search_metadata") or {}).get("total_time_taken")
    print(f"  vacation_rentals=true  → wall {rentals_elapsed:.2f}s | SerpAPI metadata: {rmeta}")
    print(f"  vacation_rentals=false → wall {hotels_elapsed:.2f}s | SerpAPI metadata: {hmeta}")
    print(f"  → Parallel-fanout merged path would take ~max({rentals_elapsed:.1f}s, {hotels_elapsed:.1f}s).")

    # Type distribution (bonus — shows what `type` values appear)
    print(f"\n--- Bonus: `type` value distribution ---")
    from collections import Counter
    rentals_types = Counter(p.get("type") for p in rentals_props)
    hotels_types = Counter(p.get("type") for p in hotels_props)
    print(f"  vacation_rentals=true:  {dict(rentals_types)}")
    print(f"  vacation_rentals=false: {dict(hotels_types)}")

    # Essential info (vacation-rental-specific field per docs)
    print(f"\n--- Bonus: essential_info on rentals (drives bedrooms/bathrooms display) ---")
    for p in rentals_props[:3]:
        ei = p.get("essential_info")
        print(f"  {p.get('name', '?')!r}: {ei}")

    print("\n" + "=" * 70)
    print("DONE.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
