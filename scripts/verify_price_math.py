#!/usr/bin/env python
"""Verify whether SerpAPI's `price` field is per-passenger or total.

Runs the same HEL→IAD one-way query with 1 adult and then 2 adults, prints
both totals. If the 2-adult total is roughly 2× the 1-adult total, the
existing assumption in normalize.py (price = total for the requested
passenger count, divide for per-adult) is correct. If the prices are
similar, the field is per-passenger and the normalizer should be inverted.

Cost: 2 SerpAPI calls.

Usage:
    set -a; source .env; set +a
    .venv/bin/python scripts/verify_price_math.py
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

from flights_mcp.models import SearchFlightsInput
from flights_mcp.serpapi.client import SerpAPIClient


async def search_with(adults: int) -> dict:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("ERROR: SERPAPI_KEY not set. Run `set -a; source .env; set +a` first.")
        sys.exit(2)
    async with httpx.AsyncClient(timeout=30.0) as http:
        client = SerpAPIClient(http=http, api_key=api_key)
        params = SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date="2026-05-18",
            adults=adults, max_results=1,
        )
        offers = await client.search(params)
        return {
            "adults": adults,
            "total_price": offers[0].total_price,
            "price_per_adult": offers[0].price_per_adult,
        }


async def main() -> int:
    print("Running two HEL→IAD one-way searches (1 adult, then 2 adults)…\n")
    one = await search_with(1)
    two = await search_with(2)

    print(f"  1 adult:  total={one['total_price']:>8.2f}  per_adult={one['price_per_adult']:>8.2f}")
    print(f"  2 adults: total={two['total_price']:>8.2f}  per_adult={two['price_per_adult']:>8.2f}")
    print()

    # Simple sanity check.
    ratio = two["total_price"] / one["total_price"] if one["total_price"] else 0
    print(f"  total_price ratio (2-adult / 1-adult) = {ratio:.2f}")
    print()
    if 1.7 <= ratio <= 2.3:
        print("✓ Ratio is ~2×. SerpAPI's `price` IS the total for the requested")
        print("  passenger count. normalize.py's assumption is correct: price is")
        print("  the total, divide by adults for per-adult.")
        return 0
    if 0.85 <= ratio <= 1.15:
        print("✗ Ratio is ~1×. SerpAPI's `price` is PER-PASSENGER, not total.")
        print("  normalize.py needs to flip the math: price IS per-adult, and")
        print("  total_price = price × adults.")
        print()
        print("  Fix: in build_one_way_offers and build_round_trip_offer in")
        print("  src/flights_mcp/serpapi/normalize.py, swap the per_adult/total")
        print("  derivations.")
        return 1
    print(f"⚠ Ratio is unexpected ({ratio:.2f}). Could be different fare buckets")
    print("  picked for 1 vs 2 adults. Inspect manually before changing anything.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
