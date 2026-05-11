"""SerpAPI Google Flights client.

Substitutable HTTP transport via the injected `httpx.AsyncClient` so tests can
swap in `MockTransport`. Authentication is a static `api_key` query parameter;
there is no OAuth and no token cache.

Round-trip search is a two-step dance:
  1. Initial GET returns outbound options, each with a `departure_token`.
  2. For each outbound we want to surface, GET again with that token to fetch
     matching return options. The first return option becomes the inbound.

One-way search is a single GET; the returned options are the offers.
"""
from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import ValidationError

from flights_mcp.errors import ErrorCode, ToolError
from flights_mcp.models import FlightOffer, SearchFlightsInput
from flights_mcp.serpapi.normalize import (
    build_one_way_offers,
    build_round_trip_offer,
)
from flights_mcp.serpapi.raw import SerpFlightOption, SerpGoogleFlightsResponse

BASE_URL = "https://serpapi.com"
_SEARCH_PATH = "/search"
_SEARCH_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)

# SerpAPI surfaces quota/rate-limit problems as plain English in the `error`
# field on a 200 or 4xx response. Match on substrings of the documented messages.
_QUOTA_HINTS = ("monthly searches", "plan has run out", "exhausted", "run out of searches")
_RATE_LIMIT_HINTS = ("too many", "rate limit", "throttled")
_AUTH_HINTS = ("invalid api key", "missing api key", "api key not")


