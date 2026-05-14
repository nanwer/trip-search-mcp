#!/usr/bin/env python
"""Phase 0 (Track E, search_events): verify SerpAPI's google_events engine.

Three live calls to capture the response shape across:
  1. Generic city query — "Events in Lisbon"
  2. Type + city + month — "Concerts in Lisbon June 2026"
  3. Specific event + city + window — "BTS tour Paris July 2026"

Saves 3 fixtures and prints a summary covering Phase 0 questions:
  Q1. Date-filter mechanics (htichips parameter values)
  Q2. Query-phrasing impact on result quality
  Q3. Field parity across event types (title, address, venue, link, date)
  Q4. Where ticket_url actually goes

Usage:
    set -a; source .env; set +a    # or export SERPAPI_KEY
    .venv/bin/python scripts/verify_google_events.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

SERPAPI_URL = "https://serpapi.com/search"
USER_AGENT = "trip-search-mcp/0.2 (https://github.com/nanwer/trip-search-mcp)"

CASES = [
    ("lisbon_generic", "Events in Lisbon", None),
    ("lisbon_concerts_june", "Concerts in Lisbon June 2026", None),
    ("paris_bts_july", "BTS tour Paris July 2026", None),
]


def _call(api_key: str, q: str, htichips: str | None) -> tuple[dict, float]:
    params = {
        "engine": "google_events",
        "q": q,
        "hl": "en",
        "api_key": api_key,
    }
    if htichips:
        params["htichips"] = htichips
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


def _summarize_event(e: dict) -> str:
    title = e.get("title", "?")
    when = (e.get("date") or {}).get("when", "?")
    venue = ((e.get("venue") or {}).get("name")) or "?"
    link = e.get("link", "?")
    return f"    title={title!r}\n      when={when!r}\n      venue={venue!r}\n      link={link[:70]}..."


def main() -> int:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("ERROR: SERPAPI_KEY not set in env.")
        return 2

    print("=" * 70)
    print("Phase 0 verification: SerpAPI google_events engine")
    print("=" * 70)

    results: dict[str, tuple[dict, float]] = {}
    for label, q, htichips in CASES:
        print(f"\n[{label}] q={q!r} htichips={htichips!r}")
        try:
            body, elapsed = _call(api_key, q, htichips)
        except httpx.HTTPStatusError as e:
            print(f"  FAILED: {e.response.status_code} {e.response.text[:200]}")
            return 1
        path = Path(f"tests/fixtures/serpapi_events_{label}.json")
        _save(path, body)
        events = body.get("events_results", [])
        print(f"  → {path}  ({elapsed:.2f}s, {len(events)} events)")
        results[label] = (body, elapsed)

    # ---- Q3: field parity ---------------------------------------------------
    print("\n" + "=" * 70)
    print("Q3: field parity across the 3 fixtures")
    print("=" * 70)
    for label, (body, _) in results.items():
        events = body.get("events_results", [])
        if not events:
            print(f"  {label}: no events_results")
            continue
        e0 = events[0]
        print(f"\n  {label} — top-level keys on events[0]:")
        print(f"    {sorted(e0.keys())}")

    # ---- Q3 sample ---------------------------------------------------------
    print("\n" + "=" * 70)
    print("Sample of first event per fixture")
    print("=" * 70)
    for label, (body, _) in results.items():
        print(f"\n  {label}:")
        events = body.get("events_results", [])
        if events:
            print(_summarize_event(events[0]))

    # ---- Q4: link target distribution --------------------------------------
    print("\n" + "=" * 70)
    print("Q4: where do `link` values point (host distribution across all events)")
    print("=" * 70)
    from collections import Counter
    from urllib.parse import urlparse
    for label, (body, _) in results.items():
        events = body.get("events_results", [])
        hosts = Counter()
        for e in events:
            link = e.get("link") or ""
            host = urlparse(link).netloc
            if host:
                hosts[host] += 1
        print(f"\n  {label}:  {dict(hosts.most_common(8))}")

    # ---- htichips probe ----------------------------------------------------
    print("\n" + "=" * 70)
    print("Q1 (BONUS): probing htichips=date:next_week for events in Lisbon")
    print("=" * 70)
    try:
        body, elapsed = _call(api_key, "Events in Lisbon", "date:next_week")
    except httpx.HTTPStatusError as e:
        print(f"  htichips probe FAILED: {e.response.status_code} {e.response.text[:200]}")
    else:
        events = body.get("events_results", [])
        path = Path("tests/fixtures/serpapi_events_lisbon_next_week.json")
        _save(path, body)
        print(f"  → {path}  ({elapsed:.2f}s, {len(events)} events)")
        if events:
            print(f"  first when: {(events[0].get('date') or {}).get('when')!r}")

    print("\n" + "=" * 70)
    print("DONE.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
