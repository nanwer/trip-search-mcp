"""SerpAPI Tripadvisor client (search side).

Uses `engine=tripadvisor` with `ssrc=A` (Things to Do). Phase 0 confirmed
the endpoint takes a free-text `q` parameter that handles natural-
language activity queries ("cooking class Lisbon", "boat tours Paris")
without needing a separate structured filter.

Same injectable-transport pattern as the other SerpAPI backends.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import ActivityOffer, SearchActivitiesInput
from trip_search_mcp.tripadvisor_backend.normalize import build_offers
from trip_search_mcp.tripadvisor_backend.raw import SerpTripadvisorResponse

BASE_URL = "https://serpapi.com"
_SEARCH_PATH = "/search"
_REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)

_QUOTA_HINTS = ("monthly searches", "plan has run out", "exhausted", "run out of searches")
_RATE_LIMIT_HINTS = ("too many", "rate limit", "throttled")
_AUTH_HINTS = ("invalid api key", "missing api key", "api key not")


class SerpAPITripadvisorClient:
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

    async def search(self, params: SearchActivitiesInput) -> list[ActivityOffer]:
        body = await self._call(self._build_query(params))
        try:
            parsed = SerpTripadvisorResponse.model_validate(body)
        except ValidationError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI returned an unparseable activities response: {e}",
                retryable=True,
            ) from e

        if not parsed.places:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"No activities found for {params.location!r}"
                + (f" matching {params.query!r}" if params.query else "")
                + ".",
            )

        try:
            offers = build_offers(
                parsed,
                place_type_filter=params.place_type_filter,
                min_rating=params.min_rating,
                limit=params.max_results,
            )
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Couldn't normalize a Tripadvisor entry: {e}",
                retryable=True,
            ) from e

        if not offers:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                "Activities were returned but all were filtered out by your criteria "
                "(place_type_filter / min_rating). Relax one of those and retry.",
            )
        return offers

    def _build_query(self, p: SearchActivitiesInput) -> dict[str, str]:
        """Compose the SerpAPI query string.

        Per Phase 0, free-text concatenation works well:
        - "cooking class Lisbon" returns cooking experiences
        - "boat tours Lisbon" returns boat tours
        - "Lisbon" alone returns top sights
        """
        if p.query:
            q = f"{p.query} {p.location}"
        else:
            q = p.location
        return {
            "engine": "tripadvisor",
            "q": q,
            "ssrc": "A",                   # ssrc=A → Things to Do
            "hl": "en",
        }

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

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        sc = response.status_code
        if sc == 200:
            return
        if sc == 400:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI rejected the request (400): {response.text[:200]}",
                retryable=False,
            )
        if sc == 401:
            raise ToolError(
                ErrorCode.AUTH_FAILED,
                "SerpAPI rejected the API key. Check SERPAPI_KEY.",
            )
        if sc == 429:
            raise ToolError(
                ErrorCode.RATE_LIMITED, "SerpAPI rate limit hit.", retryable=True,
            )
        if sc >= 500:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR, f"SerpAPI returned {sc}.", retryable=True,
            )
        raise ToolError(
            ErrorCode.UPSTREAM_ERROR,
            f"SerpAPI returned {sc}: {response.text[:200]}",
        )

    @staticmethod
    def _raise_for_body_error(message: str) -> None:
        text = message.lower()
        if any(hint in text for hint in _AUTH_HINTS):
            raise ToolError(ErrorCode.AUTH_FAILED, f"SerpAPI auth error: {message}")
        if any(hint in text for hint in _QUOTA_HINTS):
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI quota exhausted: {message}", retryable=False,
            )
        if any(hint in text for hint in _RATE_LIMIT_HINTS):
            raise ToolError(
                ErrorCode.RATE_LIMITED, f"SerpAPI rate limit: {message}", retryable=True,
            )
        raise ToolError(
            ErrorCode.UPSTREAM_ERROR, f"SerpAPI error: {message}", retryable=True,
        )
