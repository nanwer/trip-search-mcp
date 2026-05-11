#!/usr/bin/env python
"""Phase 0 (migration): Verify SerpAPI's Google Flights endpoint.

Reads SERPAPI_KEY from the environment, makes ONE call for HEL→IAD round-trip,
prints the full response to stdout, and saves it as a test fixture for the
upcoming migration.

This is a verification probe, not part of the production code. It exists so we
can answer two open design questions before writing the new client:
  1. Does SerpAPI return a complete round-trip in one response, or does it
     require a follow-up call using `departure_token` for the return leg?
  2. Are timestamps timezone-aware (e.g. "+03:00") or local-airport only?

Usage:
    set -a; source .env; set +a
    .venv/bin/python scripts/verify_serpapi.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

OUT_PATH = Path("tests/fixtures/serpapi_hel_iad_success.json")
SERPAPI_URL = "https://serpapi.com/search"


async def main() -> int:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("ERROR: SERPAPI_KEY not set in the environment.")
        print("       Add `SERPAPI_KEY=<your key>` to .env, then run:")
        print("       set -a; source .env; set +a")
        print("       .venv/bin/python scripts/verify_serpapi.py")
        return 2

    params = {
        "engine": "google_flights",
        "departure_id": "HEL",
        "arrival_id": "IAD",
        "outbound_date": "2026-05-18",
        "return_date": "2026-05-29",
        "type": "1",          # 1 = round trip
        "adults": "1",
        "currency": "USD",
        "api_key": api_key,
    }

    safe_params = {k: ("<redacted>" if k == "api_key" else v) for k, v in params.items()}
    print(f"→ GET {SERPAPI_URL}")
    print(f"  params: {safe_params}")

    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(SERPAPI_URL, params=params)

    print(f"  status: {resp.status_code}")
    if resp.status_code != 200:
        print()
        print("Body:")
        print(resp.text[:2000])
        return 1

    body = resp.json()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(body, indent=2))
    print(f"  saved to {OUT_PATH}")

    # Full response (spec requires this for review).
    print()
    print("=" * 78)
    print("FULL RESPONSE")
    print("=" * 78)
    print(json.dumps(body, indent=2))

    # Shape survey targeting the two open design questions.
    print()
    print("=" * 78)
    print("SHAPE SURVEY (for design review)")
    print("=" * 78)
    top_keys = sorted(body.keys())
    print(f"Top-level keys: {top_keys}")
    print()
    print("Q1: ROUND-TRIP REPRESENTATION")

    # SerpAPI Google Flights typically returns `best_flights` and/or
    # `other_flights`. A complete round-trip would have both legs inline; an
    # incomplete one would include a `departure_token` for the follow-up call.
    for bucket in ("best_flights", "other_flights"):
        if bucket not in body:
            print(f"  {bucket}: not present")
            continue
        items = body[bucket] or []
        print(f"  {bucket}: {len(items)} item(s)")
        if items:
            sample = items[0]
            sample_keys = sorted(sample.keys())
            print(f"    sample item keys: {sample_keys}")
            print(f"    has 'flights' inline: {'flights' in sample}")
            print(f"    has 'departure_token' (follow-up needed): {'departure_token' in sample}")
            if "flights" in sample:
                inline_count = len(sample.get("flights") or [])
                print(f"    inline 'flights' length: {inline_count}")

    print()
    print("Q2: TIMESTAMP FORMAT")
    # Walk to the first segment we can find and inspect its time fields.
    sample_segment = None
    for bucket in ("best_flights", "other_flights"):
        items = body.get(bucket) or []
        for item in items:
            flights = item.get("flights") or []
            if flights:
                sample_segment = flights[0]
                break
        if sample_segment:
            break

    if sample_segment is None:
        print("  no flight segment found to inspect")
    else:
        for endpoint in ("departure_airport", "arrival_airport"):
            ep = sample_segment.get(endpoint) or {}
            time_str = ep.get("time")
            print(f"  {endpoint}.time: {time_str!r}")
            if isinstance(time_str, str):
                has_offset = ("+" in time_str) or time_str.endswith("Z") or (
                    len(time_str) >= 6 and time_str[-6] == "-" and time_str[-3] == ":"
                )
                print(f"    timezone-aware? {has_offset}")
        # Also note useful extras for the design discussion.
        for k in ("duration", "airline", "flight_number", "travel_class"):
            if k in sample_segment:
                print(f"  segment.{k}: {sample_segment[k]!r}")

    print()
    print("=" * 78)
    print("Done. Review the response above and the SHAPE SURVEY before Phase 1.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
