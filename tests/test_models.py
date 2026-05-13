from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from flights_mcp.models import (
    CabinClass,
    DatePriceOffer,
    FlightOffer,
    Itinerary,
    MaxStops,
    SearchCheapestDatesInput,
    SearchCheapestDatesResult,
    SearchFlightsInput,
    SearchFlightsResult,
    Segment,
)

# Match the validator's UTC frame so tests can't flake at midnight UTC on UTC-offset hosts.
TODAY = datetime.now(tz=timezone.utc).date()
TOMORROW = TODAY + timedelta(days=1)
NEXT_WEEK = TODAY + timedelta(days=7)


# ----- SearchFlightsInput happy path -----------------------------------------


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
    assert m.max_stops is MaxStops.ANY
    assert m.departure_window is None
    assert m.airlines is None
    assert m.max_results == 20


def test_accepts_valid_one_way_with_defaults():
    m = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date=TOMORROW.isoformat(),
    )
    assert m.return_date is None
    assert m.max_results == 20


# ----- IATA, date, passenger validation (carried over from previous phases) --


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
            adults=1, infants=2,
        )


def test_rejects_total_travelers_above_limit():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date=TOMORROW.isoformat(),
            adults=5, children=4, infants=1,  # total = 10, cap is 9
        )


def test_rejects_max_results_above_50():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date=TOMORROW.isoformat(),
            max_results=51,
        )


def test_round_trip_max_results_50_now_allowed():
    """fli round-trip is a single upstream call, so the old 5-cap is gone."""
    m = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date=TOMORROW.isoformat(),
        return_date=NEXT_WEEK.isoformat(),
        max_results=50,
    )
    assert m.max_results == 50


def test_cabin_class_enum():
    m = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date=TOMORROW.isoformat(),
        cabin_class="BUSINESS",
    )
    assert m.cabin_class is CabinClass.BUSINESS


# ----- New fli-era input fields ----------------------------------------------


def test_max_stops_accepts_all_fli_values():
    for v in ("ANY", "NON_STOP", "ONE_STOP_OR_FEWER", "TWO_OR_FEWER_STOPS"):
        m = SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date=TOMORROW.isoformat(),
            max_stops=v,
        )
        assert m.max_stops.value == v


def test_max_stops_rejects_unknown_value():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date=TOMORROW.isoformat(),
            max_stops="THREE_STOPS",
        )


def test_departure_window_accepts_valid_range():
    m = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date=TOMORROW.isoformat(),
        departure_window="6-20",
    )
    assert m.departure_window == "6-20"


def test_departure_window_rejects_malformed():
    for bad in ("morning", "6", "6-20-22", "6:00-20:00"):
        with pytest.raises(ValidationError):
            SearchFlightsInput(
                origin="HEL", destination="IAD",
                departure_date=TOMORROW.isoformat(),
                departure_window=bad,
            )


def test_departure_window_rejects_inverted_range():
    """End hour must be after start hour."""
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date=TOMORROW.isoformat(),
            departure_window="20-6",
        )


def test_departure_window_rejects_hour_over_23():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date=TOMORROW.isoformat(),
            departure_window="0-24",
        )


def test_airlines_filter_accepts_iata_list():
    m = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date=TOMORROW.isoformat(),
        airlines=["AY", "FI"],
    )
    assert m.airlines == ["AY", "FI"]


def test_airlines_filter_rejects_bad_codes():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date=TOMORROW.isoformat(),
            airlines=["Finnair"],
        )


# ----- output model shape (unchanged from previous phase) --------------------


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


_SAMPLE_BOOKING_URL = "https://www.google.com/travel/flights?q=Flights+from+HEL+to+IAD+on+2026-05-18"


def test_segment_round_trips():
    s = _make_segment()
    assert s.airline == "AY"
    assert s.departure_time_local == "2026-05-18T15:30:00"


def test_itinerary_holds_segments():
    it = Itinerary(duration="PT10H30M", stops=1, segments=[_make_segment(), _make_segment(flight_number="AY99")])
    assert it.stops == 1
    assert len(it.segments) == 2


def test_itinerary_rejects_empty_segments():
    with pytest.raises(ValidationError):
        Itinerary(duration="PT10H30M", stops=0, segments=[])


def test_itinerary_rejects_negative_stops():
    with pytest.raises(ValidationError):
        Itinerary(duration="PT10H30M", stops=-1, segments=[_make_segment()])


