"""SerpAPI google_events client.

Same injectable-transport pattern as the other SerpAPI backends. Phase 0
confirmed `htichips` accepts named ranges (today / tomorrow / week /
weekend / next_week / month / next_month) for date filtering.

The tool layer composes location + optional event-type `query` into the
single `q` string SerpAPI expects.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import EventDateFilter, EventOffer, SearchEventsInput
from trip_search_mcp.serpapi_events_backend.normalize import build_offers
from trip_search_mcp.serpapi_events_backend.raw import SerpEventsResponse

BASE_URL = "https://serpapi.com"
_SEARCH_PATH = "/search"
_REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)

# Reuse the same error-keyword heuristics as the hotels backend.
_QUOTA_HINTS = ("monthly searches", "plan has run out", "exhausted", "run out of searches")
_RATE_LIMIT_HINTS = ("too many", "rate limit", "throttled")
_AUTH_HINTS = ("invalid api key", "missing api key", "api key not")


class SerpAPIEventsClient:
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

    async def search(self, params: SearchEventsInput) -> list[EventOffer]:
        body = await self._call(self._build_query(params))
        try:
            parsed = SerpEventsResponse.model_validate(body)
        except ValidationError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI returned an unparseable events response: {e}",
                retryable=True,
            ) from e

        if not parsed.events_results:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"No events found for {params.location!r}"
                + (f" matching {params.query!r}" if params.query else "")
                + ".",
            )

        try:
            offers = build_offers(parsed, limit=params.max_results)
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Couldn't normalize a SerpAPI event entry: {e}",
                retryable=True,
            ) from e

        if not offers:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"Events were returned for {params.location!r} but all were "
                "unactionable (missing title or ticket URL).",
            )
        return offers

    # ----- HTTP --------------------------------------------------------------

    def _build_query(self, p: SearchEventsInput) -> dict[str, str]:
        # Compose the q string. SerpAPI's google_events doesn't accept a
        # separate location parameter; everything goes in `q`. We follow
        # the natural-language phrasings Phase 0 verified work well:
        #   "Concerts in Lisbon"
        #   "Events in Lisbon"
        #   "BTS tour Paris"
        if p.query:
            q = f"{p.query} in {p.location}"
        else:
            q = f"Events in {p.location}"
        params: dict[str, str] = {
            "engine": "google_events",
            "q": q,
            "hl": "en",
        }
        if p.date_filter is not None:
            params["htichips"] = f"date:{p.date_filter.value}"
        return params

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

    # ----- error mapping (mirrors the hotels client) -------------------------

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        sc = response.status_code
        if sc == 200:
            return
        if sc == 400:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI rejected the events request (400): {response.text[:200]}",
                retryable=False,
            )
        if sc == 401:
            raise ToolError(
                ErrorCode.AUTH_FAILED,
                "SerpAPI rejected the API key. Check SERPAPI_KEY in your config.",
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
