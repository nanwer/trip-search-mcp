import httpx
import pytest

from flights_mcp.amadeus.client import AmadeusClient
from flights_mcp.cache import TTLCache
from flights_mcp.tools.search_flights import search_flights


def _make_amadeus(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return AmadeusClient(http=http, base_url="https://test.api.amadeus.com",
                         client_id="id", client_secret="sec")


def _token_or(json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 1800})
        return httpx.Response(200, json=json_response)
    return handler


async def test_returns_success_envelope(synthetic_round_trip):
    amadeus = _make_amadeus(_token_or(synthetic_round_trip))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        amadeus=amadeus, cache=cache, env="test",
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", adults=1,
    )
    assert "error" not in result
    assert len(result["results"]) == 2
    assert result["results"][0]["offer_id"] == "1"


async def test_invalid_input_returns_error_envelope(synthetic_round_trip):
    amadeus = _make_amadeus(_token_or(synthetic_round_trip))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        amadeus=amadeus, cache=cache, env="test",
        origin="hel",  # lowercase — invalid
        destination="IAD", departure_date="2026-05-18",
    )
    assert "error" in result
    assert result["error"]["code"] == "invalid_input"


async def test_no_results_message_varies_by_env(empty_results):
    amadeus = _make_amadeus(_token_or(empty_results))
    cache = TTLCache(ttl_seconds=300)

    test_result = await search_flights(
        amadeus=amadeus, cache=cache, env="test",
        origin="ZZZ", destination="QQQ", departure_date="2026-05-18",
    )
    assert test_result["error"]["code"] == "no_results"
    assert "test environment" in test_result["error"]["message"].lower()

    prod_result = await search_flights(
        amadeus=amadeus, cache=cache, env="production",
        origin="ZZZ", destination="QQQ", departure_date="2026-05-18",
    )
    assert prod_result["error"]["code"] == "no_results"
    assert "test environment" not in prod_result["error"]["message"].lower()


async def test_second_identical_call_is_cache_hit(synthetic_round_trip):
    call_count = {"search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 1800})
        call_count["search"] += 1
        return httpx.Response(200, json=synthetic_round_trip)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    amadeus = AmadeusClient(http=http, base_url="https://test.api.amadeus.com",
                            client_id="id", client_secret="sec")
    cache = TTLCache(ttl_seconds=300)

    kwargs = dict(
        amadeus=amadeus, cache=cache, env="test",
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", adults=1,
    )
    await search_flights(**kwargs)
    await search_flights(**kwargs)
    assert call_count["search"] == 1


async def test_full_round_trip_matches_documented_shape(synthetic_round_trip):
    amadeus = _make_amadeus(_token_or(synthetic_round_trip))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        amadeus=amadeus, cache=cache, env="test",
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", adults=1,
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
    # Time format contract: no offset, ISO datetime.
    assert "+" not in seg["departure_time_local"]
    assert "Z" not in seg["departure_time_local"]
    assert "T" in seg["departure_time_local"]
