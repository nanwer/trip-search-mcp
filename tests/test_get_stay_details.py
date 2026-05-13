"""Tests for the get_stay_details tool and the property_details client method.

Uses the Phase 0 fixture (real Tampere property_details response) for
shape parity, plus a stub for orchestration concerns (auth check,
caching, input validation).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from trip_search_mcp.cache import TTLCache
from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import GetStayDetailsInput
from trip_search_mcp.serpapi_hotels_backend.client import SerpAPIHotelsClient
from trip_search_mcp.tools.get_stay_details import get_stay_details

FIXTURE = Path(__file__).parent / "fixtures" / "serpapi_property_details_tampere.json"


@pytest.fixture
def details_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def _make_client(handler) -> SerpAPIHotelsClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SerpAPIHotelsClient(http=http, api_key="fake-key")


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _input(**overrides) -> GetStayDetailsInput:
    base = dict(
        property_token="ChoQ7cre3vCRr9naARoNL2cvMTF6MjFqODVqcBAC",
        check_in_date="2026-06-15",
        check_out_date="2026-06-18",
    )
    base.update(overrides)
    return GetStayDetailsInput(**base)


# ----- client method: normalization end-to-end ------------------------------


async def test_get_property_details_normalizes_full_fixture(details_fixture):
    """Real Phase 0 fixture round-trips through the parser + normalizer."""
    client = _make_client(lambda req: _ok(details_fixture))
    details = await client.get_property_details(_input())

    assert details.name == "Luxurious 7th Floor Penthouse | Private Jacuzzi & Sauna"
    assert details.category == "vacation_rental"
    # Long-form description present.
    assert details.description and len(details.description) > 100
    # Per-Phase-0 verification: 4 booking partners, all carrying links.
    assert len(details.booking_partners) == 4
    for p in details.booking_partners:
        assert p.link and p.link.startswith("https://www.google.com/travel/clk?")
    # Nearby places: should be the rich list, not the truncated search-time one.
    assert len(details.nearby_places) >= 10


async def test_get_property_details_canonicalizes_partner_names(details_fixture):
    """Same canonical-name map as search-time sources."""
    client = _make_client(lambda req: _ok(details_fixture))
    details = await client.get_property_details(_input())
    names = {p.name for p in details.booking_partners}
    # Fixture has Expedia.com, Hotels.com, Vrbo.com, plus the property's own.
    assert "Expedia" in names
    assert "Hotels.com" in names
    assert "VRBO" in names


async def test_get_property_details_threads_check_in_to_request(details_fixture):
    """Verify check_in_date / property_token actually appear in the
    outgoing SerpAPI request."""
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["params"] = dict(req.url.params)
        return _ok(details_fixture)

    client = _make_client(handler)
    await client.get_property_details(_input(
        property_token="TOKEN_XYZ",
        check_in_date="2026-07-01",
        check_out_date="2026-07-05",
    ))
    assert captured["params"]["property_token"] == "TOKEN_XYZ"
    assert captured["params"]["check_in_date"] == "2026-07-01"
    assert captured["params"]["check_out_date"] == "2026-07-05"


async def test_get_property_details_empty_response_raises_no_results():
    """SerpAPI returns 200 but the property doesn't exist."""
    client = _make_client(lambda req: _ok({}))  # no `name` → treated as no result
    with pytest.raises(ToolError) as exc:
        await client.get_property_details(_input())
    assert exc.value.code is ErrorCode.NO_RESULTS


# ----- tool function: orchestration -----------------------------------------


async def test_tool_returns_success_envelope(details_fixture):
    client = _make_client(lambda req: _ok(details_fixture))
    cache = TTLCache(ttl_seconds=300)
    result = await get_stay_details(
        client=client, cache=cache,
        property_token="ChoQ7cre3vCRr9naARoNL2cvMTF6MjFqODVqcBAC",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    assert "error" not in result
    assert result["name"]
    assert len(result["booking_partners"]) >= 1


async def test_tool_lazy_auth_failure_without_client():
    """Server started without SERPAPI_KEY → client is None → auth_failed."""
    cache = TTLCache(ttl_seconds=300)
    result = await get_stay_details(
        client=None, cache=cache,
        property_token="TOKEN_XYZ",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    assert result["error"]["code"] == "auth_failed"


async def test_tool_validates_token_min_length():
    """Empty / too-short tokens are rejected before any HTTP call."""
    cache = TTLCache(ttl_seconds=300)
    result = await get_stay_details(
        client=None, cache=cache,  # client doesn't matter; validation runs first
        property_token="x",  # too short
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    assert result["error"]["code"] == "invalid_input"


async def test_tool_caches_by_token_and_dates(details_fixture):
    """Two identical calls → 1 SerpAPI call."""
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return _ok(details_fixture)

    client = _make_client(handler)
    cache = TTLCache(ttl_seconds=300)
    kwargs = dict(
        client=client, cache=cache,
        property_token="ChoQ7cre3vCRr9naARoNL2cvMTF6MjFqODVqcBAC",
        check_in_date="2026-06-15", check_out_date="2026-06-18",
    )
    await get_stay_details(**kwargs)
    await get_stay_details(**kwargs)
    assert call_count["n"] == 1
