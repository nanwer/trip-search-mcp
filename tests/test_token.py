import asyncio

import httpx
import pytest

from flights_mcp.amadeus.token import TokenCache


def _make_transport(responses):
    """Return a MockTransport that yields the given responses in order."""
    iter_responses = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(iter_responses)

    return httpx.MockTransport(handler)


async def test_first_call_fetches_token():
    transport = _make_transport([
        httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800}),
    ])
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        token = await cache.get_token()
        assert token == "tok-1"


async def test_second_call_returns_cached_token():
    """Only one HTTP call should be made if the token is still valid."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        await cache.get_token()
        await cache.get_token()
        assert call_count["n"] == 1


async def test_concurrent_callers_share_one_refresh():
    """Lock contract: two concurrent get_token() calls produce exactly one HTTP call."""
    call_count = {"n": 0}
    gate = asyncio.Event()

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        await gate.wait()
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800})

    # MockTransport's handler can be a coroutine.
    transport = httpx.MockTransport(slow_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        # Kick off two concurrent fetches.
        task_a = asyncio.create_task(cache.get_token())
        task_b = asyncio.create_task(cache.get_token())
        # Let them block on the lock + the gated HTTP response.
        await asyncio.sleep(0.05)
        gate.set()
        a, b = await asyncio.gather(task_a, task_b)
        assert a == b == "tok-1"
        assert call_count["n"] == 1


async def test_401_raises_auth_failed():
    from flights_mcp.errors import ErrorCode, ToolError

    transport = _make_transport([
        httpx.Response(401, json={"errors": [{"code": 38187, "title": "bad creds"}]}),
    ])
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        with pytest.raises(ToolError) as exc:
            await cache.get_token()
        assert exc.value.code is ErrorCode.AUTH_FAILED


async def test_5xx_raises_upstream_error():
    from flights_mcp.errors import ErrorCode, ToolError

    transport = _make_transport([httpx.Response(503)])
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        with pytest.raises(ToolError) as exc:
            await cache.get_token()
        assert exc.value.code is ErrorCode.UPSTREAM_ERROR


async def test_expired_token_triggers_refresh(monkeypatch):
    """After the cached token's _expires_at lapses, get_token() must re-fetch."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"access_token": f"tok-{call_count['n']}", "expires_in": 100})

    transport = httpx.MockTransport(handler)
    fake_clock = [1000.0]
    monkeypatch.setattr("flights_mcp.amadeus.token.time.monotonic", lambda: fake_clock[0])

    async with httpx.AsyncClient(transport=transport) as client:
        # refresh_buffer_seconds=0 makes _expires_at exactly the upstream expires_in.
        cache = TokenCache(
            client=client, base_url="https://test.api.amadeus.com",
            client_id="id", client_secret="sec", refresh_buffer_seconds=0,
        )
        first = await cache.get_token()
        assert first == "tok-1"
        assert call_count["n"] == 1

        # Advance the clock past expiry; next call must refresh.
        fake_clock[0] += 101
        second = await cache.get_token()
        assert second == "tok-2"
        assert call_count["n"] == 2
