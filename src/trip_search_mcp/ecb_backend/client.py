"""ECB daily-rates HTTP client.

GET https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml → parse.

ECB updates the file once per day (~16:00 CET). The client maintains a
process-local cache (6h TTL) so repeated calls within the same session
don't re-fetch.

Same injectable-transport pattern as the other backends; tests pass an
`httpx.MockTransport` to avoid hitting ECB in CI.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from trip_search_mcp.ecb_backend.parse import EcbParseError, EcbRates, parse_ecb_xml
from trip_search_mcp.errors import ErrorCode, ToolError

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
USER_AGENT = "trip-search-mcp/0.2 (https://github.com/nanwer/trip-search-mcp)"
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# ECB publishes daily; 6h TTL means at most 4 fetches/day per process.
# In practice it's once — the cache outlives most chat sessions.
_CACHE_TTL_SECONDS = 6 * 60 * 60


class EcbClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        url: str = ECB_URL,
    ):
        self._http = http
        self._url = url
        self._cache: tuple[float, EcbRates] | None = None

    async def get_rates(self) -> EcbRates:
        """Return today's ECB rates. Cached for 6 hours per process."""
        if self._cache is not None:
            cached_at, rates = self._cache
            if time.time() - cached_at < _CACHE_TTL_SECONDS:
                return rates

        rates = await self._fetch_and_parse()
        self._cache = (time.time(), rates)
        return rates

    async def _fetch_and_parse(self) -> EcbRates:
        own_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=_TIMEOUT)
        try:
            try:
                response = await client.get(
                    self._url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=_TIMEOUT,
                )
            except httpx.HTTPError as e:
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"ECB network error: {e}",
                    retryable=True,
                ) from e

            sc = response.status_code
            if sc != 200:
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"ECB returned {sc}.",
                    retryable=(sc >= 500),
                )

            try:
                return parse_ecb_xml(response.content)
            except EcbParseError as e:
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"Couldn't parse ECB XML: {e}",
                    retryable=True,
                ) from e
        finally:
            if own_client:
                await client.aclose()
