"""Tests for the SerpAPI events backend + search_events tool.

Uses the Phase 0 fixtures (real Google Events captures for Lisbon and
Paris) for shape parity plus httpx.MockTransport for orchestration.
No live SerpAPI calls in CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trip_search_mcp.cache import TTLCache
from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import EventDateFilter, SearchEventsInput
from trip_search_mcp.serpapi_events_backend.client import SerpAPIEventsClient
from trip_search_mcp.serpapi_events_backend.normalize import (
    _compute_offer_id,
    _flatten_address,
    build_offers,
)
from trip_search_mcp.serpapi_events_backend.raw import SerpEventsResponse
from trip_search_mcp.tools.search_events import search_events

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def lisbon_generic() -> dict:
    return json.loads((FIXTURES / "serpapi_events_lisbon_generic.json").read_text())


@pytest.fixture
def paris_bts() -> dict:
    return json.loads((FIXTURES / "serpapi_events_paris_bts_july.json").read_text())


@pytest.fixture
def lisbon_concerts() -> dict:
    return json.loads((FIXTURES / "serpapi_events_lisbon_concerts_june.json").read_text())


# ----- normalize ------------------------------------------------------------


def test_flatten_address_joins_with_commas():
    assert _flatten_address(["B.Leza Club, Cais do Gás 1", "Lisbon, Portugal"]) \
        == "B.Leza Club, Cais do Gás 1, Lisbon, Portugal"


def test_flatten_address_strips_and_dedupes_empty():
    assert _flatten_address(["", "Real Place", "   "]) == "Real Place"
    assert _flatten_address([]) is None
    assert _flatten_address(None) is None


def test_offer_id_is_deterministic():
    a = _compute_offer_id(title="BTS", start_date="Jul 17", venue_name="Stade de France")
    b = _compute_offer_id(title="BTS", start_date="Jul 17", venue_name="Stade de France")
    assert a == b
    assert a.startswith("ev:")


def test_offer_id_differs_with_different_inputs():
    base = dict(title="BTS", start_date="Jul 17", venue_name="Stade de France")
    assert _compute_offer_id(**base) != _compute_offer_id(**{**base, "title": "BTS 2"})
    assert _compute_offer_id(**base) != _compute_offer_id(**{**base, "venue_name": "Other"})


def test_build_offers_normalizes_real_fixture(lisbon_generic):
    parsed = SerpEventsResponse.model_validate(lisbon_generic)
    offers = build_offers(parsed, limit=20)
    assert len(offers) > 0
    first = offers[0]
    assert first.title
    assert first.ticket_url.startswith("http")
    assert first.offer_id.startswith("ev:")


def test_build_offers_caps_at_limit(lisbon_generic):
    parsed = SerpEventsResponse.model_validate(lisbon_generic)
    offers = build_offers(parsed, limit=3)
    assert len(offers) == 3


def test_build_offers_carries_ticket_sources(lisbon_generic):
    """Phase 0 confirmed events surface ticket_info with multiple OTAs.
    Ensure we preserve them."""
    parsed = SerpEventsResponse.model_validate(lisbon_generic)
    offers = build_offers(parsed, limit=20)
    # The Fogo Fogo fixture event has Viagogo + StubHub sources.
    targeted = next((o for o in offers if "Fogo" in o.title), None)
    if targeted is not None:
        names = {s.source for s in targeted.ticket_sources}
        # Don't assert exact set (SerpAPI varies) — just that ≥1 source threaded through.
        assert names


def test_build_offers_skips_events_missing_title_or_link():
    """Defensive: events without a title or actionable link are dropped."""
    parsed = SerpEventsResponse.model_validate({
        "events_results": [
            {"title": "Has no link"},
            {"link": "https://example.com/no-title"},
            {"title": "Good", "link": "https://example.com/good"},
        ],
    })
    offers = build_offers(parsed, limit=10)
    assert len(offers) == 1
    assert offers[0].title == "Good"


def test_build_offers_dedupes_within_response():
    """Same offer_id appearing twice collapses to one row."""
    same_event = {
        "title": "Dup", "link": "https://example.com/dup",
        "date": {"start_date": "May 15"},
        "venue": {"name": "V"},
    }
    parsed = SerpEventsResponse.model_validate({
        "events_results": [same_event, same_event],
    })
    offers = build_offers(parsed, limit=10)
    assert len(offers) == 1


# ----- input model ----------------------------------------------------------


def test_input_requires_location():
    with pytest.raises(Exception):
        SearchEventsInput.model_validate({})


def test_input_accepts_date_filter_enum():
    m = SearchEventsInput.model_validate({
        "location": "Lisbon", "date_filter": "next_week",
    })
    assert m.date_filter is EventDateFilter.NEXT_WEEK


def test_input_rejects_invalid_date_filter():
    with pytest.raises(Exception):
        SearchEventsInput.model_validate({
            "location": "Lisbon", "date_filter": "yesterday",  # not a valid enum value
        })


def test_input_max_results_bounded():
    with pytest.raises(Exception):
        SearchEventsInput.model_validate({"location": "Lisbon", "max_results": 0})
    with pytest.raises(Exception):
        SearchEventsInput.model_validate({"location": "Lisbon", "max_results": 51})


# ----- client orchestration -------------------------------------------------


def _make_client(handler) -> SerpAPIEventsClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SerpAPIEventsClient(http=http, api_key="fake-key")


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _input(**overrides) -> SearchEventsInput:
    base = {"location": "Lisbon"}
    base.update(overrides)
    return SearchEventsInput.model_validate(base)


async def test_client_q_string_combines_query_and_location(lisbon_concerts):
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(dict(req.url.params))
        return _ok(lisbon_concerts)

    client = _make_client(handler)
    await client.search(_input(query="Concerts"))
    assert captured["q"] == "Concerts in Lisbon"
    assert captured["engine"] == "google_events"


async def test_client_q_defaults_to_events_in_location(lisbon_generic):
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(dict(req.url.params))
        return _ok(lisbon_generic)

    client = _make_client(handler)
    await client.search(_input())
    assert captured["q"] == "Events in Lisbon"


async def test_client_date_filter_threads_through_as_htichips(lisbon_generic):
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(dict(req.url.params))
        return _ok(lisbon_generic)

    client = _make_client(handler)
    await client.search(_input(date_filter="next_week"))
    assert captured["htichips"] == "date:next_week"


async def test_client_no_date_filter_omits_htichips(lisbon_generic):
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(dict(req.url.params))
        return _ok(lisbon_generic)

    client = _make_client(handler)
    await client.search(_input())
    assert "htichips" not in captured


async def test_client_empty_events_raises_no_results():
    client = _make_client(lambda req: _ok({"events_results": []}))
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


# ----- tool function: orchestration -----------------------------------------


async def test_tool_returns_success_envelope(lisbon_generic):
    client = _make_client(lambda req: _ok(lisbon_generic))
    cache = TTLCache(ttl_seconds=300)
    result = await search_events(
        client=client, cache=cache,
        location="Lisbon",
    )
    assert "error" not in result
    assert len(result["results"]) > 0
    assert "ticket_url" in result["results"][0]


async def test_tool_lazy_auth_failure_without_client():
    cache = TTLCache(ttl_seconds=300)
    result = await search_events(
        client=None, cache=cache, location="Lisbon",
    )
    assert result["error"]["code"] == "auth_failed"


async def test_tool_invalid_input_envelope(lisbon_generic):
    client = _make_client(lambda req: _ok(lisbon_generic))
    cache = TTLCache(ttl_seconds=300)
    result = await search_events(
        client=client, cache=cache, location="",  # empty location
    )
    assert result["error"]["code"] == "invalid_input"


async def test_tool_caches_repeat_calls(lisbon_generic):
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return _ok(lisbon_generic)

    client = _make_client(handler)
    cache = TTLCache(ttl_seconds=300)
    kwargs = dict(client=client, cache=cache, location="Lisbon")
    await search_events(**kwargs)
    await search_events(**kwargs)
    assert call_count["n"] == 1
