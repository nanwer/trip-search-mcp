"""Orchestration tests for the search_cheapest_dates tool function."""
from __future__ import annotations

import pytest

from flights_mcp.cache import TTLCache
from flights_mcp.fli_backend.client import FliClient
from flights_mcp.tools.search_cheapest_dates import search_cheapest_dates


class _MockSearcher:
    def __init__(self, *, results=None, raises: Exception | None = None):
        self._results = results
        self._raises = raises
        self.call_count = 0

    def search(self, filters, *args, **kwargs):
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return self._results


def _client_with(results=None, raises=None) -> tuple[FliClient, _MockSearcher]:
    searcher = _MockSearcher(results=results, raises=raises)
    return FliClient(date_searcher=searcher), searcher


# ----- happy paths ----------------------------------------------------------


async def test_round_trip_returns_sorted_envelope(fli_dates_flex):
    client, _ = _client_with(results=fli_dates_flex)
    cache = TTLCache(ttl_seconds=300)
    result = await search_cheapest_dates(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        start_date="2026-05-15", end_date="2026-05-25",
        is_round_trip=True, trip_duration=11,
    )
    assert "error" not in result
    prices = [r["price"] for r in result["results"]]
    assert prices == sorted(prices), "results must be sorted by price ascending"
    # Fixture's cheapest is 540.0; should be first.
    assert prices[0] == 540.0


async def test_one_way_return_date_is_null():
    """Even with round-trip fixture, request shape says is_round_trip=False → null returns."""
    from datetime import datetime
    from fli.search import DatePrice
    one_way_results = [
        DatePrice(date=(datetime(2026, 5, 18),), price=300.0, currency="EUR"),
        DatePrice(date=(datetime(2026, 5, 19),), price=275.0, currency="EUR"),
    ]
    client, _ = _client_with(results=one_way_results)
    cache = TTLCache(ttl_seconds=300)
    result = await search_cheapest_dates(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        start_date="2026-05-15", end_date="2026-05-25",
        is_round_trip=False,
    )
    assert "error" not in result
    for r in result["results"]:
        assert r["return_date"] is None


# ----- error envelopes ------------------------------------------------------


async def test_round_trip_without_trip_duration_returns_invalid_input(fli_dates_flex):
    client, _ = _client_with(results=fli_dates_flex)
    cache = TTLCache(ttl_seconds=300)
    result = await search_cheapest_dates(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        start_date="2026-05-15", end_date="2026-05-25",
        is_round_trip=True,
        # trip_duration missing
    )
    assert result["error"]["code"] == "invalid_input"
    assert "trip_duration" in result["error"]["message"].lower()


async def test_end_before_start_returns_invalid_input(fli_dates_flex):
    client, _ = _client_with(results=fli_dates_flex)
    cache = TTLCache(ttl_seconds=300)
    result = await search_cheapest_dates(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        start_date="2026-05-25", end_date="2026-05-15",  # inverted
        is_round_trip=True, trip_duration=11,
    )
    assert result["error"]["code"] == "invalid_input"


async def test_no_results_returns_clean_message():
    client, _ = _client_with(results=[])
    cache = TTLCache(ttl_seconds=300)
    result = await search_cheapest_dates(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        start_date="2026-05-15", end_date="2026-05-25",
        is_round_trip=True, trip_duration=11,
    )
    assert result["error"]["code"] == "no_results"
    assert "widening" in result["error"]["message"].lower() or "relaxing" in result["error"]["message"].lower()


async def test_upstream_error_returns_error_envelope():
    client, _ = _client_with(raises=RuntimeError("Google said nope"))
    cache = TTLCache(ttl_seconds=300)
    result = await search_cheapest_dates(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        start_date="2026-05-15", end_date="2026-05-25",
        is_round_trip=True, trip_duration=11,
    )
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["retryable"] is True


# ----- caching --------------------------------------------------------------


async def test_second_identical_call_is_cache_hit(fli_dates_flex):
    client, searcher = _client_with(results=fli_dates_flex)
    cache = TTLCache(ttl_seconds=300)
    kwargs = dict(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        start_date="2026-05-15", end_date="2026-05-25",
        is_round_trip=True, trip_duration=11,
    )
    await search_cheapest_dates(**kwargs)
    await search_cheapest_dates(**kwargs)
    assert searcher.call_count == 1


async def test_cache_key_namespaced_apart_from_search_flights(fli_dates_flex, fli_round_trip):
    """search_flights and search_cheapest_dates must not share cache entries."""
    from flights_mcp.tools.search_flights import search_flights

    flight_searcher = _MockSearcher(results=fli_round_trip)
    date_searcher = _MockSearcher(results=fli_dates_flex)
    # Build a client with both searchers.
    client = FliClient(flight_searcher=flight_searcher, date_searcher=date_searcher)
    cache = TTLCache(ttl_seconds=300)

    # Two different tools share several identical input fields (origin,
    # destination, cabin_class, max_stops, airlines) — without namespacing
    # they could collide. With namespacing they don't.
    await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    await search_cheapest_dates(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        start_date="2026-05-15", end_date="2026-05-25",
        is_round_trip=True, trip_duration=11,
    )
    # Both upstream calls fired — no cross-tool cache pollution.
    assert flight_searcher.call_count == 1
    assert date_searcher.call_count == 1


# ----- shape regression -----------------------------------------------------


async def test_result_shape(fli_dates_flex):
    client, _ = _client_with(results=fli_dates_flex)
    cache = TTLCache(ttl_seconds=300)
    result = await search_cheapest_dates(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        start_date="2026-05-15", end_date="2026-05-25",
        is_round_trip=True, trip_duration=11,
    )
    assert "results" in result
    first = result["results"][0]
    assert {"departure_date", "return_date", "price", "currency"}.issubset(first.keys())
    # ISO YYYY-MM-DD format.
    assert len(first["departure_date"]) == 10 and first["departure_date"][4] == "-"
