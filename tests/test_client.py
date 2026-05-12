"""Tests for fli_backend.client.

Inject mock SearchFlights/SearchDates instances so tests never hit Google.
"""
from __future__ import annotations

import pytest

from flights_mcp.errors import ErrorCode, ToolError
from flights_mcp.fli_backend.client import FliClient
from flights_mcp.models import (
    MaxStops,
    SearchCheapestDatesInput,
    SearchFlightsInput,
)


class _MockSearcher:
    """Stand-in for fli.search.SearchFlights / SearchDates.

    `search()` returns whatever was passed in, or raises if `raises` is set.
    Captures the last filter passed for assertions.
    """
    def __init__(self, *, results=None, raises: Exception | None = None):
        self._results = results
        self._raises = raises
        self.last_filters = None
        self.last_extra: tuple = ()

    def search(self, filters, *args, **kwargs):
        self.last_filters = filters
        self.last_extra = args
        if self._raises is not None:
            raise self._raises
        return self._results


def _input(**overrides) -> SearchFlightsInput:
    base = dict(
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    base.update(overrides)
    return SearchFlightsInput(**base)


# ----- happy paths -----------------------------------------------------------


async def test_one_way_returns_offers(fli_one_way):
    searcher = _MockSearcher(results=fli_one_way)
    client = FliClient(flight_searcher=searcher)
    offers = await client.search(_input())
    assert len(offers) == 2
    assert offers[0].outbound.segments[0].departure_airport == "HEL"
    assert offers[0].inbound is None


async def test_round_trip_returns_paired_offers(fli_round_trip):
    searcher = _MockSearcher(results=fli_round_trip)
    client = FliClient(flight_searcher=searcher)
    offers = await client.search(_input(return_date="2026-05-29"))
    assert len(offers) == 2
    assert all(o.inbound is not None for o in offers)


async def test_respects_max_results(fli_one_way):
    searcher = _MockSearcher(results=fli_one_way)
    client = FliClient(flight_searcher=searcher)
    offers = await client.search(_input(max_results=1))
    assert len(offers) == 1


# ----- filter construction --------------------------------------------------


async def test_one_way_builds_one_flight_segment(fli_one_way):
    searcher = _MockSearcher(results=fli_one_way)
    client = FliClient(flight_searcher=searcher)
    await client.search(_input())
    assert len(searcher.last_filters.flight_segments) == 1
    assert searcher.last_filters.flight_segments[0].travel_date == "2026-05-18"


async def test_round_trip_builds_two_flight_segments(fli_round_trip):
    searcher = _MockSearcher(results=fli_round_trip)
    client = FliClient(flight_searcher=searcher)
    await client.search(_input(return_date="2026-05-29"))
    segs = searcher.last_filters.flight_segments
    assert len(segs) == 2
    assert segs[0].travel_date == "2026-05-18"
    assert segs[1].travel_date == "2026-05-29"


async def test_max_stops_passes_through(fli_one_way):
    from fli.models import MaxStops as FliMaxStops
    searcher = _MockSearcher(results=fli_one_way)
    client = FliClient(flight_searcher=searcher)
    await client.search(_input(max_stops="NON_STOP"))
    assert searcher.last_filters.stops is FliMaxStops.NON_STOP


async def test_departure_window_becomes_time_restrictions(fli_one_way):
    searcher = _MockSearcher(results=fli_one_way)
    client = FliClient(flight_searcher=searcher)
    await client.search(_input(departure_window="6-20"))
    tr = searcher.last_filters.flight_segments[0].time_restrictions
    assert tr is not None
    assert tr.earliest_departure == 6
    assert tr.latest_departure == 20


async def test_airlines_filter_passes_iata_codes(fli_one_way):
    searcher = _MockSearcher(results=fli_one_way)
    client = FliClient(flight_searcher=searcher)
    await client.search(_input(airlines=["AY", "FI"]))
    airlines = searcher.last_filters.airlines
    assert airlines is not None
    assert [a.name for a in airlines] == ["AY", "FI"]


async def test_passenger_info_passes_all_categories(fli_one_way):
    searcher = _MockSearcher(results=fli_one_way)
    client = FliClient(flight_searcher=searcher)
    await client.search(_input(adults=2, children=1, infants=1))
    info = searcher.last_filters.passenger_info
    assert info.adults == 2
    assert info.children == 1
    assert info.infants_on_lap == 1


# ----- error paths ----------------------------------------------------------


async def test_empty_results_raises_no_results():
    searcher = _MockSearcher(results=[])
    client = FliClient(flight_searcher=searcher)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_none_results_raises_no_results():
    # fli's search returns None when no flights match.
    searcher = _MockSearcher(results=None)
    client = FliClient(flight_searcher=searcher)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_unknown_origin_raises_invalid_input():
    """ZZZ matches our regex but isn't in fli's Airport enum."""
    searcher = _MockSearcher(results=[])
    client = FliClient(flight_searcher=searcher)
    # Bypass SearchFlightsInput's loose IATA check by passing a real-format
    # string fli won't recognize. Use ZZZ which is unlikely to be in fli's
    # Airport enum (it's not a real IATA airport).
    with pytest.raises(ToolError) as exc:
        await client.search(_input(origin="ZZZ"))
    assert exc.value.code is ErrorCode.INVALID_INPUT


async def test_unknown_airline_filter_raises_invalid_input(fli_one_way):
    searcher = _MockSearcher(results=fli_one_way)
    client = FliClient(flight_searcher=searcher)
    # "QQQ" matches our IataAirlineCode regex but is not in fli's Airline enum.
    with pytest.raises(ToolError) as exc:
        await client.search(_input(airlines=["QQQ"]))
    assert exc.value.code is ErrorCode.INVALID_INPUT


async def test_searcher_exception_maps_to_upstream_error():
    searcher = _MockSearcher(raises=RuntimeError("Google said no"))
    client = FliClient(flight_searcher=searcher)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
    assert exc.value.retryable is True


async def test_inbound_window_threaded_into_normalize(fli_round_trip):
    """End-to-end: inbound_window on input filters the result list."""
    searcher = _MockSearcher(results=fli_round_trip)
    client = FliClient(flight_searcher=searcher)
    inp = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29",
        inbound_window="6-19",
    )
    offers = await client.search(inp)
    assert len(offers) == 1  # fixture has one entry inside 6-19, one outside


