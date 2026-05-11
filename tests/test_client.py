import httpx
import pytest

from flights_mcp.errors import ErrorCode, ToolError
from flights_mcp.models import SearchFlightsInput
from flights_mcp.serpapi.client import SerpAPIClient


def _make_client(handler) -> SerpAPIClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SerpAPIClient(http=http, api_key="fake-key")


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


# ----- one-way path ----------------------------------------------------------


async def test_one_way_returns_normalized_offers(serpapi_one_way):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return _ok(serpapi_one_way)

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    offers = await client.search(params)
    assert len(offers) == 2
    assert captured["params"]["type"] == "2"
    assert captured["params"]["sort_by"] == "1"
    assert captured["params"]["api_key"] == "fake-key"


async def test_one_way_empty_results_raises_no_results(serpapi_empty_results):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(serpapi_empty_results)

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    with pytest.raises(ToolError) as exc:
        await client.search(params)
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_one_way_respects_max_results(serpapi_one_way):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(serpapi_one_way)

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date="2026-05-18", max_results=1,
    )
    offers = await client.search(params)
    assert len(offers) == 1


# ----- round-trip path -------------------------------------------------------


async def test_round_trip_makes_one_plus_n_calls(
    serpapi_round_trip_outbound, serpapi_round_trip_return,
):
    """Two outbound options + max_results=2 → 1 outbound call + 2 return calls = 3 total."""
    call_log = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        call_log.append(params)
        if "departure_token" in params:
            return _ok(serpapi_round_trip_return)
        return _ok(serpapi_round_trip_outbound)

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", max_results=2,
    )
    offers = await client.search(params)

    assert len(offers) == 2
    assert len(call_log) == 3  # 1 outbound + 2 return-leg fetches
    assert "departure_token" not in call_log[0]
    assert call_log[1]["departure_token"] == "OUTBOUND_TOKEN_A"
    assert call_log[2]["departure_token"] == "OUTBOUND_TOKEN_B"


async def test_round_trip_max_results_clamps_to_outbound_options(
    serpapi_round_trip_outbound, serpapi_round_trip_return,
):
    """If max_results > available outbound options, only existing options are expanded."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "departure_token" in dict(request.url.params):
            return _ok(serpapi_round_trip_return)
        return _ok(serpapi_round_trip_outbound)

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", max_results=5,
    )
    offers = await client.search(params)
    # Outbound fixture only has 2 options total (1 best + 1 other).
    assert len(offers) == 2


async def test_round_trip_no_outbound_options_raises_no_results(serpapi_empty_results):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(serpapi_empty_results)

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", max_results=3,
    )
    with pytest.raises(ToolError) as exc:
        await client.search(params)
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_round_trip_outbound_exists_but_no_returns(
    serpapi_round_trip_outbound, serpapi_empty_results,
):
    """If every outbound has no matching return, surface NO_RESULTS."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "departure_token" in dict(request.url.params):
            return _ok(serpapi_empty_results)
        return _ok(serpapi_round_trip_outbound)

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", max_results=3,
    )
    with pytest.raises(ToolError) as exc:
        await client.search(params)
    assert exc.value.code is ErrorCode.NO_RESULTS


# ----- error mapping ---------------------------------------------------------


async def test_search_401_maps_to_auth_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid API key"})

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    with pytest.raises(ToolError) as exc:
        await client.search(params)
    assert exc.value.code is ErrorCode.AUTH_FAILED


async def test_search_429_maps_to_rate_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Too Many Requests")

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    with pytest.raises(ToolError) as exc:
        await client.search(params)
    assert exc.value.code is ErrorCode.RATE_LIMITED
    assert exc.value.retryable is True


async def test_search_5xx_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    with pytest.raises(ToolError) as exc:
        await client.search(params)
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
    assert exc.value.retryable is True


async def test_search_body_error_invalid_key_maps_to_auth_failed(serpapi_auth_failed_body):
    """SerpAPI sometimes returns a 200 with an `error` string in the body."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(serpapi_auth_failed_body)

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    with pytest.raises(ToolError) as exc:
        await client.search(params)
    assert exc.value.code is ErrorCode.AUTH_FAILED


async def test_search_body_error_quota_maps_to_quota_exceeded():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"error": "Your account has run out of searches for the month."})

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    with pytest.raises(ToolError) as exc:
        await client.search(params)
    assert exc.value.code is ErrorCode.QUOTA_EXCEEDED
    assert exc.value.retryable is False


async def test_search_malformed_body_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    client = _make_client(handler)
    params = SearchFlightsInput(
        origin="HEL", destination="IAD", departure_date="2026-05-18",
    )
    with pytest.raises(ToolError) as exc:
        await client.search(params)
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
    assert exc.value.retryable is True
