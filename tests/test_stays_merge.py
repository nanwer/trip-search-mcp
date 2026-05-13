"""Tests for the category dispatcher, parallel fanout, dedup, and
partial-failure warnings path in SerpAPIHotelsClient.

The mock transport routes by inspecting `vacation_rentals` in the
outgoing request — that's how we can serve different responses on the
two parallel calls without coordinating between them.
"""
from __future__ import annotations

import httpx
import pytest

from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import SearchStaysInput
from trip_search_mcp.serpapi_hotels_backend.client import SerpAPIHotelsClient


def _input(**overrides) -> SearchStaysInput:
    base = dict(
        location="Tampere",
        check_in_date="2026-06-15",
        check_out_date="2026-06-18",
    )
    base.update(overrides)
    return SearchStaysInput(**base)


def _routed_handler(hotels_response: dict, rentals_response: dict):
    """Returns a handler that picks the response by the outgoing
    `vacation_rentals` query parameter. Used to simulate the merge path
    where the two parallel calls need different bodies."""
    def handler(request: httpx.Request) -> httpx.Response:
        is_rentals = request.url.params.get("vacation_rentals") == "true"
        body = rentals_response if is_rentals else hotels_response
        return httpx.Response(200, json=body)
    return handler


def _client_with(handler) -> SerpAPIHotelsClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SerpAPIHotelsClient(http=http, api_key="fake-key")


# ----- category dispatch ----------------------------------------------------


async def test_category_hotels_makes_one_call_with_vacation_rentals_false(
    serpapi_hotels_success,
):
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        return httpx.Response(200, json=serpapi_hotels_success)

    client = _client_with(handler)
    await client.search(_input(category="hotels"))
    assert len(requests) == 1
    assert requests[0]["vacation_rentals"] == "false"


async def test_category_vacation_rentals_makes_one_call_with_vacation_rentals_true(
    serpapi_vacation_rentals_success,
):
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        return httpx.Response(200, json=serpapi_vacation_rentals_success)

    client = _client_with(handler)
    await client.search(_input(category="vacation_rentals"))
    assert len(requests) == 1
    assert requests[0]["vacation_rentals"] == "true"


async def test_category_all_makes_two_calls_in_parallel(
    serpapi_hotels_success, serpapi_vacation_rentals_success,
):
    requests: list[dict] = []
    handler = _routed_handler(serpapi_hotels_success, serpapi_vacation_rentals_success)

    def tracking_handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        return handler(request)

    client = _client_with(tracking_handler)
    result = await client.search(_input(category="all"))
    assert len(requests) == 2
    modes = {r["vacation_rentals"] for r in requests}
    assert modes == {"true", "false"}
    # Merged results: 3 hotels + 3 rentals, all distinct property_tokens.
    assert len(result.results) == 6
    assert result.warnings == []


# ----- filter scoping at request-build time --------------------------------


async def test_min_bedrooms_routed_only_to_rentals_request(
    serpapi_hotels_success, serpapi_vacation_rentals_success,
):
    """Phase 0 regression: SerpAPI returns HTTP 400 if `bedrooms` is sent
    with vacation_rentals=false. The client must NOT include it on the
    hotel call."""
    requests: list[dict] = []
    base_handler = _routed_handler(serpapi_hotels_success, serpapi_vacation_rentals_success)

    def tracking_handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        return base_handler(request)

    client = _client_with(tracking_handler)
    await client.search(_input(category="all", min_bedrooms=2, min_bathrooms=1))

    hotels_req = next(r for r in requests if r["vacation_rentals"] == "false")
    rentals_req = next(r for r in requests if r["vacation_rentals"] == "true")
    assert "bedrooms" not in hotels_req
    assert "bathrooms" not in hotels_req
    assert rentals_req["bedrooms"] == "2"
    assert rentals_req["bathrooms"] == "1"


async def test_min_rating_does_not_filter_out_rentals_in_merged_mode(
    serpapi_hotels_success, serpapi_vacation_rentals_success,
):
    """`min_rating` only applies to the hotel side. Rentals have no
    star_rating; if we routed min_rating to their filter, they'd all be
    excluded. Verify they pass through."""
    handler = _routed_handler(serpapi_hotels_success, serpapi_vacation_rentals_success)
    client = _client_with(handler)
    result = await client.search(_input(category="all", min_rating=4))
    # Rentals (no star_rating) must still appear in the merged result.
    rental_names = {o.name for o in result.results if o.category == "vacation_rental"}
    assert "Modern 2BR Apartment near Pyynikki" in rental_names


