"""Orchestration tests for the search_flights tool function."""
from __future__ import annotations

import pytest

from flights_mcp.cache import TTLCache
from flights_mcp.fli_backend.client import FliClient
from flights_mcp.tools.search_flights import search_flights


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
    return FliClient(flight_searcher=searcher), searcher


# ----- happy paths -----------------------------------------------------------


async def test_one_way_returns_success_envelope(fli_one_way):
    client, _ = _client_with(results=fli_one_way)
    cache = TTLCache(ttl_seconds=300)
    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    assert "error" not in result
    assert len(result["results"]) == 2
    assert result["results"][0]["inbound"] is None


async def test_round_trip_returns_success_envelope(fli_round_trip):
    client, _ = _client_with(results=fli_round_trip)
    cache = TTLCache(ttl_seconds=300)
    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29",
    )
    assert "error" not in result
    assert len(result["results"]) >= 1
    first = result["results"][0]
    assert first["inbound"] is not None
    seg = first["outbound"]["segments"][0]
    assert "T" in seg["departure_time_local"]
    assert "+" not in seg["departure_time_local"]
    assert "Z" not in seg["departure_time_local"]


# ----- new fli-era filter params plumb through --------------------------------


async def test_max_stops_param_threads_through(fli_one_way):
    from fli.models import MaxStops as FliMaxStops
    client, searcher = _client_with(results=fli_one_way)
    cache = TTLCache(ttl_seconds=300)
    await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        departure_date="2026-05-18",
        max_stops="NON_STOP",
    )
    # The mock captured the filters that reached fli's SearchFlights.
    assert searcher.call_count == 1


async def test_departure_window_validation_at_boundary(fli_one_way):
    client, _ = _client_with(results=fli_one_way)
    cache = TTLCache(ttl_seconds=300)
    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        departure_date="2026-05-18",
        departure_window="not-a-window",
    )
    assert result["error"]["code"] == "invalid_input"


async def test_airlines_filter_validation(fli_one_way):
    client, _ = _client_with(results=fli_one_way)
    cache = TTLCache(ttl_seconds=300)
    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        departure_date="2026-05-18",
        airlines=["finnair"],  # lowercase — fails IataAirlineCode regex
    )
    assert result["error"]["code"] == "invalid_input"


# ----- error envelopes -------------------------------------------------------


async def test_invalid_input_returns_error_envelope(fli_one_way):
    client, _ = _client_with(results=fli_one_way)
    cache = TTLCache(ttl_seconds=300)
    result = await search_flights(
        client=client, cache=cache,
        origin="hel",  # lowercase — invalid
        destination="IAD", departure_date="2026-05-18",
    )
    assert result["error"]["code"] == "invalid_input"


async def test_no_results_returns_clean_message():
    client, _ = _client_with(results=[])
    cache = TTLCache(ttl_seconds=300)
    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    assert result["error"]["code"] == "no_results"
    # No more SerpAPI test-env caveats.
    assert "serpapi" not in result["error"]["message"].lower()
    assert "test environment" not in result["error"]["message"].lower()


async def test_upstream_error_returns_error_envelope():
    client, _ = _client_with(raises=RuntimeError("google said nope"))
    cache = TTLCache(ttl_seconds=300)
    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["retryable"] is True


async def test_unknown_airport_returns_invalid_input():
    client, _ = _client_with(results=[])
    cache = TTLCache(ttl_seconds=300)
    result = await search_flights(
        client=client, cache=cache,
        origin="ZZZ",  # passes regex, fails fli's Airport enum lookup
        destination="IAD", departure_date="2026-05-18",
    )
    assert result["error"]["code"] == "invalid_input"


# ----- caching ---------------------------------------------------------------


async def test_second_identical_call_is_cache_hit(fli_one_way):
    client, searcher = _client_with(results=fli_one_way)
    cache = TTLCache(ttl_seconds=300)
    kwargs = dict(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    await search_flights(**kwargs)
    await search_flights(**kwargs)
    assert searcher.call_count == 1


# ----- shape regression ------------------------------------------------------


async def test_full_round_trip_matches_documented_shape(fli_round_trip):
    client, _ = _client_with(results=fli_round_trip)
    cache = TTLCache(ttl_seconds=300)
    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29",
    )
    offer = result["results"][0]
    expected_keys = {
        "offer_id", "total_price", "currency", "price_per_adult",
        "airlines", "validating_airline", "outbound", "inbound",
        "seats_available", "last_ticketing_date", "fare_basis", "baggage_allowance",
        "booking_url",  # regression check: still populated post-migration
    }
    assert expected_keys.issubset(offer.keys())
    assert offer["booking_url"].startswith("https://www.google.com/travel/flights")
    seg = offer["outbound"]["segments"][0]
    assert "T" in seg["departure_time_local"]
    assert "+" not in seg["departure_time_local"]
    assert "Z" not in seg["departure_time_local"]
