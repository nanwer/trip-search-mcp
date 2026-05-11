#!/usr/bin/env python
"""Fetch a flight-offers response from Amadeus and save it as a test fixture.

After Phase 0.1 signup, run this once to verify your credentials work and to
capture a real Amadeus response for fixture-driven tests.

Usage:
    set -a; source .env; set +a
    .venv/bin/python scripts/fetch_phase0_fixture.py
    # or with a different route if HEL-IAD isn't in the test cache:
    .venv/bin/python scripts/fetch_phase0_fixture.py --origin MAD --destination FRA
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

BASE = "https://test.api.amadeus.com"
DEFAULT_OUT = Path("tests/fixtures/hel_iad_round_trip.json")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch and save an Amadeus flight-offers fixture.")
    p.add_argument("--origin", default="HEL")
    p.add_argument("--destination", default="IAD")
    p.add_argument("--departure-date", default="2026-05-18")
    p.add_argument("--return-date", default="2026-05-29")
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--max-results", type=int, default=10)
    p.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Where to write the JSON (default: {DEFAULT_OUT})",
    )
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    client_id = os.environ.get("AMADEUS_CLIENT_ID")
    client_secret = os.environ.get("AMADEUS_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("ERROR: AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET must be set in the environment.")
        print("       Did you `set -a; source .env; set +a` before running?")
        return 2

    async with httpx.AsyncClient(timeout=30.0) as http:
        print(f"→ POST {BASE}/v1/security/oauth2/token")
        token_resp = await http.post(
            f"{BASE}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            print(f"  ✗ status {token_resp.status_code}")
            print(f"  body: {token_resp.text[:500]}")
            print("\nCommon causes:")
            print("  - API Key/Secret mismatch (copy carefully — they're long)")
            print("  - App still provisioning (wait ~1 minute after creation, then retry)")
            return 1
        token = token_resp.json()["access_token"]
        print(f"  ✓ token acquired ({token[:8]}…)")

        params = {
            "originLocationCode": args.origin,
            "destinationLocationCode": args.destination,
            "departureDate": args.departure_date,
            "adults": str(args.adults),
            "currencyCode": "USD",
            "max": str(args.max_results),
        }
        if args.return_date:
            params["returnDate"] = args.return_date

        print(f"→ GET {BASE}/v2/shopping/flight-offers ({args.origin}→{args.destination})")
        search_resp = await http.get(
            f"{BASE}/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    if search_resp.status_code != 200:
        print(f"  ✗ status {search_resp.status_code}")
        print(f"  body: {search_resp.text[:500]}")
        return 1

    body = search_resp.json()
    count = body.get("meta", {}).get("count", 0)
    print(f"  ✓ {count} offers returned")

    if count == 0:
        print()
        print("⚠️  Zero offers. In the test environment, this almost always means")
        print("   this route isn't in Amadeus's cached subset.")
        print()
        print("   Options:")
        print("   1. Try a known-good route — pairs that are usually cached:")
        print("      MAD→FRA, LHR→CDG, JFK→LAX, FRA→MUC, BCN→LIS")
        print("      Re-run with: --origin MAD --destination FRA")
        print("   2. Check the amadeus4dev/data-collection repo for the full")
        print("      list of cached routes.")
        print("   3. Apply for production credentials (1-7 business days).")
        print()
        print(f"   The empty response will still be saved to {args.out}")
        print("   so you can inspect it, but it won't be useful as a test fixture.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(body, indent=2))
    print(f"  ✓ saved to {args.out}")

    if count > 0:
        first_seg = body["data"][0]["itineraries"][0]["segments"][0]
        at = first_seg["departure"]["at"]
        print()
        print(f"Time-format spot-check:  departure.at = {at!r}")
        if "+" in at or at.endswith("Z"):
            print("  ⚠️  Times include a timezone offset.")
            print("     The spec assumes local-airport time with no offset.")
            print("     normalize.py and the time-format regression test in")
            print("     tests/test_search_flights.py may need a small update.")
        else:
            print("  ✓ Matches the spec (no offset, local airport time)")

    print()
    print("Done. Next steps:")
    print("  1. Re-run the test suite:  .venv/bin/pytest -v")
    print("  2. If everything is green, you're ready for the MCP Inspector check.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
