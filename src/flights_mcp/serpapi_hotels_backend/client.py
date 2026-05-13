"""SerpAPI google_hotels client.

Same injectable-transport pattern as `serpapi/client.py` was for flights
(pre-fli) — tests substitute `httpx.MockTransport` so nothing hits SerpAPI.

The hotels integration is OPT-IN. The server starts without requiring
`SERPAPI_KEY` — flights work key-free via fli. When `search_hotels` is
called without the key, the tool returns a structured `invalid_input`
error pointing the user at SerpAPI signup, rather than crashing the
server. (Code "auth_failed" was retired during the fli migration; we
re-use "invalid_input" with an actionable message instead.)
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from flights_mcp.errors import ErrorCode, ToolError
from flights_mcp.models import HotelOffer, SearchHotelsInput
from flights_mcp.serpapi_hotels_backend.normalize import build_offers
from flights_mcp.serpapi_hotels_backend.raw import SerpHotelsResponse

BASE_URL = "https://serpapi.com"
_SEARCH_PATH = "/search"
_REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
# We pin a single response currency rather than expose it as a tool input.
# Matches the flights contract: "no currency input; documented in tool
# description". Users who care about a different currency can submit a
# future enhancement adding the input.
_RESPONSE_CURRENCY = "USD"

# Mirror the body-error keyword maps used by the flights SerpAPI client
# (which was retired during the fli migration, but the wisdom carries over).
_QUOTA_HINTS = ("monthly searches", "plan has run out", "exhausted", "run out of searches")
_RATE_LIMIT_HINTS = ("too many", "rate limit", "throttled")
_AUTH_HINTS = ("invalid api key", "missing api key", "api key not")


class SerpAPIHotelsClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        api_key: str,
        base_url: str = BASE_URL,
    ):
        self._http = http
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def search(self, params: SearchHotelsInput) -> list[HotelOffer]:
        body = await self._call(self._build_query(params))

        try:
            parsed = SerpHotelsResponse.model_validate(body)
        except ValidationError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI returned an unparseable hotels response: {e}",
                retryable=True,
            ) from e

        if not parsed.properties:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"SerpAPI returned no hotels for {params.location!r}.",
            )

        try:
            offers = build_offers(
                parsed,
                location=params.location,
                check_in=params.check_in_date,
                check_out=params.check_out_date,
                currency=_RESPONSE_CURRENCY,
                sort_by=params.sort_by,
                min_rating=params.min_rating,
                min_review_score=params.min_review_score,
                max_price_per_night=params.max_price_per_night,
                required_amenities=params.required_amenities,
                limit=params.max_results,
            )
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Couldn't normalize a SerpAPI hotel entry: {e}",
                retryable=True,
            ) from e

        if not offers:
            # All raw properties were filtered out — tell the user their
            # filters rejected everything rather than implying SerpAPI
            # returned nothing.
            raise ToolError(
                ErrorCode.NO_RESULTS,
                "Hotels were returned but all were filtered out by your criteria "
                "(min_rating / min_review_score / max_price_per_night / required_amenities). "
                "Relax one of those and retry.",
            )
        return offers

    # ----- HTTP --------------------------------------------------------------

    async def _call(self, query: dict[str, Any]) -> dict[str, Any]:
        full_params = {**query, "api_key": self._api_key}
        try:
            response = await self._http.get(
                f"{self._base_url}{_SEARCH_PATH}",
                params=full_params,
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as e:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, f"SerpAPI network error: {e}") from e

        self._raise_for_status(response)
        try:
            body = response.json()
        except json.JSONDecodeError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI returned non-JSON body: {e}",
                retryable=True,
            ) from e

        if isinstance(body, dict) and isinstance(body.get("error"), str):
            self._raise_for_body_error(body["error"])

        return body

    def _build_query(self, p: SearchHotelsInput) -> dict[str, str]:
        q: dict[str, str] = {
            "engine": "google_hotels",
            "q": p.location,
            "check_in_date": p.check_in_date,
            "check_out_date": p.check_out_date,
            "adults": str(p.adults),
            "currency": _RESPONSE_CURRENCY,
            "hl": "en",
        }
        if p.children:
            q["children"] = str(p.children)
        if p.rooms and p.rooms > 1:
            # SerpAPI's google_hotels treats rooms via the `children_ages`
            # mechanism in some doc versions; the cleanest way to bias
            # results for multi-room is to bump adults. For V1 we pass
            # rooms in as a hint and let SerpAPI route it (it accepts the
            # parameter without erroring even if it doesn't act on it).
            q["rooms"] = str(p.rooms)
        return q

    # ----- error mapping -----------------------------------------------------

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        sc = response.status_code
        if sc == 200:
            return
        if sc == 401:
            raise ToolError(
                ErrorCode.AUTH_FAILED,
                "SerpAPI rejected the API key. Check that SERPAPI_KEY is set correctly.",
            )
        if sc == 429:
            raise ToolError(
                ErrorCode.RATE_LIMITED,
                "SerpAPI rate limit hit.",
                retryable=True,
            )
        if sc >= 500:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI returned {sc}.",
                retryable=True,
            )
        raise ToolError(
            ErrorCode.UPSTREAM_ERROR,
            f"SerpAPI returned {sc}: {response.text[:200]}",
        )

    @staticmethod
    def _raise_for_body_error(message: str) -> None:
        text = message.lower()
        if any(hint in text for hint in _AUTH_HINTS):
            raise ToolError(
                ErrorCode.AUTH_FAILED,
                f"SerpAPI auth error: {message}",
            )
        if any(hint in text for hint in _QUOTA_HINTS):
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI quota exhausted: {message}",
                retryable=False,
            )
        if any(hint in text for hint in _RATE_LIMIT_HINTS):
            raise ToolError(
                ErrorCode.RATE_LIMITED,
                f"SerpAPI rate limit: {message}",
                retryable=True,
            )
        raise ToolError(
            ErrorCode.UPSTREAM_ERROR,
            f"SerpAPI error: {message}",
            retryable=True,
        )
