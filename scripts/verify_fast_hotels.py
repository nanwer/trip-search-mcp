#!/usr/bin/env python
"""Phase 0 (hotels extension): verify the `fast-hotels` library.

Searches Tampere hotels for check-in 2026-06-15 → check-out 2026-06-18, 2 adults,
saves the result to a fixture, prints a structure summary covering the five
Phase 0 questions.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

from fast_hotels import get_hotels
from fast_hotels.hotels_impl import Guests, HotelData

FIXTURE = Path("tests/fixtures/fast_hotels_tampere_success.json")


def _dump(obj):
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def main() -> int:
    print("→ fast_hotels.get_hotels(Tampere, 2026-06-15 → 2026-06-18, 2 adults)")
    hotel_data = [
        HotelData(
            checkin_date="2026-06-15",
            checkout_date="2026-06-18",
            location="Tampere",
        ),
    ]
    guests = Guests(adults=2, children=0)

    started = time.monotonic()
    try:
        result = get_hotels(
            hotel_data=hotel_data,
            guests=guests,
            room_type="standard",
            fetch_mode="common",  # falls back to playwright if "common" fails
            limit=25,
            sort_by=None,  # sort_by only supports "price" / "rating" — explore both later
        )
    except Exception as e:
        print(f"  ✗ get_hotels failed: {type(e).__name__}: {e}")
        return 1
    elapsed = time.monotonic() - started

    hotels = result.hotels if result else []
    print(f"  ✓ {len(hotels)} hotel(s) in {elapsed:.2f}s")

    payload = {
        "result": {
            "lowest_price": result.lowest_price if result else None,
            "current_price": result.current_price if result else None,
            "hotels": [_dump(h) for h in hotels],
        },
    }
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(payload, indent=2, default=str))
    print(f"  saved to {FIXTURE}")

    if not hotels:
        print()
        print("⚠ No hotels returned. fast-hotels may be stale; consider SerpAPI pivot.")
        return 0

    print()
    print("=" * 78)
    print("Q1 — RESPONSE SHAPE (per-hotel fields fast-hotels actually populates)")
    print("=" * 78)
    sample = hotels[0]
    print(f"  type: {type(sample).__name__}")
    print(f"  fields:")
    for k, v in asdict(sample).items():
        v_repr = (v[:80] + "…") if isinstance(v, str) and len(v) > 80 else repr(v)
        print(f"    {k}: {v_repr}")

    null_counts = {k: 0 for k in asdict(sample).keys()}
    for h in hotels:
        d = asdict(h)
        for k, v in d.items():
            if v is None or v == [] or v == "":
                null_counts[k] += 1
    print()
    print(f"  null/empty distribution across {len(hotels)} hotels:")
    for k, n in null_counts.items():
        print(f"    {k}: {n}/{len(hotels)} null/empty")

    print()
    print("=" * 78)
    print("Q2 — PRICE FORMAT (total vs per-night, currency)")
    print("=" * 78)
    prices = [h.price for h in hotels if h.price is not None]
    if prices:
        nights = 3  # 06-15 to 06-18
        print(f"  raw price values (first 5): {prices[:5]}")
        print(f"  range: {min(prices)} – {max(prices)}")
        print(f"  trip is {nights} nights; if per-night you'd expect ~{min(prices)*nights}–{max(prices)*nights} total")
        print(f"  currency: NOT exposed in Hotel dataclass — would need to infer or assume")
    print(f"  Result.lowest_price: {result.lowest_price!r}")
    print(f"  Result.current_price: {result.current_price!r}")

    print()
    print("=" * 78)
    print("Q3 — DATE HANDLING")
    print("=" * 78)
    print("  HotelData.__init__ accepts checkin_date/checkout_date as plain STRINGS")
    print("  (verified by source inspection — no datetime parsing on the way in)")

    print()
    print("=" * 78)
    print("Q4 — SORT/FILTER PRIMITIVES")
    print("=" * 78)
    print("  Native sort_by accepted values (from source): 'price', 'rating' ONLY")
    print("  Spec wanted: BEST, PRICE_LOW, PRICE_HIGH, RATING, REVIEW_SCORE")
    print("  Native amenities filter: yes (List[str] passed to HotelData and get_hotels)")
    print("  Native min_rating filter: NO — post-filter required")
    print("  Native min_review_score filter: NO — and review_score isn't even returned")
    print("  Native max_price filter: NO — post-filter required")
    print("  Native limit: YES (int)")

    print()
    print("=" * 78)
    print("Q5 — LIBRARY HEALTH")
    print("=" * 78)
    print(f"  Live call returned {len(hotels)} hotels in {elapsed:.2f}s.")
    print(f"  Package version: 0.2.1 (PyPI); last GitHub push June 2025 per spec.")
    print(f"  Packaging issue: pyproject.toml did NOT declare selectolax or primp")
    print(f"    as runtime deps — I had to `uv pip install` them manually for `import")
    print(f"    fast_hotels` to succeed.")

    print()
    print("Done. Review the response above before any backend code gets written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
