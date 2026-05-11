from flights_mcp.amadeus.normalize import _baggage_summary, _coerce_cabin, normalize_offers
from flights_mcp.models import AmadeusFareDetail, AmadeusSearchResponse, CabinClass


def _fare(**bag) -> AmadeusFareDetail:
    return AmadeusFareDetail.model_validate({
        "segmentId": "1",
        "cabin": "ECONOMY",
        "fareBasis": "VLOWFI",
        "class": "V",
        "includedCheckedBags": bag or None,
    })


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


def test_baggage_summary_quantity_zero():
    assert _baggage_summary(_fare(quantity=0)) == "no checked bag"


def test_baggage_summary_quantity_singular():
    assert _baggage_summary(_fare(quantity=1)) == "1 checked bag"


def test_baggage_summary_quantity_plural():
    assert _baggage_summary(_fare(quantity=2)) == "2 checked bags"


def test_baggage_summary_weight_with_unit():
    assert _baggage_summary(_fare(weight=23, weightUnit="KG")) == "23KG checked baggage"


def test_baggage_summary_weight_without_unit():
    assert _baggage_summary(_fare(weight=23)) == "23 checked baggage"


def test_baggage_summary_missing_fare():
    assert _baggage_summary(None) is None


def test_baggage_summary_missing_bags():
    assert _baggage_summary(_fare()) is None


def test_coerce_cabin_known_enum_value():
    assert _coerce_cabin("BUSINESS") is CabinClass.BUSINESS


def test_coerce_cabin_falls_back_on_unknown_string():
    # Real-world drift: a carrier returning "COACH" must not crash the response.
    assert _coerce_cabin("COACH") is CabinClass.ECONOMY


def test_coerce_cabin_handles_space_separated_label():
    assert _coerce_cabin("PREMIUM ECONOMY") is CabinClass.PREMIUM_ECONOMY


def test_coerce_cabin_handles_truly_unknown_value():
    assert _coerce_cabin("MYSTERY_CLASS") is CabinClass.ECONOMY


def test_coerce_cabin_handles_none():
    assert _coerce_cabin(None) is CabinClass.ECONOMY