# ============================================================================
# search_dates (Phase 2)
# ============================================================================


def _dates_input(**overrides) -> SearchCheapestDatesInput:
    base = dict(
        origin="HEL", destination="IAD",
        start_date="2026-05-15", end_date="2026-05-25",
    )
    base.update(overrides)
    return SearchCheapestDatesInput(**base)


async def test_search_dates_returns_offers(fli_dates_flex):
    searcher = _MockSearcher(results=fli_dates_flex)
    client = FliClient(date_searcher=searcher)
    offers = await client.search_dates(_dates_input(is_round_trip=True, trip_duration=11))
    assert len(offers) == 5
    assert all(o.currency == "EUR" for o in offers)


async def test_search_dates_round_trip_builds_two_segments(fli_dates_flex):
    searcher = _MockSearcher(results=fli_dates_flex)
    client = FliClient(date_searcher=searcher)
    await client.search_dates(_dates_input(is_round_trip=True, trip_duration=11))
    filters = searcher.last_filters
    assert len(filters.flight_segments) == 2
    assert filters.from_date == "2026-05-15"
    assert filters.to_date == "2026-05-25"
    assert filters.duration == 11


async def test_search_dates_one_way_builds_one_segment(fli_dates_flex):
    searcher = _MockSearcher(results=fli_dates_flex)
    client = FliClient(date_searcher=searcher)
    await client.search_dates(_dates_input())  # is_round_trip=False
    filters = searcher.last_filters
    assert len(filters.flight_segments) == 1
    assert filters.duration is None


async def test_search_dates_empty_raises_no_results():
    searcher = _MockSearcher(results=[])
    client = FliClient(date_searcher=searcher)
    with pytest.raises(ToolError) as exc:
        await client.search_dates(_dates_input(is_round_trip=True, trip_duration=11))
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_search_dates_none_raises_no_results():
    searcher = _MockSearcher(results=None)
    client = FliClient(date_searcher=searcher)
    with pytest.raises(ToolError) as exc:
        await client.search_dates(_dates_input())
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_search_dates_unknown_origin_invalid_input():
    searcher = _MockSearcher(results=[])
    client = FliClient(date_searcher=searcher)
    with pytest.raises(ToolError) as exc:
        await client.search_dates(_dates_input(origin="ZZZ"))
    assert exc.value.code is ErrorCode.INVALID_INPUT


async def test_search_dates_passes_max_stops_and_airlines(fli_dates_flex):
    from fli.models import MaxStops as FliMaxStops
    searcher = _MockSearcher(results=fli_dates_flex)
    client = FliClient(date_searcher=searcher)
    await client.search_dates(_dates_input(
        max_stops="NON_STOP",
        airlines=["AY", "FI"],
    ))
    filters = searcher.last_filters
    assert filters.stops is FliMaxStops.NON_STOP
    assert [a.name for a in filters.airlines] == ["AY", "FI"]


async def test_search_dates_passes_departure_window(fli_dates_flex):
    searcher = _MockSearcher(results=fli_dates_flex)
    client = FliClient(date_searcher=searcher)
    await client.search_dates(_dates_input(departure_window="8-20"))
    tr = searcher.last_filters.flight_segments[0].time_restrictions
    assert tr is not None
    assert tr.earliest_departure == 8
    assert tr.latest_departure == 20
