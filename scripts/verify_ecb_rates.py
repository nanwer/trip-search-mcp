#!/usr/bin/env python
"""Phase 0 (Track B, currency conversion): verify the ECB daily XML feed.

The European Central Bank publishes daily reference rates against EUR
at https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml.
No API key, no rate limit, no signup.

This script does ONE live HTTP GET, saves the raw response to
`tests/fixtures/ecb_eurofxref_daily.xml`, and prints a structured
summary covering Phase 0's questions:

  1. Coverage — does ECB carry the 19 currencies we'd realistically hit?
  2. Update frequency — what does the `time` attribute say?
  3. Precision — how many decimal places in the rate strings?

Usage:
    .venv/bin/python scripts/verify_ecb_rates.py
"""
from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

FIXTURE = Path("tests/fixtures/ecb_eurofxref_daily.xml")
URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
USER_AGENT = "trip-search-mcp/0.2 (https://github.com/nanwer/trip-search-mcp)"

# 19 currencies the spec calls out as "realistic to hit".
CHECK_CURRENCIES = [
    "USD", "EUR", "JPY", "GBP", "CAD", "AUD", "CHF",
    "SEK", "NOK", "DKK", "INR", "MXN", "BRL", "SGD",
    "KRW", "CNY", "THB", "HKD", "NZD",
]

# ECB's XML uses two namespaces. The actual rate Cubes are under the
# eurofxref one; the outer envelope is gesmes. We parse positionally
# rather than wrestle with full XPath namespacing.
NS = {
    "gesmes": "http://www.gesmes.org/xml/2002-08-01",
    "exr": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref",
}


def main() -> int:
    print("=" * 70)
    print("Phase 0 verification: ECB daily reference rates")
    print("=" * 70)

    started = time.monotonic()
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(URL, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as e:
        print(f"  FAILED: {e}")
        return 1
    elapsed = time.monotonic() - started

    if response.status_code != 200:
        print(f"  FAILED: HTTP {response.status_code}")
        return 1

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_bytes(response.content)
    print(f"  → {FIXTURE}  ({elapsed:.2f}s wall-clock, {len(response.content)} bytes)")

    # Parse and inspect.
    root = ET.fromstring(response.content)

    # The inner Cube has the date as an attribute.
    inner_cubes = root.findall(".//exr:Cube[@time]", NS)
    if not inner_cubes:
        print("  FAILED: no inner Cube with @time found — XML schema may have changed.")
        return 1
    inner = inner_cubes[0]
    rate_date = inner.get("time")
    rate_cubes = inner.findall("exr:Cube", NS)

    print(f"\n--- Q2: Update timestamp ---")
    print(f"  ECB time attribute: {rate_date!r}")
    print(f"  Total currencies in feed: {len(rate_cubes)}")

    rates: dict[str, str] = {}
    for c in rate_cubes:
        cur = c.get("currency")
        rate = c.get("rate")
        if cur and rate:
            rates[cur] = rate

    print(f"\n--- Q1: Coverage of the 19 'realistic' currencies ---")
    missing = [cur for cur in CHECK_CURRENCIES if cur != "EUR" and cur not in rates]
    print(f"  Missing: {missing or '(none — all 18 non-EUR covered)'}")
    for cur in CHECK_CURRENCIES:
        if cur == "EUR":
            print(f"  EUR: (base — always 1.000)")
        else:
            print(f"  {cur}: {rates.get(cur, '!! MISSING !!')}")

    print(f"\n--- Q3: Precision (decimal places per rate) ---")
    sample = ["USD", "JPY", "GBP", "CHF", "BRL"]
    for cur in sample:
        if cur in rates:
            rate_str = rates[cur]
            decimals = len(rate_str.rsplit(".", 1)[1]) if "." in rate_str else 0
            print(f"  {cur} → {rate_str!r} ({decimals} decimal places)")

    print(f"\n--- Bonus: full currency list ---")
    print(f"  {', '.join(sorted(rates.keys()))}")

    print(f"\n" + "=" * 70)
    print(f"DONE. Latency: {elapsed:.2f}s.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
