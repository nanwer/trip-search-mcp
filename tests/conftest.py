import json
from datetime import datetime
from pathlib import Path

import pytest

from fli.models import Airline, Airport, FlightLeg, FlightResult
from fli.search import DatePrice

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> object:
    with (FIXTURES / name).open(encoding="utf-8") as f:
        return json.load(f)


def _leg_from_dict(d: dict) -> FlightLeg:
    """Construct a FlightLeg from IATA-coded fixture data.

    fli's enum *values* are full names ('Finnair'), not IATA codes — so a
    naive `FlightLeg.model_validate(json)` rejects 'AY'. We translate IATA
    codes to enum members here so test fixtures stay short and readable.
    """
    return FlightLeg(
        airline=Airline[d["airline"]],
        flight_number=d["flight_number"],
        departure_airport=Airport[d["departure_airport"]],
        arrival_airport=Airport[d["arrival_airport"]],
        departure_datetime=datetime.fromisoformat(d["departure_datetime"]),
        arrival_datetime=datetime.fromisoformat(d["arrival_datetime"]),
        duration=d["duration"],
    )


def _result_from_dict(d: dict) -> FlightResult:
    return FlightResult(
        legs=[_leg_from_dict(leg) for leg in d["legs"]],
        price=d["price"],
        currency=d.get("currency"),
        duration=d["duration"],
        stops=d["stops"],
    )


def _date_price_from_dict(d: dict) -> DatePrice:
    return DatePrice(
        date=tuple(datetime.fromisoformat(s) for s in d["date"]),
        price=d["price"],
        currency=d.get("currency"),
    )


@pytest.fixture
def fli_one_way() -> list[FlightResult]:
    """Two one-way HEL→IAD options, one direct and one with a layover."""
    return [_result_from_dict(item) for item in _load("fli_one_way_success.json")]


@pytest.fixture
def fli_round_trip() -> list[tuple[FlightResult, FlightResult]]:
    """Two round-trip pairs as (outbound, inbound) tuples."""
    return [
        (_result_from_dict(pair[0]), _result_from_dict(pair[1]))
        for pair in _load("fli_round_trip_success.json")
    ]


@pytest.fixture
def fli_dates_flex() -> list[DatePrice]:
    """Five DatePrice entries, unsorted by price (matching upstream behavior)."""
    return [_date_price_from_dict(item) for item in _load("fli_dates_flex.json")]


@pytest.fixture
def fli_empty() -> list:
    return _load("fli_empty_results.json")


@pytest.fixture
def serpapi_hotels_success() -> dict:
    """3-property synthetic SerpAPI google_hotels response covering:
    a 4-star inn with full data, a vacation rental with sparse data
    (no hotel_class), and a 5-star hotel for sort-order tests."""
    return _load("serpapi_hotels_success.json")


@pytest.fixture
def serpapi_hotels_empty() -> dict:
    return _load("serpapi_hotels_empty.json")