class SerpAPIClient:
    def __init__(self, *, http: httpx.AsyncClient, api_key: str, base_url: str = BASE_URL):
        self._http = http
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def search(self, params: SearchFlightsInput) -> list[FlightOffer]:
        if params.return_date is None:
            return await self._search_one_way(params)
        return await self._search_round_trip(params)

    # ----- one-way path ------------------------------------------------------

    async def _search_one_way(self, params: SearchFlightsInput) -> list[FlightOffer]:
        outbound = await self._call(self._one_way_query(params))
        all_options = list(outbound.best_flights) + list(outbound.other_flights)
        offers = build_one_way_offers(
            all_options,
            currency=params.currency,
            adults=params.adults,
            limit=params.max_results,
        )
        if not offers:
            raise ToolError(ErrorCode.NO_RESULTS, "SerpAPI returned no flight options.")
        return offers

    # ----- round-trip path ---------------------------------------------------

    async def _search_round_trip(self, params: SearchFlightsInput) -> list[FlightOffer]:
        # Step 1: outbound options.
        outbound_resp = await self._call(self._round_trip_outbound_query(params))
        outbound_options = list(outbound_resp.best_flights) + list(outbound_resp.other_flights)
        if not outbound_options:
            raise ToolError(ErrorCode.NO_RESULTS, "SerpAPI returned no outbound options.")

        # Step 2: fetch return options for each candidate outbound in parallel.
        # SerpAPI's per-call latency is the dominant cost (3-5s each), so doing
        # N return-leg fetches concurrently brings round-trip wall time down
        # from ~N×latency to ~latency. The 5-call cap on max_results keeps the
        # burst small enough that SerpAPI's per-second rate limit isn't a risk.
        candidates = [
            o for o in outbound_options[: params.max_results] if o.departure_token
        ]
        if not candidates:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                "SerpAPI returned outbound options but none had a departure_token.",
            )

        return_resps = await asyncio.gather(
            *(
                self._call(self._round_trip_return_query(params, c.departure_token))
                for c in candidates
            ),
            return_exceptions=True,
        )

        offers: list[FlightOffer] = []
        for outbound_option, resp in zip(candidates, return_resps):
            if isinstance(resp, ToolError):
                # An auth/quota/rate-limit problem on any leg is global —
                # surface the first one and abort the whole search.
                raise resp
            if isinstance(resp, BaseException):
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"Return-leg fetch failed unexpectedly: {resp}",
                    retryable=True,
                ) from resp
            return_options = list(resp.best_flights) + list(resp.other_flights)
            if not return_options:
                continue  # this outbound has no matching returns for the requested date
            offers.append(build_round_trip_offer(
                outbound_option,
                return_options[0],
                currency=params.currency,
                adults=params.adults,
            ))

        if not offers:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                "SerpAPI returned outbound options but no matching return legs.",
            )
        return offers

    # ----- HTTP helpers ------------------------------------------------------

    async def _call(self, query: dict[str, str]) -> SerpGoogleFlightsResponse:
        full_params = {**query, "api_key": self._api_key}
        try:
            response = await self._http.get(
                f"{self._base_url}{_SEARCH_PATH}",
                params=full_params,
                timeout=_SEARCH_TIMEOUT,
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

        # SerpAPI can return a 200 with an `error` field — treat it as an error.
        if isinstance(body, dict) and "error" in body and isinstance(body["error"], str):
            self._raise_for_body_error(body["error"])

        try:
            return SerpGoogleFlightsResponse.model_validate(body)
        except ValidationError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI returned an unparseable response: {e}",
                retryable=True,
            ) from e

    # ----- query construction -----------------------------------------------

    def _base_query(self, params: SearchFlightsInput) -> dict[str, str]:
        # sort_by=1 pins SerpAPI to "best" ordering (Google's blended ranking
        # of price/duration/stops). Not exposed to Claude.
        return {
            "engine": "google_flights",
            "departure_id": params.origin,
            "arrival_id": params.destination,
            "outbound_date": params.departure_date,
            "adults": str(params.adults),
            "currency": params.currency,
            "travel_class": _travel_class_for_serpapi(params.cabin_class.value),
            "sort_by": "1",
        }

    def _one_way_query(self, params: SearchFlightsInput) -> dict[str, str]:
        q = self._base_query(params)
        q["type"] = "2"  # 2 = one-way
        if params.children:
            q["children"] = str(params.children)
        if params.infants:
            # SerpAPI splits "infants in seat" vs "on lap" — we treat all
            # infants as lap (the input model already enforces infants <= adults
            # to match the lap-infant rule).
            q["infants_on_lap"] = str(params.infants)
        if params.non_stop_only:
            q["stops"] = "1"  # SerpAPI: 1 = nonstop only
        return q

    def _round_trip_outbound_query(self, params: SearchFlightsInput) -> dict[str, str]:
        q = self._one_way_query(params)
        q["type"] = "1"  # 1 = round trip
        if params.return_date:
            q["return_date"] = params.return_date
        return q

    def _round_trip_return_query(self, params: SearchFlightsInput, departure_token: str) -> dict[str, str]:
        # The follow-up call repeats the original parameters plus the token.
        q = self._round_trip_outbound_query(params)
        q["departure_token"] = departure_token
        return q

    # ----- error mapping -----------------------------------------------------

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        sc = response.status_code
        if sc == 200:
            return
        if sc == 401:
            raise ToolError(ErrorCode.AUTH_FAILED, "SerpAPI rejected the API key.")
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
        # 4xx other than 401/429: surface as upstream_error with a snippet.
        raise ToolError(
            ErrorCode.UPSTREAM_ERROR,
            f"SerpAPI returned {sc}: {response.text[:200]}",
        )

    @staticmethod
    def _raise_for_body_error(message: str) -> None:
        """Translate SerpAPI's body-level `error` string into our codes."""
        text = message.lower()
        if any(hint in text for hint in _AUTH_HINTS):
            raise ToolError(ErrorCode.AUTH_FAILED, f"SerpAPI auth error: {message}")
        if any(hint in text for hint in _QUOTA_HINTS):
            raise ToolError(
                ErrorCode.QUOTA_EXCEEDED,
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


def _travel_class_for_serpapi(cabin: str) -> str:
    # SerpAPI Google Flights uses integer codes: 1=Economy, 2=PremiumEconomy,
    # 3=Business, 4=First.
    return {
        "ECONOMY": "1",
        "PREMIUM_ECONOMY": "2",
        "BUSINESS": "3",
        "FIRST": "4",
    }.get(cabin, "1")
