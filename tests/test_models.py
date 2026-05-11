from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from flights_mcp.models import CabinClass, SearchFlightsInput

TODAY = date.today()
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
