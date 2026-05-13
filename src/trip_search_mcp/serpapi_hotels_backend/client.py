"""SerpAPI google_hotels client.

Same injectable-transport pattern as `serpapi/client.py` was for flights
(pre-fli) — tests substitute `httpx.MockTransport` so nothing hits SerpAPI.

The stays integration is OPT-IN. The server starts without requiring
`SERPAPI_KEY` — flights work key-free via fli. When `search_stays` is
called without the key, the tool returns a structured `auth_failed`
envelope rather than crashing the server.

Phase 1 added the category dispatcher. When `category="all"`, the client
fans out to two SerpAPI calls in parallel:
  - hotels:  vacation_rentals=false + hotel-only filters
  - rentals: vacation_rentals=true  + rental-only filters

SerpAPI rejects mismatched filters with HTTP 400 (see Phase 0 findings),
so the two query-builders MUST be kept separate at request-build time.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import (
    GetStayDetailsInput,
    SearchStaysInput,
    SearchStaysResult,
    StayCategory,
    StayDetails,
    StayOffer,
)
from trip_search_mcp.serpapi_hotels_backend.normalize import (
    build_stay_details,
    merge_and_dedup,
    normalize_and_filter,
    sort_and_truncate,
)
from trip_search_mcp.serpapi_hotels_backend.raw import (
    SerpHotelsResponse,
    SerpPropertyDetailsResponse,
)

BASE_URL = "https://serpapi.com"
_SEARCH_PATH = "/search"
_REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)

# Body-error keyword maps (carried from the retired flights SerpAPI client).
_QUOTA_HINTS = ("monthly searches", "plan has run out", "exhausted", "run out of searches")
_RATE_LIMIT_HINTS = ("too many", "rate limit", "throttled")
_AUTH_HINTS = ("invalid api key", "missing api key", "api key not")

Mode = Literal["hotels", "rentals"]


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

    # ----- public entry point ------------------------------------------------

    async def search(self, params: SearchStaysInput) -> SearchStaysResult:
        """Dispatcher: route to single-mode or merged-mode based on category.

        Single-mode paths preserve the historical search_hotels contract;
        merged path runs both SerpAPI calls in parallel via asyncio.gather
        with return_exceptions=True so one failure doesn't cancel the other.

        NOTE: `StayCategory.AIRBNB` is NOT handled here — it bypasses
        SerpAPI entirely. The tool-function layer routes it to
        `AirbnbClient.search()` instead.
        """
        if params.category is StayCategory.HOTELS:
            offers = await self._search_single(params, mode="hotels")
            return SearchStaysResult(results=offers, warnings=[])

        if params.category is StayCategory.VACATION_RENTALS:
            offers = await self._search_single(params, mode="rentals")
            return SearchStaysResult(results=offers, warnings=[])

        if params.category is StayCategory.AIRBNB:
            # Defensive: tool layer should have routed this to AirbnbClient.
            # If we land here, signal a programming error clearly.
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "category='airbnb' must be handled by AirbnbClient, not SerpAPIHotelsClient. "
                "This is a router bug.",
                retryable=False,
            )

        # category=ALL: fan out in parallel.
        return await self._search_merged(params)

    # ----- property details (get_stay_details tool) -------------------------

    async def get_property_details(self, params: GetStayDetailsInput) -> StayDetails:
        """One SerpAPI call against the property_details endpoint.

        Unlike `search()`, this takes a specific `property_token` (from a
        prior search result) and returns rich per-property data:
        long-form description, ~14 nearby places, and a `prices` array
        where each entry includes a `link` to the booking partner.
        """
        body = await self._call({
            "engine": "google_hotels",
            "q": params.property_token,    # required by SerpAPI but ignored when property_token is set
            "check_in_date": params.check_in_date,
            "check_out_date": params.check_out_date,
            "adults": str(params.adults),
            "currency": params.currency,
            "hl": "en",
            "property_token": params.property_token,
        })
        try:
            parsed = SerpPropertyDetailsResponse.model_validate(body)
        except ValidationError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI returned an unparseable property_details response: {e}",
                retryable=True,
            ) from e
        if not parsed.name:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"No property found for token {params.property_token!r}. "
                "The token may have expired or the property is no longer listed.",
            )
        try:
            return build_stay_details(parsed, currency=params.currency)
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Couldn't normalize property_details response: {e}",
                retryable=True,
            ) from e

    # ----- single-mode -------------------------------------------------------

    async def _search_single(
        self, params: SearchStaysInput, *, mode: Mode,
    ) -> list[StayOffer]:
        """Public single-call path: one SerpAPI call → normalize+filter →
        sort → truncate. Raises ToolError on failure."""
        offers = await self._fetch_one_unfiltered(params, mode=mode)
        offers = sort_and_truncate(offers, params.sort_by, params.max_results)
        if not offers:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"{mode.capitalize()} were returned but all were filtered out by your "
                "criteria (min_rating / min_review_score / max_price_per_night / "
                "required_amenities). Relax one of those and retry.",
            )
        return offers

    async def _fetch_one_unfiltered(
        self, params: SearchStaysInput, *, mode: Mode,
    ) -> list[StayOffer]:
        """One SerpAPI call → normalize → filter. No sort, no truncate.

        Used by both _search_single and _search_merged. Critical for the
        merge path: each side returns its full filtered candidate set so
        the dedup+sort+truncate can run on the full universe rather than
        on a pre-truncated subset that might miss the global top-N.
        """
        body = await self._call(self._build_query(params, mode=mode))
        parsed = self._parse(body)
        if not parsed.properties:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"SerpAPI returned no {mode} for {params.location!r}.",
            )
        return self._normalize_for(parsed, params, mode=mode)

    # ----- merged mode -------------------------------------------------------

    async def _search_merged(
        self, params: SearchStaysInput,
    ) -> SearchStaysResult:
        """Fan out hotels + rentals in parallel, merge, dedup, sort.

        Uses return_exceptions=True so one side's failure doesn't cancel
        the other side's in-flight request. Builds warnings for any
        partial failures so the LLM can surface them to the user.
        """
        hotels_task = self._fetch_one_unfiltered(params, mode="hotels")
        rentals_task = self._fetch_one_unfiltered(params, mode="rentals")
        h_result, r_result = await asyncio.gather(
            hotels_task, rentals_task, return_exceptions=True,
        )

        warnings: list[str] = []
        hotels: list[StayOffer] = []
        rentals: list[StayOffer] = []

        h_err = h_result if isinstance(h_result, BaseException) else None
        r_err = r_result if isinstance(r_result, BaseException) else None

        if h_err is None:
            hotels = h_result  # type: ignore[assignment]
        elif isinstance(h_err, ToolError):
            warnings.append(self._warning_for("Hotel", h_err))
        else:
            # Unexpected exception (network bug, programming error) — re-raise
            # so it surfaces; partial-failure handling is for known ToolErrors.
            raise h_err

        if r_err is None:
            rentals = r_result  # type: ignore[assignment]
        elif isinstance(r_err, ToolError):
            warnings.append(self._warning_for("Vacation rental", r_err))
        else:
            raise r_err

        # Both sides errored — surface the more informative error.
        if h_err is not None and r_err is not None:
            assert isinstance(h_err, ToolError) and isinstance(r_err, ToolError)
            # Prefer the hotel-side error on tie (it's the historical default).
            raise h_err

        merged = merge_and_dedup(hotels, rentals)
        merged = sort_and_truncate(merged, params.sort_by, params.max_results)

        if not merged:
            # Both sides returned results but all were post-filtered out
            # OR both sides returned NO_RESULTS (which raised ToolError
            # captured above as warnings).
            raise ToolError(
                ErrorCode.NO_RESULTS,
                "No matching stays — both hotels and vacation rentals were "
                "either empty or fully filtered out. Relax your filters and retry.",
            )

        return SearchStaysResult(results=merged, warnings=warnings)

    @staticmethod
    def _warning_for(side_label: str, err: ToolError) -> str:
        if err.code is ErrorCode.NO_RESULTS:
            return f"{side_label} search returned no results."
        return (
            f"{side_label} search failed: {err.code.value}. "
            f"Showing the other side only."
        )

    # ----- normalize-and-filter (per mode) ------------------------------------

    def _normalize_for(
        self,
        parsed: SerpHotelsResponse,
        params: SearchStaysInput,
        *,
        mode: Mode,
    ) -> list[StayOffer]:
        """Apply mode-appropriate post-filters.

        - hotels mode: min_rating applies (rentals have no star_rating).
        - rentals mode: min_rating is dropped; min_bedrooms/min_bathrooms apply.

        Cross-category filters (min_review_score, max_price_per_night,
        required_amenities) apply to both modes.
        """
        offers = normalize_and_filter(
            parsed,
            location=params.location,
            check_in=params.check_in_date,
            check_out=params.check_out_date,
            currency=params.currency,
            # min_rating: hotels only. Rentals carry no hotel class so this
            # would always filter them out — drop instead.
            min_rating=params.min_rating if mode == "hotels" else None,
            min_review_score=params.min_review_score,
            max_price_per_night=params.max_price_per_night,
            required_amenities=params.required_amenities,
        )
        # Rental-only structural filters apply post-normalize too.
        if mode == "rentals":
            if params.min_bedrooms is not None:
                offers = [o for o in offers if (o.bedrooms or 0) >= params.min_bedrooms]
            if params.min_bathrooms is not None:
                offers = [o for o in offers if (o.bathrooms or 0) >= params.min_bathrooms]
        return offers

    @staticmethod
    def _parse(body: dict) -> SerpHotelsResponse:
        try:
            return SerpHotelsResponse.model_validate(body)
        except ValidationError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI returned an unparseable hotels response: {e}",
                retryable=True,
            ) from e

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

    def _build_query(self, p: SearchStaysInput, *, mode: Mode) -> dict[str, str]:
        """Build SerpAPI request params scoped to a single mode.

        IMPORTANT: SerpAPI returns HTTP 400 for filters used in the wrong
        mode (Phase 0 finding). We only include `hotel_class`-equivalent
        in the hotel call and `bedrooms`/`bathrooms` in the rental call.
        """
        q: dict[str, str] = {
            "engine": "google_hotels",
            "q": p.location,
            "check_in_date": p.check_in_date,
            "check_out_date": p.check_out_date,
            "adults": str(p.adults),
            "currency": p.currency,
            "hl": "en",
            "vacation_rentals": "true" if mode == "rentals" else "false",
        }
        if p.children:
            q["children"] = str(p.children)
        if p.rooms and p.rooms > 1:
            q["rooms"] = str(p.rooms)

        if mode == "rentals":
            # SerpAPI's `bedrooms` / `bathrooms` are native filters; routing
            # them to the request saves a post-filter pass AND surfaces
            # SerpAPI's own ranking against the constraint.
            if p.min_bedrooms is not None:
                q["bedrooms"] = str(p.min_bedrooms)
            if p.min_bathrooms is not None:
                q["bathrooms"] = str(p.min_bathrooms)
        # hotels mode: no native `hotel_class` request param today. We post-
        # filter on min_rating in `_normalize_for`. (SerpAPI supports
        # `hotel_class` natively; routing it would be a follow-up perf opt.)

        return q

    # ----- error mapping -----------------------------------------------------

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        sc = response.status_code
        if sc == 200:
            return
        if sc == 400:
            # Caller almost certainly sent a mismatched-mode filter (e.g.
            # bedrooms with vacation_rentals=false). Surface SerpAPI's
            # message so the bug is diagnosable; mark unretryable.
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"SerpAPI rejected request (400): {response.text[:200]}",
                retryable=False,
            )
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
