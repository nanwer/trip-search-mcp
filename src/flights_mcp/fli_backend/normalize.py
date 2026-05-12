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
from urllib.parse import quote_plus

from fli.models import FlightLeg, FlightResult

from flights_mcp.models import (
    CabinClass,
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
    flight_numbers: list[str],
    departure_date: str,
    return_date: str | None,
) -> str:
    """Stable identifier scoped to a query result set.

    fli doesn't issue a booking_token like SerpAPI did. The hash is
    deterministic per query input, so cache invalidation works correctly,
    but the value isn't globally meaningful — only useful inside a single
    search response for cross-referencing.
    """
    payload = "|".join([
        ",".join(sorted(airlines)),
        ",".join(sorted(flight_numbers)),
        departure_date,
        return_date or "",
    ])
    return hashlib.sha256(payload.encode()).hexdigest()


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

    all_legs: list[FlightLeg] = list(outbound_raw.legs)
    if inbound_raw:
        all_legs += list(inbound_raw.legs)
    airlines = _dedupe_airlines(outbound, inbound)
    flight_numbers = [_flight_number(leg) for leg in all_legs]

    return FlightOffer(
        offer_id=_compute_offer_id(
            airlines=airlines,
            flight_numbers=flight_numbers,
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


def build_offers(
    raw_entries: list,
    *,
    cabin: CabinClass,
    adults: int,
    booking_url: str,
    departure_date: str,
    return_date: str | None,
    limit: int,
) -> list[FlightOffer]:
    """Translate up to `limit` fli results into the clean FlightOffer shape.

    Post-filtering happens here: fli's `top_n` is a suggestion the upstream
    sometimes ignores (29 results were observed when 5 were requested), so
    we slice to `limit` after the fact. Order is preserved from upstream
    (fli's SortBy.BEST ranking).
    """
    offers: list[FlightOffer] = []
    for entry in raw_entries[:limit]:
        offers.append(_to_offer(
            entry,
            cabin=cabin,
            adults=adults,
            booking_url=booking_url,
            departure_date=departure_date,
            return_date=return_date,
        ))
    return offers
