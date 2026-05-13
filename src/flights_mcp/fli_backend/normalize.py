"""Translate fli's FlightResult objects into our clean tool output shape.

Pure functions, no I/O. Called by `client.py` after each upstream call.

fli's data model is friendly:
- airline IATA codes are the enum NAMES (`Airline.FI`, `Airline.AY`)
- airport IATA codes are the enum NAMES too
- datetimes are timezone-naive (local airport time)
- duration is in minutes (int)
- legs are flat lists; round-trip arrives as a tuple of (outbound, inbound)
  FlightResults

That means most fields drop straight in, with formatting touch-ups for ISO
8601 datetimes/durations and the booking URL.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import quote_plus

from fli.models import FlightLeg, FlightResult
from fli.search import DatePrice

from flights_mcp.models import (
    CabinClass,
    DatePriceOffer,
    FlightOffer,
    Itinerary,
    Segment,
)


def booking_url_for(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None,
) -> str:
    """Pre-fill a Google Flights search URL from the user's query inputs.

    Identical for all offers from one search — depends only on inputs. fli
    doesn't expose a per-offer booking page; we link to the search page.
    """
    base = "https://www.google.com/travel/flights?q="
    if return_date:
        q = (
            f"Flights from {origin} to {destination} "
            f"on {departure_date} through {return_date}"
        )
    else:
        q = f"Flights from {origin} to {destination} on {departure_date}"
    return base + quote_plus(q)


def _iso_duration(minutes: int) -> str:
    """Convert an integer minute count to ISO 8601 duration ('PT3H40M')."""
    minutes = max(0, minutes)
    hours, mins = divmod(minutes, 60)
    parts = ["PT"]
    if hours:
        parts.append(f"{hours}H")
    if mins or not hours:
        parts.append(f"{mins}M")
    return "".join(parts)


def _flight_number(leg: FlightLeg) -> str:
    """Combine IATA airline code with fli's numeric-only flight number.

    fli returns the digits ('343'), not the airline-prefixed string ('FI 343').
    """
    return f"{leg.airline.name}{leg.flight_number}"


def _to_segment(leg: FlightLeg, cabin: CabinClass) -> Segment:
    return Segment(
        airline=leg.airline.name,
        flight_number=_flight_number(leg),
        departure_airport=leg.departure_airport.name,
        departure_time_local=leg.departure_datetime.isoformat(),
        arrival_airport=leg.arrival_airport.name,
        arrival_time_local=leg.arrival_datetime.isoformat(),
        cabin=cabin,
        booking_class="",
    )


def _to_itinerary(result: FlightResult, cabin: CabinClass) -> Itinerary:
    segments = [_to_segment(leg, cabin) for leg in result.legs]
    return Itinerary(
        duration=_iso_duration(result.duration),
        stops=result.stops,
        segments=segments,
    )


def _dedupe_airlines(*itineraries: Itinerary | None) -> list[str]:
    return list(dict.fromkeys(
        seg.airline
        for it in itineraries if it is not None
        for seg in it.segments
    ))


def _compute_offer_id(
    *,
    airlines: list[str],
    segments: list[tuple[str, str]],
    departure_date: str,
    return_date: str | None,
) -> str:
    """Stable identifier scoped to a query result set.

    fli doesn't issue a booking_token like SerpAPI did. The hash is
    deterministic per query input, so cache invalidation works correctly,
    but the value isn't globally meaningful — only useful inside a single
    search response for cross-referencing.

    Inputs:
      - sorted airline IATA codes (set-like; ordering doesn't matter)
      - ordered list of (flight_number, departure_time_local) tuples across
        outbound segments then inbound segments. ORDER MATTERS — two
        itineraries with identical flight numbers but different segment
        timing (e.g. same-day connection vs overnight layover) must hash
        differently, which the previous flight-number-only hash failed to
        guarantee.
      - departure_date
      - return_date (empty string for one-way so it still gets canonicalized)
    """
    canonical = json.dumps(
        {
            "airlines": sorted(airlines),
            "segments": [list(t) for t in segments],  # JSON has no native tuples
            "departure_date": departure_date,
            "return_date": return_date or "",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _to_offer(
    raw_entry: FlightResult | tuple[FlightResult, ...],
    *,
    cabin: CabinClass,
    adults: int,
    booking_url: str,
    departure_date: str,
    return_date: str | None,
) -> FlightOffer:
    if isinstance(raw_entry, tuple):
        outbound_raw = raw_entry[0]
        inbound_raw = raw_entry[1] if len(raw_entry) > 1 else None
    else:
        outbound_raw = raw_entry
        inbound_raw = None

    outbound = _to_itinerary(outbound_raw, cabin)
    inbound = _to_itinerary(inbound_raw, cabin) if inbound_raw else None

    # Currency comes from fli — they parse it from the upstream response
    # (PR #78). Default to USD if fli ever returns None on a malformed result;
    # the regex on IsoCurrency would otherwise reject the offer.
    currency = (outbound_raw.currency or "USD").upper()

    # Price: fli returns the total for the passenger count we requested. Derive
    # per-adult by dividing (children/infants aren't broken out separately).
    total = float(outbound_raw.price)
    per_adult = total / max(1, adults)

    airlines = _dedupe_airlines(outbound, inbound)
    # Build (flight_number, departure_time_local) tuples in itinerary order:
    # outbound legs first, then inbound legs. Order is meaningful for the
    # offer_id hash — two itineraries with identical flight numbers but
    # different segment timing (e.g. same-day connection vs overnight stay)
    # must hash to distinct offer_ids.
    segment_id_inputs: list[tuple[str, str]] = []
    for seg in outbound.segments:
        segment_id_inputs.append((seg.flight_number, seg.departure_time_local))
    if inbound is not None:
        for seg in inbound.segments:
            segment_id_inputs.append((seg.flight_number, seg.departure_time_local))

    return FlightOffer(
        offer_id=_compute_offer_id(
            airlines=airlines,
            segments=segment_id_inputs,
            departure_date=departure_date,
            return_date=return_date,
        ),
        total_price=total,
        currency=currency,
        price_per_adult=per_adult,
        airlines=airlines,
        validating_airline=outbound.segments[0].airline,
        outbound=outbound,
        inbound=inbound,
        seats_available=None,        # fli doesn't expose this
        last_ticketing_date=None,    # fli doesn't expose this
        fare_basis="",               # fli doesn't expose this
        baggage_allowance=None,      # fli doesn't expose this
        booking_url=booking_url,
    )


def _parse_window(window: str | None) -> tuple[int, int] | None:
    """'6-20' → (6, 20). None → None. Validation already happened at the model."""
    if not window:
        return None
    s, e = window.split("-", 1)
    return int(s), int(e)


def _inbound_hour_within(offer: FlightOffer, window: tuple[int, int]) -> bool:
    """True if the offer's inbound first-segment departure hour is in the window.

    Semantics: inclusive of start, EXCLUSIVE of end. window=(8, 20) admits
    hours 8 through 19 (08:00 through 19:59 local). A 20:00 or 20:30
    departure does NOT match. This matches how humans typically read
    "between 8am and 8pm" — "8pm" is the cutoff, not part of the range.

    One-way offers (inbound=None) trivially pass — no inbound leg to filter.
    """
    if offer.inbound is None:
        return True
    first_inbound = offer.inbound.segments[0]
    # Time format is ISO 8601 with no offset: "2026-05-29T20:30:00"
    # Hour is characters 11-13.
    hour = int(first_inbound.departure_time_local[11:13])
    return window[0] <= hour < window[1]


def build_offers(
    raw_entries: list,
    *,
    cabin: CabinClass,
    adults: int,
    booking_url: str,
    departure_date: str,
    return_date: str | None,
    limit: int,
    inbound_window: str | None = None,
) -> list[FlightOffer]:
    """Translate fli results into the clean FlightOffer shape.

    fli's `top_n` argument is a soft suggestion the upstream sometimes ignores
    (29 results were observed when 5 were requested), so we cap at `limit`
    here. When `inbound_window` is set, we filter before counting toward the
    limit — so a tight window doesn't silently shrink the result list because
    of unrelated truncation.

    Order is preserved from upstream (fli's SortBy.BEST ranking).
    """
    window = _parse_window(inbound_window)
    offers: list[FlightOffer] = []
    for entry in raw_entries:
        if len(offers) >= limit:
            break
        offer = _to_offer(
            entry,
            cabin=cabin,
            adults=adults,
            booking_url=booking_url,
            departure_date=departure_date,
            return_date=return_date,
        )
        if window is not None and not _inbound_hour_within(offer, window):
            continue
        offers.append(offer)
    return offers


def build_date_offers(
    entries: list[DatePrice],
    *,
    currency_fallback: str = "USD",
) -> list[DatePriceOffer]:
    """Translate fli's DatePrice entries into DatePriceOffer.

    fli's `DatePrice.date` is a tuple — 1-element for one-way (just the
    departure datetime) or 2-element for round-trip (departure, return).
    Both datetimes are naive (midnight, no tz). We surface them as ISO
    YYYY-MM-DD strings consistent with the rest of the date contract.
    """
    out: list[DatePriceOffer] = []
    for entry in entries:
        if not entry.date:
            continue
        departure = entry.date[0].date().isoformat()
        return_ = entry.date[1].date().isoformat() if len(entry.date) > 1 else None
        out.append(DatePriceOffer(
            departure_date=departure,
            return_date=return_,
            price=float(entry.price),
            currency=(entry.currency or currency_fallback).upper(),
        ))
    return out
