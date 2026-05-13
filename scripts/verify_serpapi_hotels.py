#!/usr/bin/env python
"""Phase 0 (hotels, SerpAPI pivot): verify SerpAPI's google_hotels endpoint.

Same Tampere query as the fast-hotels attempt. Saves the full JSON to
tests/fixtures/serpapi_hotels_tampere_success.json and prints a structure
summary covering the four design-relevant questions:

  1. Does each property carry a `property_token` we can use as offer_id?
  2. Does it ship a `description` and `hotel_type` field?
  3. How many `images` per property — is the spec's cap of 5 reasonable?
  4. Per-night vs total price split, currency exposure?

Usage:
    set -a; source .env; set +a
    .venv/bin/python scripts/verify_serpapi_hotels.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

FIXTURE = Path("tests/fixtures/serpapi_hotels_tampere_success.json")
SERPAPI_URL = "https://serpapi.com/search"


def main() -> int:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("ERROR: SERPAPI_KEY not set. Run `set -a; source .env; set +a` first.")
        return 2

    params = {
        "engine": "google_hotels",
        "q": "Tampere",
        "check_in_date": "2026-06-15",
        "check_out_date": "2026-06-18",
        "adults": "2",
        "currency": "EUR",
        "gl": "fi",   # geolocation hint — Finland
        "hl": "en",   # response language
        "api_key": api_key,
    }
    safe = {k: ("<redacted>" if k == "api_key" else v) for k, v in params.items()}
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
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(body, indent=2))
    print(f"  saved to {FIXTURE}")

    # Top-level keys
    print()
    print("=" * 78)
    print("TOP-LEVEL KEYS")
    print("=" * 78)
    for k in sorted(body.keys()):
        v = body[k]
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)} item(s)]")
        elif isinstance(v, dict):
            print(f"  {k}: dict[{len(v)} key(s)]: {sorted(v.keys())[:6]}")
        else:
            print(f"  {k}: {type(v).__name__}")

    # SerpAPI body-level errors (200 with `error` field)
    if isinstance(body.get("error"), str):
        print()
        print(f"⚠ Body-level error: {body['error']}")
        return 1

    # Find the result list. SerpAPI typically uses `properties`.
    props = body.get("properties") or body.get("hotels_results") or []
    if not props:
        print()
        print("⚠ No properties returned. Cannot continue verification.")
        return 1

    print()
    print("=" * 78)
    print(f"PROPERTY COUNT: {len(props)}")
    print("=" * 78)

    sample = props[0]
    print()
    print("=" * 78)
    print("SAMPLE PROPERTY[0] — all keys, truncated values")
    print("=" * 78)
    for k in sorted(sample.keys()):
        v = sample[k]
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]  example[0]: {json.dumps(v[0], default=str)[:120] if v else '(empty)'}")
        elif isinstance(v, dict):
            print(f"  {k}: dict[{len(v)} keys]: {sorted(v.keys())[:6]}")
        elif isinstance(v, str):
            short = v if len(v) <= 120 else v[:117] + "…"
            print(f"  {k}: {short!r}")
        else:
            print(f"  {k}: {v!r}")

    # Design-relevant probes
    print()
    print("=" * 78)
    print("DESIGN QUESTIONS")
    print("=" * 78)

    # Q1 — property_token presence
    have_token = sum(1 for p in props if p.get("property_token"))
    print(f"Q1  property_token present: {have_token}/{len(props)} properties")

    # Q2 — description and hotel_type / type
    have_desc = sum(1 for p in props if p.get("description"))
    type_fields = ("type", "hotel_type", "property_type", "category")
    types_seen: dict[str, int] = {}
    type_field_used: str | None = None
    for p in props:
        for f in type_fields:
            v = p.get(f)
            if v:
                type_field_used = f
                types_seen[v] = types_seen.get(v, 0) + 1
                break
    print(f"Q2a description present: {have_desc}/{len(props)}")
    print(f"Q2b type-like field used: {type_field_used!r}; values seen: {types_seen}")

    # Q3 — image counts
    image_field_candidates = ("images", "photos", "thumbnails")
    image_field = next((f for f in image_field_candidates if f in sample), None)
    if image_field:
        counts = [len(p.get(image_field) or []) for p in props]
        print(f"Q3  image field: {image_field!r}; counts min/median/max: {min(counts)} / {sorted(counts)[len(counts)//2]} / {max(counts)}")
        # Look at the structure of a single image
        first_imgs = sample.get(image_field) or []
        if first_imgs:
            print(f"    image[0] shape: {type(first_imgs[0]).__name__}; keys: {sorted(first_imgs[0].keys()) if isinstance(first_imgs[0], dict) else 'N/A'}")
    else:
        print(f"Q3  no image field found in {image_field_candidates}")

    # Q4 — price + currency split
    price_keys = [k for k in sample.keys() if "price" in k.lower() or "rate" in k.lower()]
    print(f"Q4  price-related keys on sample: {price_keys}")
    for k in price_keys:
        print(f"    {k}: {json.dumps(sample.get(k), default=str)[:200]}")

    print()
    print("=" * 78)
    print("Done. Review the response above before Phase 1 build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
