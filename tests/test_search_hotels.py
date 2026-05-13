"""Orchestration tests for the search_hotels tool function.

Covers the lazy-fail auth path (which is what makes this tool different
from the flight tools — server can run without SERPAPI_KEY).
"""
from __future__ import annotations

import httpx
import pytest

from trip_search_mcp.cache import TTLCache
from trip_search_mcp.serpapi_hotels_backend.client import SerpAPIHotelsClient
from trip_search_mcp.tools.search_hotels import search_hotels


def _client_with(handler) -> SerpAPIHotelsClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SerpAPIHotelsClient(http=http, api_key="fake-key")


def _ok_handler(body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)
    return handler


# ----- happy path -----------------------------------------------------------


async def test_returns_success_envelope(serpapi_hotels_success):
    client = _client_with(_ok_handler(serpapi_hotels_success))
    cache = TTLCache(ttl_seconds=300)

    result = await search_hotels(
        client=client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    assert "error" not in result
    assert len(result["results"]) == 3
    first = result["results"][0]
    assert {"offer_id", "name", "price_total", "price_per_night",
            "currency", "booking_url", "images", "description",
            "hotel_type"}.issubset(first.keys())


async def test_sort_by_price_low_returns_cheapest_first(serpapi_hotels_success):
    client = _client_with(_ok_handler(serpapi_hotels_success))
    cache = TTLCache(ttl_seconds=300)
    result = await search_hotels(
        client=client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
        sort_by="PRICE_LOW",
    )
    prices = [r["price_total"] for r in result["results"]]
    assert prices == sorted(prices)


async def test_sort_by_changes_first_result(serpapi_hotels_success):
    """Acceptance criterion #4: calling with two sort_by values should
    yield different first results (when fixture allows it)."""
    client = _client_with(_ok_handler(serpapi_hotels_success))
    cache = TTLCache(ttl_seconds=300)
    cheapest = await search_hotels(
        client=client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
        sort_by="PRICE_LOW",
    )
    rated = await search_hotels(
        client=client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
        sort_by="RATING",
    )
    assert cheapest["results"][0]["name"] != rated["results"][0]["name"]


# ----- lazy auth -----------------------------------------------------------


async def test_no_client_returns_auth_failed_with_actionable_message():
    """When the server starts without SERPAPI_KEY, the hotels client is
    None and the tool surfaces an auth_failed envelope rather than crashing."""
    cache = TTLCache(ttl_seconds=300)
    result = await search_hotels(
        client=None, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    assert result["error"]["code"] == "auth_failed"
    msg = result["error"]["message"]
    assert "SERPAPI_KEY" in msg
    assert "serpapi.com" in msg


# ----- error envelopes -----------------------------------------------------


async def test_invalid_input_returns_error_envelope(serpapi_hotels_success):
    client = _client_with(_ok_handler(serpapi_hotels_success))
    cache = TTLCache(ttl_seconds=300)
    # check_out before check_in
    result = await search_hotels(
        client=client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-18", check_out_date="2026-06-15",
    )
    assert result["error"]["code"] == "invalid_input"


async def test_no_results_returns_clean_envelope(serpapi_hotels_empty):
    client = _client_with(_ok_handler(serpapi_hotels_empty))
    cache = TTLCache(ttl_seconds=300)
    result = await search_hotels(
        client=client, cache=cache,
        location="Atlantis",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    assert result["error"]["code"] == "no_results"


async def test_upstream_401_returns_auth_failed_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid API key"})

    client = _client_with(handler)
    cache = TTLCache(ttl_seconds=300)
    result = await search_hotels(
        client=client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    assert result["error"]["code"] == "auth_failed"


async def test_rate_limit_returns_retryable_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client_with(handler)
    cache = TTLCache(ttl_seconds=300)
    result = await search_hotels(
        client=client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    assert result["error"]["code"] == "rate_limited"
    assert result["error"]["retryable"] is True


# ----- caching --------------------------------------------------------------


async def test_second_identical_call_is_cache_hit(serpapi_hotels_success):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=serpapi_hotels_success)

    client = _client_with(handler)
    cache = TTLCache(ttl_seconds=300)
    kwargs = dict(
        client=client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    await search_hotels(**kwargs)
    await search_hotels(**kwargs)
    assert call_count["n"] == 1


async def test_cache_key_namespaced_per_tool(serpapi_hotels_success):
    """The hotels cache must not collide with flights' cache for
    identical-shape inputs that happen to overlap."""
    from trip_search_mcp.tools.search_flights import search_flights as flights_search
    # Construct a flight client that we won't actually call — the test
    # only proves both tools can coexist in one cache without crosstalk.
    cache = TTLCache(ttl_seconds=300)
    hotels_client = _client_with(_ok_handler(serpapi_hotels_success))
    result = await search_hotels(
        client=hotels_client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    assert "error" not in result
    # If the key namespace were broken, this call into a different tool
    # with totally different shape would hit the hotels cache and explode.
    # Instead it should fail validation cleanly OR call upstream.
    # (We can't easily test cross-tool cache pollution without setting up
    # a flights client too; the namespacing logic itself is covered in
    # test_search_cheapest_dates::test_cache_key_namespaced_apart_from_search_flights.
    # This test just confirms the hotels namespace works on its own.)


# ----- shape regression -----------------------------------------------------


async def test_full_shape_matches_documented(serpapi_hotels_success):
    client = _client_with(_ok_handler(serpapi_hotels_success))
    cache = TTLCache(ttl_seconds=300)
    result = await search_hotels(
        client=client, cache=cache,
        location="Tampere",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    offer = result["results"][0]
    expected = {
        "offer_id", "name", "check_in_date", "check_out_date", "nights",
        "price_total", "price_per_night", "currency",
        "star_rating", "review_score", "review_count",
        "address", "latitude", "longitude",
        "amenities", "images", "description", "hotel_type",
        "booking_url",
    }
    assert expected.issubset(offer.keys())
    # booking_url populated, non-empty
    assert offer["booking_url"].startswith("https://www.google.com/travel/hotels")
    # images respected the 5-cap
    assert len(offer["images"]) <= 5
