"""Translate verbose Amadeus offers into the clean tool output shape."""
from __future__ import annotations

from flights_mcp.models import (
    AmadeusFareDetail,
    AmadeusFlightOfferRaw,
    AmadeusItinerary,
    AmadeusSearchResponse,
    CabinClass,
    FlightOffer,
    Itinerary,
    Segment,
)

# Carrier-specific cabin labels that don't match our enum directly.
_CABIN_ALIASES = {
    "COACH": CabinClass.ECONOMY,
    "PREMIUM ECONOMY": CabinClass.PREMIUM_ECONOMY,
    "BUSINESS_CLASS": CabinClass.BUSINESS,
    "FIRST_CLASS": CabinClass.FIRST,
}


def _coerce_cabin(raw_cabin: str | None) -> CabinClass:
    """Map an Amadeus cabin string onto our enum, falling back to ECONOMY.

    The normalizer's contract is "never raise"; without this guard, a carrier
    returning an unmodelled cabin label would crash the entire response.
    """
    if raw_cabin is None:
        return CabinClass.ECONOMY
    key = raw_cabin.upper().strip()
    if key in _CABIN_ALIASES:
        return _CABIN_ALIASES[key]
    try:
        return CabinClass(key)
    except ValueError:
        return CabinClass.ECONOMY


def _baggage_summary(detail: AmadeusFareDetail | None) -> str | None:
    if detail is None or detail.included_checked_bags is None:
        return None
    bag = detail.included_checked_bags
    qty = bag.get("quantity")
    if qty is not None:
        if qty == 0:
            return "no checked bag"
        return f"{qty} checked bag" if qty == 1 else f"{qty} checked bags"
    weight = bag.get("weight")
    unit = bag.get("weightUnit") or ""
    if weight is not None:
        return f"{weight}{unit} checked baggage".strip()
    return None


def _normalize_itinerary(it: AmadeusItinerary, fares_by_segment_id: dict[str, AmadeusFareDetail]) -> Itinerary:
    segments: list[Segment] = []
    for seg in it.segments:
        fare = fares_by_segment_id.get(seg.id)
        booking_class = fare.class_ if fare else ""
        segments.append(Segment(
            airline=seg.carrier_code,
            flight_number=f"{seg.carrier_code}{seg.number}",
            departure_airport=seg.departure.iata_code,
            departure_time_local=seg.departure.at,
            arrival_airport=seg.arrival.iata_code,
            arrival_time_local=seg.arrival.at,
            cabin=_coerce_cabin(fare.cabin if fare else None),
            booking_class=booking_class,
        ))
    stops = max(0, len(it.segments) - 1)
    return Itinerary(duration=it.duration, stops=stops, segments=segments)


def _normalize_offer(raw: AmadeusFlightOfferRaw) -> FlightOffer:
    # Build a segmentId -> fareDetail map from the first traveler pricing.
    # Phase 1 keeps it simple: per-traveler fare details are assumed homogeneous.
    fares_by_segment_id: dict[str, AmadeusFareDetail] = {}
    if raw.traveler_pricings:
        for fd in raw.traveler_pricings[0].fare_details_by_segment:
            fares_by_segment_id[fd.segment_id] = fd

    outbound = _normalize_itinerary(raw.itineraries[0], fares_by_segment_id)
    inbound = (
        _normalize_itinerary(raw.itineraries[1], fares_by_segment_id)
        if len(raw.itineraries) > 1
        else None
    )

    # Operating carriers across all segments, preserving order, deduplicated.
    airlines = list(dict.fromkeys(
        seg.carrier_code
        for it in raw.itineraries
        for seg in it.segments
    ))

    total_price = float(raw.price.total)
    price_per_adult = (
        float(raw.traveler_pricings[0].price.total)
        if raw.traveler_pricings
        else total_price
    )

    # Anchor representative fare to the first outbound segment, not dict
    # insertion order — Amadeus doesn't document `fareDetailsBySegment` ordering.
    first_outbound_seg_id = raw.itineraries[0].segments[0].id
    representative_fare = fares_by_segment_id.get(first_outbound_seg_id)
    fare_basis = representative_fare.fare_basis if representative_fare else ""
    baggage = _baggage_summary(representative_fare)

    return FlightOffer(
        offer_id=raw.id,
        total_price=total_price,
        currency=raw.price.currency,
        price_per_adult=price_per_adult,
        airlines=airlines,
        validating_airline=raw.validating_airline_codes[0],
        outbound=outbound,
        inbound=inbound,
        seats_available=raw.number_of_bookable_seats,
        last_ticketing_date=raw.last_ticketing_date,
        fare_basis=fare_basis,
        baggage_allowance=baggage,
    )


def normalize_offers(response: AmadeusSearchResponse) -> list[FlightOffer]:
    return [_normalize_offer(raw) for raw in response.data]
