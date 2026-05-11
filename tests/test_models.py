from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from flights_mcp.models import (
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
    # Round-trip max_results is capped at 5 because each result requires a
    # follow-up call to fetch the return leg — see ROUND_TRIP_MAX_RESULTS.
    m = SearchFlightsInput(
        origin="HEL",
        destination="IAD",
        departure_date=TOMORROW.isoformat(),
        return_date=NEXT_WEEK.isoformat(),
        adults=2,
        max_results=3,
    )
    assert m.origin == "HEL"
    assert m.cabin_class is CabinClass.ECONOMY
    assert m.currency == "USD"
    assert m.max_results == 3


def test_accepts_valid_one_way_with_defaults():
    m = SearchFlightsInput(
        origin="HEL",
        destination="IAD",
        departure_date=TOMORROW.isoformat(),
    )
    assert m.max_results == 20  # one-way keeps the looser default


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
    # adults+children+infants must fit within the provider's 9-passenger search cap.
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


_SAMPLE_BOOKING_URL = "https://www.google.com/travel/flights?q=Flights+from+HEL+to+IAD+on+2026-05-18"


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
        booking_url=_SAMPLE_BOOKING_URL,
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
        booking_url=_SAMPLE_BOOKING_URL,
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
        booking_url=_SAMPLE_BOOKING_URL,
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
# Round-trip max_results cap (migration constraint)
# ---------------------------------------------------------------------------


def test_round_trip_rejects_max_results_above_cap():
    # Round-trip burns one upstream call per result; capped at 5.
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL",
            destination="IAD",
            departure_date=TOMORROW.isoformat(),
            return_date=NEXT_WEEK.isoformat(),
            max_results=6,
        )


def test_round_trip_accepts_max_results_at_cap():
    m = SearchFlightsInput(
        origin="HEL",
        destination="IAD",
        departure_date=TOMORROW.isoformat(),
        return_date=NEXT_WEEK.isoformat(),
        max_results=5,
    )
    assert m.max_results == 5


def test_one_way_keeps_50_results_cap():
    # One-way is a single upstream call; the 50 ceiling from Field(le=50) applies.
    m = SearchFlightsInput(
        origin="HEL",
        destination="IAD",
        departure_date=TOMORROW.isoformat(),
        max_results=50,
    )
    assert m.max_results == 50

    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL",
            destination="IAD",
            departure_date=TOMORROW.isoformat(),
            max_results=51,
        )
