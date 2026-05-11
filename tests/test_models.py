from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from flights_mcp.models import (
    AmadeusSearchResponse,
    CabinClass,
    FlightOffer,
    Itinerary,
    SearchFlightsInput,
    SearchFlightsResult,
    Segment,
)

# Match the validator's UTC frame so tests can't flake at midnight UTC on UTC-offset hosts.
TODAY = datetime.now(tz=timezone.utc).date()
TOMORROW = TODAY + timedelta(days=1)
NEXT_WEEK = TODAY + timedelta(days=7)


def test_accepts_valid_round_trip():
    m = SearchFlightsInput(
        origin="HEL",
        destination="IAD",
        departure_date=TOMORROW.isoformat(),
        return_date=NEXT_WEEK.isoformat(),
        adults=2,
    )
    assert m.origin == "HEL"
    assert m.cabin_class is CabinClass.ECONOMY
    assert m.currency == "USD"
    assert m.max_results == 20


def test_rejects_lowercase_iata():
    with pytest.raises(ValidationError):
        SearchFlightsInput(origin="hel", destination="IAD", departure_date=TOMORROW.isoformat())


def test_rejects_wrong_length_iata():
    with pytest.raises(ValidationError):
        SearchFlightsInput(origin="HELS", destination="IAD", departure_date=TOMORROW.isoformat())


def test_rejects_digits_in_iata():
    with pytest.raises(ValidationError):
        SearchFlightsInput(origin="H1L", destination="IAD", departure_date=TOMORROW.isoformat())


def test_rejects_past_departure_date():
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        SearchFlightsInput(origin="HEL", destination="IAD", departure_date=yesterday)


def test_rejects_return_before_departure():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL",
            destination="IAD",
            departure_date=NEXT_WEEK.isoformat(),
            return_date=TOMORROW.isoformat(),
        )


def test_rejects_infants_exceeding_adults():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL",
            destination="IAD",
            departure_date=TOMORROW.isoformat(),
            adults=1,
            infants=2,
        )


def test_rejects_total_travelers_above_amadeus_limit():
    # adults+children+infants must fit within Amadeus's 9-passenger search cap.
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL",
            destination="IAD",
            departure_date=TOMORROW.isoformat(),
            adults=5,
            children=4,
            infants=1,
        )


def test_rejects_max_results_above_50():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD", departure_date=TOMORROW.isoformat(), max_results=51
        )


def test_cabin_class_enum():
    m = SearchFlightsInput(
        origin="HEL",
        destination="IAD",
        departure_date=TOMORROW.isoformat(),
        cabin_class="BUSINESS",
    )
    assert m.cabin_class is CabinClass.BUSINESS


# ---------------------------------------------------------------------------
# Task 5: output models
# ---------------------------------------------------------------------------


def _make_segment(**overrides):
    base = dict(
        airline="AY",
        flight_number="AY15",
        departure_airport="HEL",
        departure_time_local="2026-05-18T15:30:00",
        arrival_airport="JFK",
        arrival_time_local="2026-05-18T17:45:00",
        cabin="ECONOMY",
        booking_class="V",
    )
    base.update(overrides)
    return Segment(**base)


def test_segment_round_trips():
    s = _make_segment()
    assert s.airline == "AY"
    assert s.departure_time_local == "2026-05-18T15:30:00"


def test_itinerary_holds_segments():
    it = Itinerary(duration="PT10H30M", stops=1, segments=[_make_segment(), _make_segment(flight_number="AY99")])
    assert it.stops == 1
    assert len(it.segments) == 2


def test_flight_offer_round_trip_shape():
    it = Itinerary(duration="PT10H30M", stops=0, segments=[_make_segment()])
    offer = FlightOffer(
        offer_id="1",
        total_price=850.50,
        currency="USD",
        price_per_adult=850.50,
        airlines=["AY"],
        validating_airline="AY",
        outbound=it,
        inbound=None,
        seats_available=7,
        last_ticketing_date="2026-05-15",
        fare_basis="VLOWFI",
        baggage_allowance="1 checked bag",
    )
    assert offer.inbound is None
    assert offer.baggage_allowance == "1 checked bag"


def test_flight_offer_allows_null_optional_fields():
    it = Itinerary(duration="PT10H30M", stops=0, segments=[_make_segment()])
    offer = FlightOffer(
        offer_id="1",
        total_price=850.50,
        currency="USD",
        price_per_adult=850.50,
        airlines=["AY"],
        validating_airline="AY",
        outbound=it,
        inbound=None,
        seats_available=None,
        last_ticketing_date=None,
        fare_basis="VLOWFI",
        baggage_allowance=None,
    )
    assert offer.seats_available is None
    assert offer.last_ticketing_date is None
    assert offer.baggage_allowance is None


def test_search_flights_result_wraps_offers():
    it = Itinerary(duration="PT10H30M", stops=0, segments=[_make_segment()])
    offer = FlightOffer(
        offer_id="1", total_price=850.5, currency="USD", price_per_adult=850.5,
        airlines=["AY"], validating_airline="AY", outbound=it, inbound=None,
        seats_available=None, last_ticketing_date=None, fare_basis="V", baggage_allowance=None,
    )
    result = SearchFlightsResult(results=[offer])
    assert len(result.results) == 1


def test_segment_rejects_overlong_airline_code():
    with pytest.raises(ValidationError):
        _make_segment(airline="AYZZZ")


def test_itinerary_rejects_negative_stops():
    with pytest.raises(ValidationError):
        Itinerary(duration="PT10H30M", stops=-1, segments=[_make_segment()])


def test_itinerary_rejects_empty_segments():
    with pytest.raises(ValidationError):
        Itinerary(duration="PT10H30M", stops=0, segments=[])


# ---------------------------------------------------------------------------
# Task 6: raw Amadeus response models
# ---------------------------------------------------------------------------


def test_amadeus_search_response_parses_minimal_payload():
    payload = {
        "meta": {"count": 1},
        "data": [{
            "type": "flight-offer",
            "id": "1",
            "source": "GDS",
            "lastTicketingDate": "2026-05-15",
            "numberOfBookableSeats": 7,
            "itineraries": [{
                "duration": "PT10H30M",
                "segments": [{
                    "id": "1",
                    "carrierCode": "AY",
                    "number": "15",
                    "departure": {"iataCode": "HEL", "at": "2026-05-18T15:30:00"},
                    "arrival": {"iataCode": "JFK", "at": "2026-05-18T17:45:00"},
                    "numberOfStops": 0,
                }],
            }],
            "price": {"currency": "USD", "total": "850.50", "base": "700.00"},
            "validatingAirlineCodes": ["AY"],
            "travelerPricings": [{
                "travelerId": "1",
                "fareOption": "STANDARD",
                "travelerType": "ADULT",
                "price": {"currency": "USD", "total": "850.50", "base": "700.00"},
                "fareDetailsBySegment": [{
                    "segmentId": "1",
                    "cabin": "ECONOMY",
                    "fareBasis": "VLOWFI",
                    "class": "V",
                }],
            }],
        }],
    }
    parsed = AmadeusSearchResponse.model_validate(payload)
    assert parsed.meta.count == 1
    assert parsed.data[0].id == "1"
    assert parsed.data[0].itineraries[0].segments[0].carrier_code == "AY"
    assert parsed.data[0].price.total == "850.50"