def test_segment_rejects_overlong_airline_code():
    with pytest.raises(ValidationError):
        _make_segment(airline="AYZZZ")


def _make_offer(**overrides):
    it = Itinerary(duration="PT10H30M", stops=0, segments=[_make_segment()])
    base = dict(
        offer_id="abc",
        total_price=850.5,
        currency="USD",
        price_per_adult=850.5,
        airlines=["AY"],
        validating_airline="AY",
        outbound=it,
        inbound=None,
        seats_available=None,
        last_ticketing_date=None,
        fare_basis="",
        baggage_allowance=None,
        booking_url=_SAMPLE_BOOKING_URL,
    )
    base.update(overrides)
    return FlightOffer(**base)


def test_flight_offer_round_trip_shape():
    offer = _make_offer(
        seats_available=7,
        last_ticketing_date="2026-05-15",
        fare_basis="VLOWFI",
        baggage_allowance="1 checked bag",
    )
    assert offer.inbound is None
    assert offer.baggage_allowance == "1 checked bag"


def test_flight_offer_allows_null_optional_fields():
    offer = _make_offer()
    assert offer.seats_available is None
    assert offer.last_ticketing_date is None
    assert offer.baggage_allowance is None


def test_search_flights_result_wraps_offers():
    offer = _make_offer()
    result = SearchFlightsResult(results=[offer])
    assert len(result.results) == 1


# ----- inbound_window (added in Phase 2) -------------------------------------


def test_inbound_window_accepts_valid_range():
    m = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date=TOMORROW.isoformat(),
        return_date=NEXT_WEEK.isoformat(),
        inbound_window="6-20",
    )
    assert m.inbound_window == "6-20"


def test_inbound_window_rejects_inverted_range():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date=TOMORROW.isoformat(),
            return_date=NEXT_WEEK.isoformat(),
            inbound_window="20-6",
        )


def test_inbound_window_rejects_malformed():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD",
            departure_date=TOMORROW.isoformat(),
            return_date=NEXT_WEEK.isoformat(),
            inbound_window="evening",
        )


def test_inbound_window_default_none():
    m = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date=TOMORROW.isoformat(),
    )
    assert m.inbound_window is None


# ----- SearchCheapestDatesInput (Phase 2) ------------------------------------


def test_cheapest_dates_one_way_accepts_minimal():
    m = SearchCheapestDatesInput(
        origin="HEL", destination="IAD",
        start_date=TOMORROW.isoformat(),
        end_date=NEXT_WEEK.isoformat(),
    )
    assert m.is_round_trip is False
    assert m.passengers == 1
    assert m.cabin_class is CabinClass.ECONOMY
    assert m.max_stops is MaxStops.ANY
    assert m.trip_duration is None


def test_cheapest_dates_round_trip_requires_trip_duration():
    with pytest.raises(ValidationError):
        SearchCheapestDatesInput(
            origin="HEL", destination="IAD",
            start_date=TOMORROW.isoformat(),
            end_date=NEXT_WEEK.isoformat(),
            is_round_trip=True,
        )


def test_cheapest_dates_round_trip_with_duration_ok():
    m = SearchCheapestDatesInput(
        origin="HEL", destination="IAD",
        start_date=TOMORROW.isoformat(),
        end_date=NEXT_WEEK.isoformat(),
        is_round_trip=True,
        trip_duration=11,
    )
    assert m.trip_duration == 11


def test_cheapest_dates_rejects_end_before_start():
    with pytest.raises(ValidationError):
        SearchCheapestDatesInput(
            origin="HEL", destination="IAD",
            start_date=NEXT_WEEK.isoformat(),
            end_date=TOMORROW.isoformat(),
        )


def test_cheapest_dates_rejects_start_in_past():
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        SearchCheapestDatesInput(
            origin="HEL", destination="IAD",
            start_date=yesterday,
            end_date=NEXT_WEEK.isoformat(),
        )


def test_cheapest_dates_trip_duration_cap_at_365():
    with pytest.raises(ValidationError):
        SearchCheapestDatesInput(
            origin="HEL", destination="IAD",
            start_date=TOMORROW.isoformat(),
            end_date=NEXT_WEEK.isoformat(),
            is_round_trip=True,
            trip_duration=366,
        )


