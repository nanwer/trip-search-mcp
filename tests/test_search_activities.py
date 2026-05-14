"""Tests for the Tripadvisor backend + search_activities tool.

Uses Phase 0 fixtures (real Lisbon results) for shape parity plus
httpx.MockTransport for orchestration. No live SerpAPI calls in CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trip_search_mcp.cache import TTLCache
from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import (
    ActivityType,
    PlaceTypeFilter,
    SearchActivitiesInput,
)
from trip_search_mcp.tools.search_activities import search_activities
from trip_search_mcp.tripadvisor_backend.client import SerpAPITripadvisorClient
from trip_search_mcp.tripadvisor_backend.normalize import (
    _map_place_type,
    build_offers,
)
from trip_search_mcp.tripadvisor_backend.raw import SerpTripadvisorResponse

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cooking_fixture() -> dict:
    return json.loads((FIXTURES / "serpapi_tripadvisor_lisbon_cooking.json").read_text())


@pytest.fixture
def generic_fixture() -> dict:
    return json.loads((FIXTURES / "serpapi_tripadvisor_lisbon_generic.json").read_text())


# ----- place_type mapping ---------------------------------------------------


def test_map_place_type_attraction_product_is_experience():
    assert _map_place_type("ATTRACTION_PRODUCT") is ActivityType.EXPERIENCE


def test_map_place_type_attraction_is_sight():
    assert _map_place_type("ATTRACTION") is ActivityType.SIGHT


def test_map_place_type_unknown_falls_back_to_sight():
    """Conservative default: anything we don't recognize is a "sight"
    (no Viator URL expected)."""
    assert _map_place_type("FOOD_AND_DRINK") is ActivityType.SIGHT
    assert _map_place_type(None) is ActivityType.SIGHT


# ----- build_offers from real fixture ---------------------------------------


def test_build_offers_normalizes_cooking_fixture(cooking_fixture):
    parsed = SerpTripadvisorResponse.model_validate(cooking_fixture)
    offers = build_offers(
        parsed, place_type_filter=PlaceTypeFilter.BOTH,
        min_rating=None, limit=50,
    )
    assert len(offers) > 0
    first = offers[0]
    assert first.name
    assert first.offer_id
    assert first.booking_url.startswith("https://www.tripadvisor")
    assert first.activity_type in (ActivityType.SIGHT, ActivityType.EXPERIENCE)


def test_build_offers_filters_to_experiences_only(cooking_fixture):
    """Phase 0 showed lisbon_cooking has 24 ATTRACTION + 6 ATTRACTION_PRODUCT."""
    parsed = SerpTripadvisorResponse.model_validate(cooking_fixture)
    offers = build_offers(
        parsed, place_type_filter=PlaceTypeFilter.EXPERIENCES,
        min_rating=None, limit=50,
    )
    assert all(o.activity_type is ActivityType.EXPERIENCE for o in offers)
    assert 1 <= len(offers) <= 10  # fixture has 6 ATTRACTION_PRODUCT


def test_build_offers_filters_to_sights_only(cooking_fixture):
    parsed = SerpTripadvisorResponse.model_validate(cooking_fixture)
    offers = build_offers(
        parsed, place_type_filter=PlaceTypeFilter.SIGHTS,
        min_rating=None, limit=50,
    )
    assert all(o.activity_type is ActivityType.SIGHT for o in offers)


def test_build_offers_min_rating_drops_low_and_unrated():
    """Verify min_rating filter on a synthesized fixture (real fixture
    has rating on every entry)."""
    parsed = SerpTripadvisorResponse.model_validate({
        "places": [
            {"title": "A", "place_id": "1", "place_type": "ATTRACTION",
             "link": "https://t.com/a", "rating": 4.8},
            {"title": "B", "place_id": "2", "place_type": "ATTRACTION",
             "link": "https://t.com/b", "rating": 3.5},
            {"title": "C", "place_id": "3", "place_type": "ATTRACTION",
             "link": "https://t.com/c"},   # no rating
        ],
    })
    offers = build_offers(
        parsed, place_type_filter=PlaceTypeFilter.BOTH,
        min_rating=4.0, limit=50,
    )
    assert len(offers) == 1
    assert offers[0].name == "A"


def test_build_offers_skips_results_without_essentials():
    parsed = SerpTripadvisorResponse.model_validate({
        "places": [
            {"title": "No place_id", "place_type": "ATTRACTION",
             "link": "https://t.com/x"},
            {"place_id": "1", "place_type": "ATTRACTION",
             "link": "https://t.com/x"},   # no title
            {"title": "Z", "place_id": "2", "place_type": "ATTRACTION"},  # no link
            {"title": "Good", "place_id": "9",
             "place_type": "ATTRACTION", "link": "https://t.com/g"},
        ],
    })
    offers = build_offers(
        parsed, place_type_filter=PlaceTypeFilter.BOTH,
        min_rating=None, limit=10,
    )
    assert len(offers) == 1
    assert offers[0].name == "Good"


def test_build_offers_caps_at_limit(cooking_fixture):
    parsed = SerpTripadvisorResponse.model_validate(cooking_fixture)
    offers = build_offers(
        parsed, place_type_filter=PlaceTypeFilter.BOTH,
        min_rating=None, limit=5,
    )
    assert len(offers) == 5


def test_build_offers_carries_highlighted_review(cooking_fixture):
    parsed = SerpTripadvisorResponse.model_validate(cooking_fixture)
    offers = build_offers(
        parsed, place_type_filter=PlaceTypeFilter.BOTH,
        min_rating=None, limit=5,
    )
    # First result in the fixture has a highlighted_review.
    assert offers[0].highlighted_review is not None
    assert offers[0].highlighted_review.text


# ----- input model ----------------------------------------------------------


def test_input_requires_location():
    with pytest.raises(Exception):
        SearchActivitiesInput.model_validate({})


def test_input_defaults():
    m = SearchActivitiesInput.model_validate({"location": "Lisbon"})
    assert m.place_type_filter is PlaceTypeFilter.BOTH
    assert m.min_rating is None
    assert m.max_results == 15


def test_input_place_type_enum_coercion():
    m = SearchActivitiesInput.model_validate({
        "location": "Lisbon", "place_type_filter": "experiences",
    })
    assert m.place_type_filter is PlaceTypeFilter.EXPERIENCES


def test_input_rejects_invalid_place_type_filter():
    with pytest.raises(Exception):
        SearchActivitiesInput.model_validate({
            "location": "Lisbon", "place_type_filter": "concerts",  # not valid
        })


def test_input_rejects_min_rating_out_of_bounds():
    with pytest.raises(Exception):
        SearchActivitiesInput.model_validate({
            "location": "Lisbon", "min_rating": 6.0,
        })
    with pytest.raises(Exception):
        SearchActivitiesInput.model_validate({
            "location": "Lisbon", "min_rating": -0.5,
        })


# ----- client orchestration -------------------------------------------------


def _make_client(handler) -> SerpAPITripadvisorClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SerpAPITripadvisorClient(http=http, api_key="fake-key")


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _input(**overrides) -> SearchActivitiesInput:
    base = {"location": "Lisbon"}
    base.update(overrides)
    return SearchActivitiesInput.model_validate(base)


async def test_client_builds_query_with_ssrc_A(cooking_fixture):
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(dict(req.url.params))
        return _ok(cooking_fixture)

    client = _make_client(handler)
    await client.search(_input(query="cooking class"))
    assert captured["engine"] == "tripadvisor"
    assert captured["ssrc"] == "A"
    assert captured["q"] == "cooking class Lisbon"


async def test_client_q_string_when_no_query(cooking_fixture):
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(dict(req.url.params))
        return _ok(cooking_fixture)

    client = _make_client(handler)
    await client.search(_input())
    assert captured["q"] == "Lisbon"


async def test_client_empty_places_raises_no_results():
    client = _make_client(lambda req: _ok({"places": []}))
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_client_401_maps_to_auth_failed():
    client = _make_client(lambda req: httpx.Response(401, json={"error": "Invalid API key"}))
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.AUTH_FAILED


async def test_client_429_maps_to_rate_limited():
    client = _make_client(lambda req: httpx.Response(429))
    with pytest.raises(ToolError) as exc:
        await client.search(_input())
    assert exc.value.code is ErrorCode.RATE_LIMITED


async def test_client_all_filtered_out_raises_no_results():
    """Synthesized response with all low-rated entries → min_rating
    filters out everything → NO_RESULTS with the 'filtered out' hint."""
    low_rated = {
        "places": [
            {"title": "Bad A", "place_id": "1", "place_type": "ATTRACTION",
             "link": "https://t.com/a", "rating": 2.5},
            {"title": "Bad B", "place_id": "2", "place_type": "ATTRACTION",
             "link": "https://t.com/b", "rating": 3.0},
        ],
    }
    client = _make_client(lambda req: _ok(low_rated))
    with pytest.raises(ToolError) as exc:
        await client.search(_input(min_rating=4.5))
    assert exc.value.code is ErrorCode.NO_RESULTS
    assert "filtered out" in exc.value.message.lower()


# ----- tool function: orchestration -----------------------------------------


async def test_tool_returns_success_envelope(cooking_fixture):
    client = _make_client(lambda req: _ok(cooking_fixture))
    cache = TTLCache(ttl_seconds=300)
    result = await search_activities(
        client=client, cache=cache, location="Lisbon",
    )
    assert "error" not in result
    assert len(result["results"]) > 0


async def test_tool_lazy_auth_failure_without_client():
    cache = TTLCache(ttl_seconds=300)
    result = await search_activities(
        client=None, cache=cache, location="Lisbon",
    )
    assert result["error"]["code"] == "auth_failed"


async def test_tool_invalid_input_envelope(cooking_fixture):
    client = _make_client(lambda req: _ok(cooking_fixture))
    cache = TTLCache(ttl_seconds=300)
    result = await search_activities(
        client=client, cache=cache,
        location="Lisbon", place_type_filter="concerts",  # invalid
    )
    assert result["error"]["code"] == "invalid_input"


async def test_tool_caches_repeat_calls(cooking_fixture):
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return _ok(cooking_fixture)

    client = _make_client(handler)
    cache = TTLCache(ttl_seconds=300)
    kwargs = dict(
        client=client, cache=cache, location="Lisbon", query="cooking",
    )
    await search_activities(**kwargs)
    await search_activities(**kwargs)
    assert call_count["n"] == 1
