import httpx
import pytest

from flights_mcp.cache import TTLCache
from flights_mcp.serpapi.client import SerpAPIClient
from flights_mcp.tools.search_flights import (
    DEFAULT_MAX_RESULTS_ONE_WAY,
    DEFAULT_MAX_RESULTS_ROUND_TRIP,
    search_flights,
)


def _make_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SerpAPIClient(http=http, api_key="fake-key")


def _one_handler(body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)
    return handler


def _two_step_handler(outbound: dict, return_leg: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if "departure_token" in dict(request.url.params):
            return httpx.Response(200, json=return_leg)
        return httpx.Response(200, json=outbound)
    return handler


# ----- happy paths -----------------------------------------------------------


async def test_one_way_returns_success_envelope(serpapi_one_way):
    client = _make_client(_one_handler(serpapi_one_way))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    assert "error" not in result
    assert len(result["results"]) == 2
    assert result["results"][0]["inbound"] is None


async def test_round_trip_returns_success_envelope(
    serpapi_round_trip_outbound, serpapi_round_trip_return,
):
    client = _make_client(_two_step_handler(serpapi_round_trip_outbound, serpapi_round_trip_return))
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
    assert first["outbound"]["stops"] >= 0
    seg = first["outbound"]["segments"][0]
    assert "T" in seg["departure_time_local"]
    assert "+" not in seg["departure_time_local"]
    assert "Z" not in seg["departure_time_local"]


# ----- smart defaults --------------------------------------------------------


async def test_one_way_default_max_results(serpapi_one_way):
    client = _make_client(_one_handler(serpapi_one_way))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    assert DEFAULT_MAX_RESULTS_ONE_WAY == 20
    assert len(result["results"]) == 2


async def test_round_trip_default_max_results(
    serpapi_round_trip_outbound, serpapi_round_trip_return,
):
    call_log: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        call_log.append(params)
        if "departure_token" in params:
            return httpx.Response(200, json=serpapi_round_trip_return)
        return httpx.Response(200, json=serpapi_round_trip_outbound)

    client = _make_client(handler)
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29",
    )
    assert DEFAULT_MAX_RESULTS_ROUND_TRIP == 3
    # Outbound fixture has 2 options total; expect 1 outbound + 2 return calls.
    assert len(call_log) == 3
    assert len(result["results"]) == 2


# ----- error envelopes -------------------------------------------------------


async def test_invalid_input_returns_error_envelope(serpapi_one_way):
    client = _make_client(_one_handler(serpapi_one_way))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        client=client, cache=cache,
        origin="hel",
        destination="IAD", departure_date="2026-05-18",
    )
    assert "error" in result
    assert result["error"]["code"] == "invalid_input"


async def test_round_trip_max_results_above_cap_returns_invalid_input(serpapi_one_way):
    client = _make_client(_one_handler(serpapi_one_way))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29",
        max_results=10,
    )
    assert result["error"]["code"] == "invalid_input"
    assert "round-trip" in result["error"]["message"].lower()


async def test_no_results_returns_clean_message(serpapi_empty_results):
    client = _make_client(_one_handler(serpapi_empty_results))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    assert result["error"]["code"] == "no_results"
    # No more test/prod env distinction — message is provider-neutral.
    assert "test environment" not in result["error"]["message"].lower()


async def test_auth_failure_returns_error_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid API key"})

    client = _make_client(handler)
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    assert result["error"]["code"] == "auth_failed"


# ----- caching ---------------------------------------------------------------


async def test_second_identical_call_is_cache_hit(serpapi_one_way):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=serpapi_one_way)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SerpAPIClient(http=http, api_key="fake-key")
    cache = TTLCache(ttl_seconds=300)

    kwargs = dict(
        client=client, cache=cache,
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    await search_flights(**kwargs)
    await search_flights(**kwargs)
    assert call_count["n"] == 1


# ----- shape regression -------------------------------------------------------


async def test_full_round_trip_matches_documented_shape(
    serpapi_round_trip_outbound, serpapi_round_trip_return,
):
    client = _make_client(_two_step_handler(serpapi_round_trip_outbound, serpapi_round_trip_return))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        client=client, cache=cache,
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29",
    )
    assert "results" in result and isinstance(result["results"], list)
    offer = result["results"][0]
    expected_keys = {
        "offer_id", "total_price", "currency", "price_per_adult",
        "airlines", "validating_airline", "outbound", "inbound",
        "seats_available", "last_ticketing_date", "fare_basis", "baggage_allowance",
    }
    assert expected_keys.issubset(offer.keys())

    outbound = offer["outbound"]
    assert {"duration", "stops", "segments"}.issubset(outbound.keys())
    seg = outbound["segments"][0]
    assert {
        "airline", "flight_number", "departure_airport", "departure_time_local",
        "arrival_airport", "arrival_time_local", "cabin", "booking_class",
    }.issubset(seg.keys())
    assert "+" not in seg["departure_time_local"]
    assert "Z" not in seg["departure_time_local"]
    assert "T" in seg["departure_time_local"]
