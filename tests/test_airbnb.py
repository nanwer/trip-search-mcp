"""Tests for the Airbnb backend (geocoding, normalization, client orchestration).

pyairbnb is a real dependency, but we DON'T hit Airbnb in CI — the
AirbnbClient takes injectable `search_fn` and `geocode_fn` callables,
so tests substitute stubs.
"""
from __future__ import annotations

import pytest

from trip_search_mcp.airbnb_backend.client import AirbnbClient
from trip_search_mcp.airbnb_backend.normalize import normalize_listings
from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import SearchStaysInput


# ----- normalize -------------------------------------------------------------


def test_normalize_basic_listing():
    listings = [
        {
            "name": "Cozy Studio in Pyynikki",
            "room_id": 12345678,
            "coordinates": {"latitude": 61.4998, "longitude": 23.7610},
            "price": {"unit": {"amount": 95.50}},
            "rating": {"value": 4.7, "count": 124},
            "bedrooms": 1,
            "bathrooms": 1,
            "person_capacity": 2,
            "images": [
                {"picture": "https://a0.muscache.com/im/foo.jpg"},
            ],
        },
    ]
    offers = normalize_listings(
        listings, check_in="2026-06-15", check_out="2026-06-18", currency="EUR",
    )
    assert len(offers) == 1
    o = offers[0]
    assert o.name == "Cozy Studio in Pyynikki"
    assert o.offer_id == "airbnb:12345678"
    assert o.price_per_night == 95.50
    assert o.price_total == 286.5  # 95.50 × 3 nights
    assert o.category == "vacation_rental"
    assert o.review_score == 4.7
    assert o.review_count == 124
    assert o.bedrooms == 1
    assert o.bathrooms == 1
    assert o.sleeps == 2
    assert o.latitude == 61.4998
    assert o.longitude == 23.7610
    assert o.sources[0].name == "Airbnb"
    assert o.booking_url.startswith("https://www.airbnb.com/rooms/12345678")
    assert "check_in=2026-06-15" in o.booking_url
    assert o.images == ["https://a0.muscache.com/im/foo.jpg"]


def test_normalize_skips_listings_without_name_or_price():
    listings = [
        {"name": "No Price", "room_id": 1, "price": {}},
        {"room_id": 2, "price": {"unit": {"amount": 100}}},  # no name
        {"name": "Good", "room_id": 3, "price": {"unit": {"amount": 50}}},
    ]
    offers = normalize_listings(
        listings, check_in="2026-06-15", check_out="2026-06-18", currency="EUR",
    )
    assert len(offers) == 1
    assert offers[0].name == "Good"


def test_normalize_handles_alternative_price_keys():
    """pyairbnb's price structure has drifted between versions; the
    normalizer should accept either nested or flat numeric keys."""
    listings = [
        {"name": "A", "room_id": 1, "price_per_night": 80.0},
        {"name": "B", "room_id": 2, "price": {"amount": 90.0}},
    ]
    offers = normalize_listings(
        listings, check_in="2026-06-15", check_out="2026-06-18", currency="EUR",
    )
    prices = sorted(o.price_per_night for o in offers)
    assert prices == [80.0, 90.0]


# ----- client orchestration --------------------------------------------------


def _input(**overrides) -> SearchStaysInput:
    base = dict(
        location="Tampere",
        check_in_date="2026-06-15",
        check_out_date="2026-06-18",
        category="airbnb",
    )
    base.update(overrides)
    return SearchStaysInput(**base)


async def test_client_threads_geocoded_bbox_to_pyairbnb():
    """The bbox returned by the geocoder should appear verbatim in the
    call to pyairbnb.search_all."""
    captured_args = {}

    async def stub_geocode(location, *, http=None):
        return (61.55, 23.85, 61.45, 23.65)  # NE, SW

    def stub_search(**kwargs):
        captured_args.update(kwargs)
        return [
            {
                "name": "Stub",
                "room_id": 1,
                "price": {"unit": {"amount": 100}},
            },
        ]

    client = AirbnbClient(geocode_fn=stub_geocode, search_fn=stub_search)
    offers = await client.search(_input())
    assert len(offers) == 1
    assert captured_args["ne_lat"] == 61.55
    assert captured_args["ne_long"] == 23.85
    assert captured_args["sw_lat"] == 61.45
    assert captured_args["sw_long"] == 23.65
    assert captured_args["check_in"] == "2026-06-15"
    assert captured_args["currency"] == "EUR"


async def test_client_routes_min_bedrooms_to_pyairbnb_native_param():
    captured = {}

    async def stub_geocode(location, *, http=None):
        return (1.0, 1.0, 0.0, 0.0)

    def stub_search(**kwargs):
        captured.update(kwargs)
        return [{"name": "X", "room_id": 1, "price": {"unit": {"amount": 100}}}]

    client = AirbnbClient(geocode_fn=stub_geocode, search_fn=stub_search)
    await client.search(_input(min_bedrooms=2, min_bathrooms=1))
    assert captured["min_bedrooms"] == 2
    assert captured["min_bathrooms"] == 1


async def test_client_post_filters_review_score():
    """min_review_score has no pyairbnb native param; the client filters
    post-fetch."""
    async def stub_geocode(location, *, http=None):
        return (1.0, 1.0, 0.0, 0.0)

    def stub_search(**kwargs):
        return [
            {"name": "High", "room_id": 1, "price": {"unit": {"amount": 100}},
             "rating": {"value": 4.8, "count": 50}},
            {"name": "Low", "room_id": 2, "price": {"unit": {"amount": 50}},
             "rating": {"value": 3.5, "count": 20}},
        ]

    client = AirbnbClient(geocode_fn=stub_geocode, search_fn=stub_search)
    offers = await client.search(_input(min_review_score=4.5))
    assert len(offers) == 1
    assert offers[0].name == "High"


async def test_client_empty_listings_raises_no_results():
    async def stub_geocode(location, *, http=None):
        return (1.0, 1.0, 0.0, 0.0)

    def stub_search(**kwargs):
        return []

    client = AirbnbClient(geocode_fn=stub_geocode, search_fn=stub_search)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_client_translates_pyairbnb_exception_to_upstream_error():
    async def stub_geocode(location, *, http=None):
        return (1.0, 1.0, 0.0, 0.0)

    def stub_search(**kwargs):
        raise RuntimeError("Airbnb blocked the request (CAPTCHA)")

    client = AirbnbClient(geocode_fn=stub_geocode, search_fn=stub_search)
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
    assert exc.value.retryable is True


async def test_client_geocode_failure_propagates_invalid_input():
    async def stub_geocode(location, *, http=None):
        raise ToolError(
            ErrorCode.INVALID_INPUT,
            f"Couldn't find {location!r} on the map.",
            retryable=False,
        )

    def stub_search(**kwargs):
        raise AssertionError("search_fn should not be called when geocode fails")

    client = AirbnbClient(geocode_fn=stub_geocode, search_fn=stub_search)
    with pytest.raises(ToolError) as exc:
        await client.search(_input(location="Atlantis-The-Lost-City"))
    assert exc.value.code is ErrorCode.INVALID_INPUT
