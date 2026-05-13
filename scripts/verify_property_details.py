#!/usr/bin/env python
"""Phase 0 (stays, get_stay_details): verify SerpAPI's per-property details payload.

Loads the existing Tampere vacation-rentals list-endpoint fixture, picks the
first property, and calls SerpAPI's google_hotels endpoint a second time with
the captured `property_token`. SerpAPI promotes this from a "list" response to
a per-property "details" response.

The probe answers the design questions for the upcoming `get_stay_details`
MCP tool:

  1. Top-level keys — does the shape differ from the list response?
  2. Which fields appear that the list response did NOT have, especially
     `address`, `prices[].link/url`, `reviews_breakdown`, richer
     `nearby_places`?
  3. Is there an `address` field, and what does it look like?
  4. For each booking partner in `prices`, is there a `link`/`url` that
     lands directly on a booking flow?

Usage:
    set -a; source .env; set +a
    .venv/bin/python scripts/verify_property_details.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

LIST_FIXTURE = Path("tests/fixtures/serpapi_vacation_rentals_tampere.json")
OUT_FIXTURE = Path("tests/fixtures/serpapi_property_details_tampere.json")
SERPAPI_URL = "https://serpapi.com/search"


def main() -> int:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("ERROR: SERPAPI_KEY not set. Run `set -a; source .env; set +a` first.")
        return 2

    if not LIST_FIXTURE.exists():
        print(f"ERROR: list-endpoint fixture not found at {LIST_FIXTURE}")
        return 2

    list_body = json.loads(LIST_FIXTURE.read_text())
    list_props = list_body.get("properties") or []
    if not list_props:
        print(f"ERROR: no `properties` in {LIST_FIXTURE}")
        return 2

    sample_list = list_props[0]
    token = sample_list.get("property_token")
    if not token:
        print("ERROR: first property has no property_token")
        return 2

    list_keys = set(sample_list.keys())
    print(f"List-endpoint sample: {sample_list.get('name')!r}")
    print(f"  property_token: {token}")
    print(f"  list-endpoint keys ({len(list_keys)}): {sorted(list_keys)}")

    params = {
        "engine": "google_hotels",
        "q": "Tampere",
        "check_in_date": "2026-06-15",
        "check_out_date": "2026-06-18",
        "adults": "2",
        "currency": "EUR",
        "vacation_rentals": "true",
        "property_token": token,
        "api_key": api_key,
    }
    safe = {k: ("<redacted>" if k == "api_key" else v) for k, v in params.items()}
    print()
    print(f"→ GET {SERPAPI_URL}")
    print(f"  params: {safe}")

    started = time.monotonic()
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(SERPAPI_URL, params=params)
    elapsed = time.monotonic() - started

    print(f"  status: {resp.status_code} in {elapsed:.2f}s")
    if resp.status_code != 200:
        print()
        print("Body:")
        print(resp.text[:2000])
        return 1

    body = resp.json()
    OUT_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FIXTURE.write_text(json.dumps(body, indent=2))
    print(f"  saved to {OUT_FIXTURE}")

    if isinstance(body.get("error"), str):
        print()
        print(f"⚠ Body-level error: {body['error']}")
        return 1

    # Top-level keys
    print()
    print("=" * 78)
    print("TOP-LEVEL KEYS (details endpoint)")
    print("=" * 78)
    for k in sorted(body.keys()):
        v = body[k]
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)} item(s)]")
        elif isinstance(v, dict):
            print(f"  {k}: dict[{len(v)} key(s)]: {sorted(v.keys())[:8]}")
        elif isinstance(v, str):
            short = v if len(v) <= 100 else v[:97] + "…"
            print(f"  {k}: {short!r}")
        else:
            print(f"  {k}: {v!r}")

    # Compare keys: list-endpoint property vs details-endpoint top-level.
    # SerpAPI's details payload is a single property at the top level
    # (not wrapped in `properties[]`).
    details_keys = set(body.keys())
    new_keys = details_keys - list_keys
    missing_keys = list_keys - details_keys
    shared_keys = list_keys & details_keys

    print()
    print("=" * 78)
    print("KEY DIFF — details vs list-endpoint property[0]")
    print("=" * 78)
    print(f"shared keys ({len(shared_keys)}): {sorted(shared_keys)}")
    print()
    print(f"NEW in details ({len(new_keys)}): {sorted(new_keys)}")
    print()
    print(f"present in list but absent here ({len(missing_keys)}): {sorted(missing_keys)}")

    # Q: address
    print()
    print("=" * 78)
    print("ADDRESS")
    print("=" * 78)
    addr = body.get("address")
    if addr is None:
        # Also probe common alternative names
        for alt in ("formatted_address", "location", "full_address"):
            if alt in body:
                print(f"  no `address` field, but `{alt}` present: {body[alt]!r}")
                break
        else:
            print("  no `address` / `formatted_address` / `location` field present")
    else:
        print(f"  type: {type(addr).__name__}")
        if isinstance(addr, str):
            print(f"  value: {addr!r}")
        elif isinstance(addr, dict):
            print(f"  keys: {sorted(addr.keys())}")
            print(f"  value: {json.dumps(addr, ensure_ascii=False)[:400]}")
        else:
            print(f"  value: {json.dumps(addr, ensure_ascii=False, default=str)[:400]}")

    # Q: prices — booking-partner direct URLs
    print()
    print("=" * 78)
    print("PRICES — booking-partner links")
    print("=" * 78)
    prices = body.get("prices") or []
    print(f"  prices[] count: {len(prices)}")
    if prices:
        first = prices[0]
        print(f"  prices[0] keys: {sorted(first.keys()) if isinstance(first, dict) else type(first).__name__}")
    for i, p in enumerate(prices):
        if not isinstance(p, dict):
            continue
        source = p.get("source")
        url_field = None
        url_value = None
        for cand in ("link", "url", "booking_link", "booking_url", "official_link"):
            if p.get(cand):
                url_field = cand
                url_value = p[cand]
                break
        flag = "✓" if url_value else "✗"
        short_url = (url_value[:100] + "…") if url_value and len(url_value) > 100 else url_value
        print(f"  [{i}] {flag} source={source!r} url_field={url_field!r} url={short_url!r}")

    # Q: reviews_breakdown
    print()
    print("=" * 78)
    print("REVIEWS_BREAKDOWN / RATINGS")
    print("=" * 78)
    rb = body.get("reviews_breakdown")
    if rb is None:
        for alt in ("ratings", "review_breakdown", "reviews_summary"):
            if alt in body:
                print(f"  no `reviews_breakdown` but `{alt}` present:")
                print(f"  {json.dumps(body[alt], ensure_ascii=False, default=str)[:400]}")
                break
        else:
            print("  no reviews_breakdown / ratings field")
    else:
        print(f"  type: {type(rb).__name__}")
        print(f"  value: {json.dumps(rb, ensure_ascii=False, default=str)[:400]}")

    # Q: nearby_places — richer than list?
    print()
    print("=" * 78)
    print("NEARBY_PLACES — list vs details")
    print("=" * 78)
    list_np = sample_list.get("nearby_places") or []
    det_np = body.get("nearby_places") or []
    print(f"  list:    {len(list_np)} entries")
    print(f"  details: {len(det_np)} entries")
    if det_np:
        sample_np = det_np[0]
        print(f"  details[0] keys: {sorted(sample_np.keys()) if isinstance(sample_np, dict) else type(sample_np).__name__}")
        print(f"  details[0] value: {json.dumps(sample_np, ensure_ascii=False, default=str)[:300]}")

    # Latency summary
    print()
    print("=" * 78)
    print(f"WALL-CLOCK LATENCY: {elapsed:.2f}s")
    print("=" * 78)

    print()
    print("Done. Review the diff above before designing `get_stay_details`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
