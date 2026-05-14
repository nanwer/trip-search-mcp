#!/usr/bin/env python
"""Phase 0 (Track F, get_activity_details): verify SerpAPI's
`tripadvisor_place` engine.

One live call against a place_id pulled from the Track D capture
(`serpapi_tripadvisor_lisbon_cooking.json` — picking the bookable
Cooking Lisbon entry, place_id=5603536).

Saves fixture, prints summary on: price shape, Viator URL presence,
duration field, available-dates structure, full description length.

Usage:
    set -a; source .env; set +a
    .venv/bin/python scripts/verify_tripadvisor_place_details.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

SERPAPI_URL = "https://serpapi.com/search"
USER_AGENT = "trip-search-mcp/0.2 (https://github.com/nanwer/trip-search-mcp)"

# Pulled from the Track D fixture — Cooking Lisbon (ATTRACTION).
PLACE_ID = "5603536"
FIXTURE = Path("tests/fixtures/serpapi_tripadvisor_place_details.json")


def main() -> int:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("ERROR: SERPAPI_KEY not set.")
        return 2

    print("=" * 70)
    print("Phase 0 verification: SerpAPI tripadvisor_place endpoint")
    print("=" * 70)

    # Engine is `tripadvisor_place`, NOT `tripadvisor` — confirmed by
    # inspecting `serpapi_link` from a Track D fixture entry.
    params = {
        "engine": "tripadvisor_place",
        "place_id": PLACE_ID,
        "tripadvisor_domain": "www.tripadvisor.com",
        "hl": "en",
        "api_key": api_key,
    }
    started = time.monotonic()
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(SERPAPI_URL, params=params, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as e:
        print(f"  FAILED: {e}")
        return 1
    elapsed = time.monotonic() - started
    r.raise_for_status()

    body = r.json()
    if isinstance(body.get("search_parameters"), dict):
        body["search_parameters"].pop("api_key", None)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(body, indent=2))
    print(f"  → {FIXTURE}  ({elapsed:.2f}s, {len(r.content)} bytes)")

    print(f"\nTop-level keys: {sorted(body.keys())}")

    # ---- Try a few common nested keys for activity-detail fields -----------
    print(f"\n--- Q1: Price shape ---")
    for k in ("price", "price_range", "tour_price", "lowest_price", "starting_price"):
        if k in body:
            print(f"  {k}: {json.dumps(body[k])[:200]}")
    if not any(k in body for k in ("price", "price_range", "tour_price", "lowest_price", "starting_price")):
        print(f"  (no obvious price field at top level)")

    print(f"\n--- Q2: Viator URL ---")
    for k in ("viator_url", "booking_url", "external_url", "official_url"):
        if k in body:
            print(f"  {k}: {body[k][:200] if isinstance(body[k], str) else body[k]}")
    print(f"  link: {body.get('link', '!! MISSING !!')[:120] if body.get('link') else '!! MISSING !!'}")

    print(f"\n--- Q3: Duration ---")
    for k in ("duration", "tour_duration", "length"):
        if k in body:
            print(f"  {k}: {body[k]!r}")
    if not any(k in body for k in ("duration", "tour_duration", "length")):
        print(f"  (no duration field at top level)")

    print(f"\n--- Q4: Available dates / booking calendar ---")
    for k in ("availability", "available_dates", "calendar", "bookable_dates"):
        if k in body:
            print(f"  {k}: {json.dumps(body[k])[:300]}")
    if not any(k in body for k in ("availability", "available_dates", "calendar", "bookable_dates")):
        print(f"  (no availability info)")

    print(f"\n--- Q5: Description length ---")
    desc = body.get("description") or body.get("about", {}).get("description") or ""
    print(f"  description length: {len(desc)}")
    print(f"  preview: {desc[:200]!r}")

    print(f"\n--- Q6: Top-level field sizes (non-string) ---")
    for k, v in body.items():
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
        elif isinstance(v, dict):
            print(f"  {k}: dict({sorted(v.keys())[:5]}...)")

    print("\n" + "=" * 70)
    print("DONE.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
