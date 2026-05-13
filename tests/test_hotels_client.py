"""Tests for serpapi_hotels_backend.client.

Inject httpx.MockTransport so nothing hits SerpAPI's live endpoint.
"""
from __future__ import annotations

import httpx
import pytest

from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import HotelSortBy, SearchHotelsInput
from trip_search_mcp.serpapi_hotels_backend.client import SerpAPIHotelsClient


def _make_client(handler) -> SerpAPIHotelsClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SerpAPIHotelsClient(http=http, api_key="fake-key")


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _input(**overrides) -> SearchHotelsInput:
    base = dict(
        location="Tampere",
        check_in_date="2026-06-15",
        check_out_date="2026-06-18",
    )
    base.update(overrides)
    return SearchHotelsInput(**base)


# ----- happy path -----------------------------------------------------------


async def test_search_returns_normalized_offers(serpapi_hotels_success):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return _ok(serpapi_hotels_success)

    client = _make_client(handler)
    offers = await client.search(_input())
    assert len(offers) == 3
    # SerpAPI request shape:
    assert captured["params"]["engine"] == "google_hotels"
    assert captured["params"]["q"] == "Tampere"
    assert captured["params"]["check_in_date"] == "2026-06-15"
    assert captured["params"]["check_out_date"] == "2026-06-18"
    assert captured["params"]["adults"] == "2"
    assert captured["params"]["currency"] == "EUR"
    assert captured["params"]["api_key"] == "fake-key"


async def test_currency_threads_through_to_request_and_response(serpapi_hotels_success):
    """`currency` is now a per-call input. The same value must end up in the
    outbound SerpAPI request AND in every returned offer's `currency` field."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return _ok(serpapi_hotels_success)

    client = _make_client(handler)
    offers = await client.search(_input(currency="JPY"))
    assert captured["params"]["currency"] == "JPY"
    assert all(o.currency == "JPY" for o in offers)


async def test_currency_defaults_to_eur(serpapi_hotels_success):
    """Omitting `currency` falls back to EUR — matches the flights default
    for European-IP users so trip-cost comparisons remain direct."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return _ok(serpapi_hotels_success)

    client = _make_client(handler)
    offers = await client.search(_input())
    assert captured["params"]["currency"] == "EUR"
    assert all(o.currency == "EUR" for o in offers)


async def test_max_results_caps_response(serpapi_hotels_success):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(serpapi_hotels_success)

    client = _make_client(handler)
    offers = await client.search(_input(max_results=2))
    assert len(offers) == 2


async def test_sort_by_threads_through(serpapi_hotels_success):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(serpapi_hotels_success)

    client = _make_client(handler)
    offers = await client.search(_input(sort_by="PRICE_LOW"))
    prices = [o.price_total for o in offers]
    assert prices == sorted(prices)


# ----- empty / filtered-out -------------------------------------------------


async def test_empty_response_raises_no_results(serpapi_hotels_empty):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(serpapi_hotels_empty)

    client = _make_client(handler)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_all_filtered_out_raises_no_results_with_actionable_message(
    serpapi_hotels_success,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(serpapi_hotels_success)

    client = _make_client(handler)
    with pytest.raises(ToolError) as exc:
        await client.search(_input(max_price_per_night=1.0))  # everyone fails
    assert exc.value.code is ErrorCode.NO_RESULTS
    # The message specifically calls out the filter, not "SerpAPI returned nothing".
    assert "filtered out" in exc.value.message.lower()


# ----- error mapping ---------------------------------------------------------


async def test_401_maps_to_auth_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid API key"})

    client = _make_client(handler)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.AUTH_FAILED


async def test_429_maps_to_rate_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Too Many Requests")

    client = _make_client(handler)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.RATE_LIMITED
    assert exc.value.retryable is True


async def test_5xx_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _make_client(handler)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR


async def test_body_error_invalid_key_maps_to_auth_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"error": "Invalid API key. Your API key should be here: ..."})

    client = _make_client(handler)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.AUTH_FAILED


async def test_body_error_quota_maps_to_upstream_error():
    """SerpAPI quota messages were previously mapped to a dedicated
    QUOTA_EXCEEDED code; that code was retired with the fli flights
    migration. Quota messages now fold into UPSTREAM_ERROR retryable=false."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"error": "Your account has run out of searches for the month."})

    client = _make_client(handler)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
    assert exc.value.retryable is False


async def test_malformed_body_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    client = _make_client(handler)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
    assert exc.value.retryable is True