def test_cheapest_dates_trip_duration_accepts_365():
    m = SearchCheapestDatesInput(
        origin="HEL", destination="IAD",
        start_date=TOMORROW.isoformat(),
        end_date=NEXT_WEEK.isoformat(),
        is_round_trip=True,
        trip_duration=365,
    )
    assert m.trip_duration == 365


def test_cheapest_dates_departure_window_validates():
    # Same window-validator pattern as search_flights.
    with pytest.raises(ValidationError):
        SearchCheapestDatesInput(
            origin="HEL", destination="IAD",
            start_date=TOMORROW.isoformat(),
            end_date=NEXT_WEEK.isoformat(),
            departure_window="20-6",
        )


# ----- DatePriceOffer shape --------------------------------------------------


def test_date_price_offer_round_trip():
    o = DatePriceOffer(
        departure_date="2026-05-18",
        return_date="2026-05-29",
        price=540.0,
        currency="EUR",
    )
    assert o.return_date == "2026-05-29"


def test_date_price_offer_one_way_has_null_return():
    o = DatePriceOffer(
        departure_date="2026-05-18",
        return_date=None,
        price=300.0,
        currency="EUR",
    )
    assert o.return_date is None


def test_search_cheapest_dates_result_wraps():
    o = DatePriceOffer(
        departure_date="2026-05-18", return_date="2026-05-29",
        price=540.0, currency="EUR",
    )
    r = SearchCheapestDatesResult(results=[o])
    assert len(r.results) == 1


# ----- SearchHotelsInput (hotels extension) ---------------------------------


def test_hotels_accepts_minimal_input():
    from flights_mcp.models import HotelSortBy, SearchHotelsInput
    m = SearchHotelsInput(
        location="Tampere",
        check_in_date=TOMORROW.isoformat(),
        check_out_date=NEXT_WEEK.isoformat(),
    )
    assert m.adults == 2  # hotels-specific default
    assert m.rooms == 1
    assert m.sort_by is HotelSortBy.BEST
    assert m.max_results == 10


def test_hotels_rejects_check_out_before_check_in():
    from flights_mcp.models import SearchHotelsInput
    with pytest.raises(ValidationError):
        SearchHotelsInput(
            location="Tampere",
            check_in_date=NEXT_WEEK.isoformat(),
            check_out_date=TOMORROW.isoformat(),
        )


def test_hotels_rejects_same_day_check_in_check_out():
    """check_out_date must be STRICTLY after check_in_date."""
    from flights_mcp.models import SearchHotelsInput
    with pytest.raises(ValidationError):
        SearchHotelsInput(
            location="Tampere",
            check_in_date=TOMORROW.isoformat(),
            check_out_date=TOMORROW.isoformat(),
        )


def test_hotels_rejects_check_in_in_past():
    from flights_mcp.models import SearchHotelsInput
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        SearchHotelsInput(
            location="Tampere",
            check_in_date=yesterday,
            check_out_date=NEXT_WEEK.isoformat(),
        )


def test_hotels_rejects_empty_location():
    from flights_mcp.models import SearchHotelsInput
    with pytest.raises(ValidationError):
        SearchHotelsInput(
            location="",
            check_in_date=TOMORROW.isoformat(),
            check_out_date=NEXT_WEEK.isoformat(),
        )


def test_hotels_min_rating_bounded():
    from flights_mcp.models import SearchHotelsInput
    # 0 and 6 are out of [1, 5]
    for bad in (0, 6):
        with pytest.raises(ValidationError):
            SearchHotelsInput(
                location="Tampere",
                check_in_date=TOMORROW.isoformat(),
                check_out_date=NEXT_WEEK.isoformat(),
                min_rating=bad,
            )


def test_hotels_max_results_capped_at_25():
    from flights_mcp.models import SearchHotelsInput
    with pytest.raises(ValidationError):
        SearchHotelsInput(
            location="Tampere",
            check_in_date=TOMORROW.isoformat(),
            check_out_date=NEXT_WEEK.isoformat(),
            max_results=26,
        )


def test_hotels_sort_by_enum_coercion():
    from flights_mcp.models import HotelSortBy, SearchHotelsInput
    m = SearchHotelsInput(
        location="Tampere",
        check_in_date=TOMORROW.isoformat(),
        check_out_date=NEXT_WEEK.isoformat(),
        sort_by="PRICE_LOW",
    )
    assert m.sort_by is HotelSortBy.PRICE_LOW
