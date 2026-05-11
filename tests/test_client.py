import httpx
import pytest

from flights_mcp.amadeus.client import AmadeusClient
from flights_mcp.errors import ErrorCode, ToolError
from flights_mcp.models import SearchFlightsInput


def _make_client(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return AmadeusClient(
        http=client,
        base_url="https://test.api.amadeus.com",
        client_id="id",
        client_secret="sec",
    )


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800})


async def test_search_returns_normalized_offers(synthetic_round_trip):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(200, json=synthetic_round_trip)

    client = _make_client(handler)
    inp = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", adults=1,
    )
    offers = await client.search(inp)
    assert len(offers) == 2
    assert offers[0].offer_id == "1"


async def test_search_passes_max_param_to_amadeus(synthetic_round_trip):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=synthetic_round_trip)

    client = _make_client(handler)
    inp = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29",
        max_results=10,
    )
    await client.search(inp)
    assert captured["params"]["max"] == "10"


async def test_search_empty_data_raises_no_results(empty_results):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(200, json=empty_results)

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_search_429_with_quota_message_maps_to_quota_exceeded():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(429, json={"errors": [{"code": 38194, "detail": "Monthly quota exceeded"}]})

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.QUOTA_EXCEEDED
    assert exc.value.retryable is False  # quota is not retryable until next month


async def test_search_429_transient_maps_to_rate_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(429, json={"errors": [{"detail": "Too many requests"}]})

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.RATE_LIMITED
    assert exc.value.retryable is True


async def test_search_5xx_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(503)

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
    assert exc.value.retryable is True


async def test_search_malformed_body_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(200, content=b"<html>gateway error</html>")

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
    assert exc.value.retryable is True


async def test_search_quota_detected_by_code_even_when_detail_lacks_keyword():
    """Amadeus's prose varies by region; the numeric code must be sufficient."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(429, json={"errors": [{"code": 38194, "detail": "Limit reached"}]})

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.QUOTA_EXCEEDED
