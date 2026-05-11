from flights_mcp.amadeus.normalize import normalize_offers
from flights_mcp.models import AmadeusSearchResponse


def test_normalize_round_trip_synthetic(synthetic_round_trip):
    raw = AmadeusSearchResponse.model_validate(synthetic_round_trip)
    offers = normalize_offers(raw)

    assert len(offers) == 2

    offer_one_stop = offers[0]
    assert offer_one_stop.offer_id == "1"
    assert offer_one_stop.total_price == 742.18
    assert offer_one_stop.currency == "USD"
    assert offer_one_stop.price_per_adult == 742.18
    assert offer_one_stop.validating_airline == "AY"
    assert set(offer_one_stop.airlines) == {"AY", "AA"}
    assert offer_one_stop.outbound.stops == 1
    assert len(offer_one_stop.outbound.segments) == 2
    assert offer_one_stop.inbound is not None
    assert offer_one_stop.inbound.stops == 1
    assert offer_one_stop.fare_basis == "VLOWFI"
    assert offer_one_stop.baggage_allowance == "1 checked bag"
    assert offer_one_stop.seats_available == 7
    assert offer_one_stop.last_ticketing_date == "2026-05-15"

    first_seg = offer_one_stop.outbound.segments[0]
    assert first_seg.airline == "AY"
    assert first_seg.flight_number == "AY15"
    assert first_seg.departure_airport == "HEL"
    assert first_seg.departure_time_local == "2026-05-18T15:30:00"
    assert first_seg.arrival_airport == "JFK"
    assert first_seg.cabin.value == "ECONOMY"
    assert first_seg.booking_class == "V"

    offer_non_stop = offers[1]
    assert offer_non_stop.outbound.stops == 0
    assert offer_non_stop.baggage_allowance is None  # no includedCheckedBags in fixture


def test_normalize_empty_results(empty_results):
    raw = AmadeusSearchResponse.model_validate(empty_results)
    assert normalize_offers(raw) == []