# ----- merge + dedup --------------------------------------------------------


async def test_merge_dedups_by_property_token(serpapi_hotels_success):
    """Same property_token returned by both sides collapses to one offer
    at the lower price."""
    # Synthesize: same fixture on both sides. Identical tokens → dedup.
    handler = _routed_handler(serpapi_hotels_success, serpapi_hotels_success)
    client = _client_with(handler)
    result = await client.search(_input(category="all"))
    # 3 unique tokens in the fixture → 3 results after dedup of 6 candidates.
    assert len(result.results) == 3


async def test_merge_dedup_keeps_lower_price(serpapi_hotels_success):
    """When duplicates collide, the lower-priced variant wins."""
    import copy
    hotels = copy.deepcopy(serpapi_hotels_success)
    rentals = copy.deepcopy(serpapi_hotels_success)
    # Halve the per-night price on the rentals-side fixture so its
    # variant becomes the winner under dedup.
    for prop in rentals["properties"]:
        if prop.get("rate_per_night", {}).get("extracted_lowest"):
            prop["rate_per_night"]["extracted_lowest"] /= 2
            if prop.get("total_rate", {}).get("extracted_lowest"):
                prop["total_rate"]["extracted_lowest"] /= 2

    handler = _routed_handler(hotels, rentals)
    client = _client_with(handler)
    result = await client.search(_input(category="all"))
    # All 3 should be deduped; each kept variant should be the half-priced one.
    assert len(result.results) == 3
    for offer in result.results:
        # Sanity: prices got halved, so they should all be < 200 (original 4-star
        # rate_per_night=111 → halved ~55; 5-star 240 → 120, etc.)
        assert offer.price_per_night < 200


async def test_sort_applies_to_merged_set(
    serpapi_hotels_success, serpapi_vacation_rentals_success,
):
    handler = _routed_handler(serpapi_hotels_success, serpapi_vacation_rentals_success)
    client = _client_with(handler)
    result = await client.search(_input(category="all", sort_by="PRICE_LOW"))
    prices = [o.price_total for o in result.results]
    assert prices == sorted(prices)


async def test_merged_truncation_after_dedup(
    serpapi_hotels_success, serpapi_vacation_rentals_success,
):
    """6 candidates (3 hotels + 3 rentals, no token overlap) → truncate to 4."""
    handler = _routed_handler(serpapi_hotels_success, serpapi_vacation_rentals_success)
    client = _client_with(handler)
    result = await client.search(_input(category="all", max_results=4))
    assert len(result.results) == 4


# ----- partial-failure path -------------------------------------------------


async def test_partial_failure_returns_warnings_on_hotel_side_500(
    serpapi_vacation_rentals_success,
):
    """Hotel side returns 500, rental side succeeds → success envelope with
    rentals + a warning about the hotel-side failure."""
    def handler(request: httpx.Request) -> httpx.Response:
        is_rentals = request.url.params.get("vacation_rentals") == "true"
        if is_rentals:
            return httpx.Response(200, json=serpapi_vacation_rentals_success)
        return httpx.Response(503)

    client = _client_with(handler)
    result = await client.search(_input(category="all"))
    # Should have the 3 rental results.
    assert len(result.results) == 3
    assert all(o.category == "vacation_rental" for o in result.results)
    # And a single warning.
    assert len(result.warnings) == 1
    assert "hotel" in result.warnings[0].lower()


async def test_partial_failure_returns_warnings_on_rental_side_500(
    serpapi_hotels_success,
):
    """Mirror: rental side errors, hotels succeed. (The hotels fixture is
    mixed-type — one Backpackers entry is typed 'vacation rental' — so we
    don't assert per-result category, only that we got the hotel-side
    fixture's 3 properties and a single warning about the rental side.)"""
    def handler(request: httpx.Request) -> httpx.Response:
        is_rentals = request.url.params.get("vacation_rentals") == "true"
        if is_rentals:
            return httpx.Response(503)
        return httpx.Response(200, json=serpapi_hotels_success)

    client = _client_with(handler)
    result = await client.search(_input(category="all"))
    assert len(result.results) == 3
    assert len(result.warnings) == 1
    assert "vacation rental" in result.warnings[0].lower()


async def test_both_sides_fail_raises_tool_error(serpapi_hotels_success):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _client_with(handler)
    with pytest.raises(ToolError) as exc:
        await client.search(_input(category="all"))
    # Either side's error is fine; client prefers the hotel-side message on tie.
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
