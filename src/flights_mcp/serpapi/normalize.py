"""Translate SerpAPI Google Flights options into the clean tool output shape.

Pure functions, no I/O. Called by `client.py` after each upstream HTTP call.
"""
from __future__ import annotations

from flights_mcp.models import (
    CabinClass,
    FlightOffer,
    Itinerary,
    Segment,
)
from flights_mcp.serpapi.raw import SerpFlightOption, SerpSegment

# Carrier-specific cabin labels that don't match our enum directly.
_CABIN_ALIASES = {
    "COACH": CabinClass.ECONOMY,
    "ECONOMY": CabinClass.ECONOMY,
    "PREMIUM ECONOMY": CabinClass.PREMIUM_ECONOMY,
    "PREMIUM_ECONOMY": CabinClass.PREMIUM_ECONOMY,
    "BUSINESS": CabinClass.BUSINESS,
    "BUSINESS_CLASS": CabinClass.BUSINESS,
    "FIRST": CabinClass.FIRST,
    "FIRST_CLASS": CabinClass.FIRST,
}


def _coerce_cabin(raw_cabin: str | None) -> CabinClass:
    """Map a SerpAPI cabin string onto our enum, falling back to ECONOMY."""
    if not raw_cabin:
        return CabinClass.ECONOMY
    key = raw_cabin.upper().strip()
    return _CABIN_ALIASES.get(key, CabinClass.ECONOMY)


def _iso_local_time(serpapi_time: str | None) -> str:
    """Convert SerpAPI's "YYYY-MM-DD HH:MM" into ISO 8601 "YYYY-MM-DDTHH:MM:00".

    Keeps the existing no-offset, local-airport-time contract from the spec.
    Defensive against missing data — returns an empty string if input is None
    (the Pydantic Segment model will reject it downstream and surface a clean
    UPSTREAM_ERROR via the client's normalize-exception guard).
    """
    if not serpapi_time:
        return ""
    # Common cases: "2026-05-18 15:00" or "2026-05-18 15:00:00".
    if "T" in serpapi_time:
        return serpapi_time  # already ISO
    parts = serpapi_time.split(" ", 1)
    if len(parts) != 2:
        return serpapi_time
    d, t = parts
    if len(t) == 5:  # "HH:MM"
        t = f"{t}:00"
    return f"{d}T{t}"


def _iso_duration(total_minutes: int) -> str:
    """Convert an integer minute count to ISO 8601 duration (e.g. 220 -> 'PT3H40M')."""
    if total_minutes < 0:
        total_minutes = 0
    hours, minutes = divmod(total_minutes, 60)
    parts = ["PT"]
    if hours:
        parts.append(f"{hours}H")
    if minutes or not hours:
        parts.append(f"{minutes}M")
    return "".join(parts)


def _split_flight_number(raw: str | None, fallback_airline: str | None) -> tuple[str, str]:
    """Return (iata_airline_code, normalized_flight_number).

    SerpAPI format is typically "FI 343" — the prefix is the airline IATA code
    and the rest is the flight number. If the format doesn't match, fall back
    to the airline name (the IataAirlineCode regex will reject obviously bad
    values; the client's normalize-exception guard then surfaces UPSTREAM_ERROR).
    """
    if raw and " " in raw:
        prefix, suffix = raw.split(" ", 1)
        if prefix.isalnum() and 2 <= len(prefix) <= 3:
            return prefix.upper(), f"{prefix.upper()}{suffix.strip()}"
    # Fallback: try to extract a 2-3 char alphanumeric prefix even without a space.
    if raw:
        for cutoff in (3, 2):
            head = raw[:cutoff]
            if head.isalnum():
                return head.upper(), raw.replace(" ", "")
    # Last resort: use the airline name as-is (will likely fail validation,
    # which is fine — the client converts the error into UPSTREAM_ERROR).
    name = (fallback_airline or "").upper().strip()
    return name, name


def _to_segment(raw: SerpSegment) -> Segment:
    airline, flight_number = _split_flight_number(raw.flight_number, raw.airline)
    return Segment(
        airline=airline,
        flight_number=flight_number,
        departure_airport=raw.departure_airport.id,
        departure_time_local=_iso_local_time(raw.departure_airport.time),
        arrival_airport=raw.arrival_airport.id,
        arrival_time_local=_iso_local_time(raw.arrival_airport.time),
        cabin=_coerce_cabin(raw.travel_class),
        booking_class="",
    )


def _to_itinerary(option: SerpFlightOption) -> Itinerary:
    """Build an Itinerary from a SerpAPI flight option (outbound or return).

    Total duration accounts for in-air time + ground layovers, matching the
    `total_duration` field SerpAPI returns. Falls back to summing segment
    durations + layover durations if `total_duration` is implausible.
    """
    segments = [_to_segment(s) for s in option.flights]
    duration_minutes = option.total_duration
    if duration_minutes <= 0:
        duration_minutes = sum(s.duration for s in option.flights) + sum(l.duration for l in option.layovers)
    stops = max(0, len(option.flights) - 1)
    return Itinerary(
        duration=_iso_duration(duration_minutes),
        stops=stops,
        segments=segments,
    )


def _dedupe_airlines(*itineraries: Itinerary) -> list[str]:
    return list(dict.fromkeys(
        seg.airline for it in itineraries if it is not None for seg in it.segments
    ))


def build_one_way_offers(
    options: list[SerpFlightOption],
    *,
    currency: str,
    adults: int,
    limit: int,
) -> list[FlightOffer]:
    """Translate one-way outbound options into FlightOffers.

    No follow-up call is needed — each option is a complete offer with
    `inbound=None`. The price field is per-search (i.e. for the requested
    passenger count); we derive `price_per_adult` by dividing.
    """
    offers: list[FlightOffer] = []
    for option in options[:limit]:
        outbound = _to_itinerary(option)
        # SerpAPI's price field is the total for the requested passenger count.
        # Derive per-adult by dividing (children/infants exist but SerpAPI
        # doesn't break them out — we accept some imprecision in the per-adult
        # estimate). `adults` is guaranteed >= 1 by the input model.
        total = float(option.price)
        per_adult = total / max(1, adults)
        offers.append(FlightOffer(
            offer_id=option.booking_token or "",
            total_price=total,
            currency=currency,
            price_per_adult=per_adult,
            airlines=_dedupe_airlines(outbound),
            validating_airline=outbound.segments[0].airline,
            outbound=outbound,
            inbound=None,
            seats_available=None,
            last_ticketing_date=None,
            fare_basis="",
            baggage_allowance=None,
        ))
    return offers


def build_round_trip_offer(
    outbound_option: SerpFlightOption,
    return_option: SerpFlightOption,
    *,
    currency: str,
    adults: int,
) -> FlightOffer:
    """Pair one outbound option with one return option into a single FlightOffer.

    The price comes from the return-leg response, which reflects the actual
    combined round-trip total for the chosen outbound (Google Flights /
    SerpAPI convention).
    """
    outbound = _to_itinerary(outbound_option)
    inbound = _to_itinerary(return_option)
    total = float(return_option.price)
    per_adult = total / max(1, adults)
    offer_id = return_option.booking_token or outbound_option.departure_token or ""
    return FlightOffer(
        offer_id=offer_id,
        total_price=total,
        currency=currency,
        price_per_adult=per_adult,
        airlines=_dedupe_airlines(outbound, inbound),
        validating_airline=outbound.segments[0].airline,
        outbound=outbound,
        inbound=inbound,
        seats_available=None,
        last_ticketing_date=None,
        fare_basis="",
        baggage_allowance=None,
    )
