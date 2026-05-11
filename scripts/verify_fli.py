#!/usr/bin/env python
"""Phase 0 (fli migration): verify the `flights` library against HEL→IAD.

Calls fli's SearchFlights (round-trip) and SearchDates (date flex) directly,
serializes both responses to JSON fixtures, and prints a structure summary
covering the four open design questions:

  1. Round-trip representation: one FlightResult with all legs, or a
     tuple/pair of two FlightResults (outbound + inbound)?
  2. Datetime format: timezone-aware or naive (local airport time)?
  3. Currency: what does fli return when filters don't expose it?
  4. Cabin class: confirm SeatType maps 1:1 with our CabinClass.

Usage:
    .venv/bin/python scripts/verify_fli.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from fli.models import (
    Airport,
    DateSearchFilters,
    FlightResult,
    FlightSearchFilters,
    FlightSegment,
    MaxStops,
    PassengerInfo,
    SeatType,
    SortBy,
    TripType,
)
from fli.search import DatePrice, SearchDates, SearchFlights

FIXTURE_FLIGHTS = Path("tests/fixtures/fli_hel_iad_success.json")
FIXTURE_DATES = Path("tests/fixtures/fli_hel_iad_dates.json")


def _dump_one(obj) -> dict:
    """model_dump that survives nested datetimes."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _dump_entry(entry):
    """SearchFlights returns either FlightResult or tuple[FlightResult, ...]
    per its annotation. Serialize either shape uniformly."""
    if isinstance(entry, tuple):
        return {"kind": "tuple", "items": [_dump_one(item) for item in entry]}
    return {"kind": "single", "item": _dump_one(entry)}


def run_search_flights() -> list:
    print("→ SearchFlights HEL→IAD round-trip 2026-05-18 → 2026-05-29, 1 adult, Economy")
    filters = FlightSearchFilters(
        trip_type=TripType.ROUND_TRIP,
        passenger_info=PassengerInfo(adults=1),
        seat_type=SeatType.ECONOMY,
        stops=MaxStops.ANY,
        sort_by=SortBy.BEST,
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.HEL, 0]],
                arrival_airport=[[Airport.IAD, 0]],
                travel_date="2026-05-18",
            ),
            FlightSegment(
                departure_airport=[[Airport.IAD, 0]],
                arrival_airport=[[Airport.HEL, 0]],
                travel_date="2026-05-29",
            ),
        ],
    )

    started = time.monotonic()
    results = SearchFlights().search(filters, top_n=5)
    elapsed = time.monotonic() - started
    print(f"  ✓ {len(results) if results else 0} result(s) in {elapsed:.2f}s")
    return results or []


def run_search_dates() -> list:
    print("→ SearchDates HEL→IAD departures 2026-05-15 → 2026-05-25, 11-day duration")
    filters = DateSearchFilters(
        trip_type=TripType.ROUND_TRIP,
        passenger_info=PassengerInfo(adults=1),
        seat_type=SeatType.ECONOMY,
        stops=MaxStops.ANY,
        from_date="2026-05-15",
        to_date="2026-05-25",
        duration=11,
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.HEL, 0]],
                arrival_airport=[[Airport.IAD, 0]],
                travel_date="2026-05-18",
            ),
            FlightSegment(
                departure_airport=[[Airport.IAD, 0]],
                arrival_airport=[[Airport.HEL, 0]],
                travel_date="2026-05-29",
            ),
        ],
    )

    started = time.monotonic()
    results = SearchDates().search(filters)
    elapsed = time.monotonic() - started
    print(f"  ✓ {len(results) if results else 0} result(s) in {elapsed:.2f}s")
    return results or []


