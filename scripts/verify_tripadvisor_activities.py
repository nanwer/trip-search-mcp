#!/usr/bin/env python
"""Phase 0 (Track D, search_activities): verify SerpAPI's Tripadvisor
engine with `ssrc=A` (Things to Do).

Three live calls to capture the response shape across:
  1. Generic location — "Lisbon" with ssrc=A
  2. Free-text filter — "cooking class Lisbon"
  3. (Bonus probe) — "boat tours Lisbon"

Saves fixtures and prints a summary covering Phase 0 questions:
  Q1. place_type distribution under ssrc=A
  Q2. field population per place_type
  Q3. thumbnail accessibility (note URLs for manual hotlink check)
  Q4. deep-link target (Tripadvisor listing vs direct Viator)
  Q5. free-text query expressiveness
  Q6. lat/lon precision
  Q7. pagination mechanics

Usage:
    set -a; source .env; set +a
    .venv/bin/python scripts/verify_tripadvisor_activities.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx

SERPAPI_URL = "https://serpapi.com/search"
USER_AGENT = "trip-search-mcp/0.2 (https://github.com/nanwer/trip-search-mcp)"

CASES = [
    ("lisbon_generic", {"q": "Lisbon", "ssrc": "A"}),
    ("lisbon_cooking", {"q": "cooking class Lisbon", "ssrc": "A"}),
    ("lisbon_boat", {"q": "boat tours Lisbon", "ssrc": "A"}),
]


def _call(api_key: str, extra: dict[str, str]) -> tuple[dict, float]:
    params: dict[str, str] = {
        "engine": "tripadvisor",
        "hl": "en",
        "api_key": api_key,
        **extra,
    }
    started = time.monotonic()
    with httpx.Client(timeout=30.0) as client:
        r = client.get(SERPAPI_URL, params=params, headers={"User-Agent": USER_AGENT})
    elapsed = time.monotonic() - started
    r.raise_for_status()
    return r.json(), elapsed


def _save(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body.get("search_parameters"), dict):
        body["search_parameters"].pop("api_key", None)
    path.write_text(json.dumps(body, indent=2))


def _find_results_key(body: dict) -> str | None:
    """SerpAPI's Tripadvisor engine uses different result-array names
    depending on the search type — `things_to_do_results`, `results`,
    `restaurants_results`, etc. Find whichever exists."""
    candidates = [
        "things_to_do_results", "results", "places", "search_results",
        "tripadvisor_results", "ads",
    ]
    for k in candidates:
        if k in body and isinstance(body[k], list):
            return k
    # Fallback: any top-level list of dicts.
    for k, v in body.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return k
    return None


def main() -> int:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("ERROR: SERPAPI_KEY not set.")
        return 2

    print("=" * 70)
    print("Phase 0 verification: SerpAPI Tripadvisor (ssrc=A, Things to Do)")
    print("=" * 70)

    results: dict[str, tuple[dict, float]] = {}
    for label, params in CASES:
        print(f"\n[{label}] params={params}")
        try:
            body, elapsed = _call(api_key, params)
        except httpx.HTTPStatusError as e:
            print(f"  FAILED: {e.response.status_code} {e.response.text[:200]}")
            return 1
        path = Path(f"tests/fixtures/serpapi_tripadvisor_{label}.json")
        _save(path, body)
        results_key = _find_results_key(body)
        rs = body.get(results_key, []) if results_key else []
        print(f"  → {path}  ({elapsed:.2f}s, results_key={results_key!r}, n={len(rs)})")
        results[label] = (body, elapsed)

    # ---- Top-level keys ----------------------------------------------------
    print("\n" + "=" * 70)
    print("Top-level response keys (per fixture)")
    print("=" * 70)
    for label, (body, _) in results.items():
        print(f"\n  {label}: {sorted(body.keys())}")

    # ---- Q1: place_type distribution + Q2: field population -----------------
    print("\n" + "=" * 70)
    print("Q1: place_type distribution + Q2: field shape")
    print("=" * 70)
    for label, (body, _) in results.items():
        results_key = _find_results_key(body)
        rs = body.get(results_key, []) if results_key else []
        if not rs:
            print(f"\n  {label}: empty")
            continue
        types = Counter(r.get("type") or r.get("place_type") or "(no type)" for r in rs)
        print(f"\n  {label}:")
        print(f"    types: {dict(types)}")
        # Show keys of first 2 results
        for i, r in enumerate(rs[:2]):
            print(f"    [{i}] keys: {sorted(r.keys())}")

    # ---- Q3 + Q4: thumbnail + deep-link sampling ---------------------------
    print("\n" + "=" * 70)
    print("Q3/Q4: thumbnail URLs + deep-link targets (host distribution)")
    print("=" * 70)
    for label, (body, _) in results.items():
        results_key = _find_results_key(body)
        rs = body.get(results_key, []) if results_key else []
        link_hosts: Counter[str] = Counter()
        thumb_hosts: Counter[str] = Counter()
        for r in rs:
            link = r.get("link") or r.get("booking_url") or ""
            thumb = r.get("thumbnail") or r.get("image") or ""
            if link:
                link_hosts[urlparse(link).netloc] += 1
            if thumb:
                thumb_hosts[urlparse(thumb).netloc] += 1
        print(f"\n  {label}:")
        print(f"    link hosts: {dict(link_hosts.most_common(5))}")
        print(f"    thumb hosts: {dict(thumb_hosts.most_common(5))}")

    # ---- Q5: free-text quality (compare top 3 from each cooking-vs-generic) -
    print("\n" + "=" * 70)
    print("Q5: free-text query expressiveness (top 3 per fixture)")
    print("=" * 70)
    for label, (body, _) in results.items():
        results_key = _find_results_key(body)
        rs = body.get(results_key, []) if results_key else []
        print(f"\n  {label}:")
        for i, r in enumerate(rs[:3]):
            name = r.get("title") or r.get("name") or "?"
            print(f"    {i+1}. {name!r}")

    # ---- Q6: lat/lon presence + sample value -------------------------------
    print("\n" + "=" * 70)
    print("Q6: lat/lon presence")
    print("=" * 70)
    for label, (body, _) in results.items():
        results_key = _find_results_key(body)
        rs = body.get(results_key, []) if results_key else []
        if not rs:
            continue
        with_coords = sum(1 for r in rs if r.get("gps_coordinates") or r.get("latitude"))
        print(f"  {label}: {with_coords}/{len(rs)} results have lat/lon")
        if rs:
            sample = rs[0].get("gps_coordinates") or {
                "latitude": rs[0].get("latitude"),
                "longitude": rs[0].get("longitude"),
            }
            print(f"    sample (results[0]): {sample}")

    # ---- Q7: pagination ----------------------------------------------------
    print("\n" + "=" * 70)
    print("Q7: pagination signals")
    print("=" * 70)
    for label, (body, _) in results.items():
        sp = body.get("search_parameters", {})
        pi = body.get("pagination") or {}
        print(f"\n  {label}: pagination={pi}, search_parameters keys={sorted(sp.keys())}")

    print("\n" + "=" * 70)
    print("DONE.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