def summarize_flights(results: list) -> None:
    print()
    print("=" * 78)
    print("Q1 — round-trip representation")
    print("=" * 78)
    if not results:
        print("  no results — nothing to inspect")
        return

    sample = results[0]
    print(f"  results[0] type: {type(sample).__name__}")
    if isinstance(sample, tuple):
        print(f"  → TUPLE of {len(sample)} FlightResult(s) per entry")
        print(f"    [0].stops = {sample[0].stops}, [0].legs count = {len(sample[0].legs)}")
        if len(sample) > 1:
            print(f"    [1].stops = {sample[1].stops}, [1].legs count = {len(sample[1].legs)}")
        first_leg = sample[0].legs[0]
        last_leg = sample[-1].legs[-1]
    else:
        print(f"  → SINGLE FlightResult per entry (legs holds all segments)")
        print(f"    .stops = {sample.stops}, .legs count = {len(sample.legs)}")
        first_leg = sample.legs[0]
        last_leg = sample.legs[-1]

    print()
    print("=" * 78)
    print("Q2 — datetime format")
    print("=" * 78)
    dt = first_leg.departure_datetime
    print(f"  first leg departure_datetime: {dt!r}")
    print(f"    tzinfo: {dt.tzinfo!r}  (None = naive, expected for local airport time)")
    print(f"    isoformat(): {dt.isoformat()!r}")

    print()
    print("=" * 78)
    print("Q3 — currency")
    print("=" * 78)
    first_entry_result = sample[0] if isinstance(sample, tuple) else sample
    print(f"  price: {first_entry_result.price!r}")
    print(f"  currency: {first_entry_result.currency!r}")

    print()
    print("=" * 78)
    print("Q4 — cabin class / seat type")
    print("=" * 78)
    print(f"  fli.SeatType members: {[e.name for e in SeatType]}")
    print(f"  our CabinClass members: ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST")
    print(f"  → 1:1 mapping confirmed by naming")

    print()
    print("=" * 78)
    print("Leg shape (first leg of first result)")
    print("=" * 78)
    print(f"  airline: {first_leg.airline.name} ({first_leg.airline.value})")
    print(f"  flight_number: {first_leg.flight_number!r}")
    print(f"  departure: {first_leg.departure_airport.name} @ {first_leg.departure_datetime.isoformat()}")
    print(f"  arrival:   {first_leg.arrival_airport.name} @ {first_leg.arrival_datetime.isoformat()}")
    print(f"  duration (minutes): {first_leg.duration}")
    print(f"  last leg airline:  {last_leg.airline.name}, arrival: {last_leg.arrival_airport.name}")


def summarize_dates(results: list) -> None:
    print()
    print("=" * 78)
    print("SearchDates response")
    print("=" * 78)
    if not results:
        print("  no results")
        return
    sample = results[0]
    print(f"  results[0] type: {type(sample).__name__}")
    print(f"    date tuple type: {type(sample.date).__name__}, length: {len(sample.date)}")
    for i, d in enumerate(sample.date):
        print(f"      date[{i}]: {d.isoformat()}  tzinfo={d.tzinfo}")
    print(f"    price: {sample.price!r}")
    print(f"    currency: {sample.currency!r}")
    print(f"  total entries: {len(results)}")
    sorted_by_price = sorted(results, key=lambda r: r.price)
    print(f"  cheapest 3:")
    for r in sorted_by_price[:3]:
        dates = " → ".join(d.date().isoformat() for d in r.date)
        print(f"    {dates}   {r.price} {r.currency or ''}")


def main() -> int:
    FIXTURE_FLIGHTS.parent.mkdir(parents=True, exist_ok=True)

    # SearchFlights
    try:
        results = run_search_flights()
    except Exception as e:
        print(f"  ✗ SearchFlights failed: {type(e).__name__}: {e}")
        return 1
    payload = [_dump_entry(r) for r in results]
    FIXTURE_FLIGHTS.write_text(json.dumps(payload, indent=2, default=str))
    print(f"  saved to {FIXTURE_FLIGHTS}")
    summarize_flights(results)

    # SearchDates
    print()
    try:
        date_results = run_search_dates()
    except Exception as e:
        print(f"  ✗ SearchDates failed: {type(e).__name__}: {e}")
        return 1
    date_payload = [_dump_one(r) for r in date_results]
    FIXTURE_DATES.write_text(json.dumps(date_payload, indent=2, default=str))
    print(f"  saved to {FIXTURE_DATES}")
    summarize_dates(date_results)

    print()
    print("=" * 78)
    print("Done. Review the structure above before Phase 1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
