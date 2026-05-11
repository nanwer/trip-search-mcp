"""Amadeus Flight Offers Search client.

Substitutable HTTP transport via the injected `httpx.AsyncClient`. Token cache
is constructed internally because its lifecycle is identical to the client's.
"""
from __future__ import annotations

import httpx

from flights_mcp.amadeus.normalize import normalize_offers
from flights_mcp.amadeus.token import TokenCache
from flights_mcp.errors import ErrorCode, ToolError
from flights_mcp.models import AmadeusSearchResponse, FlightOffer, SearchFlightsInput

_BASE_URL_TEST = "https://test.api.amadeus.com"
_BASE_URL_PROD = "https://api.amadeus.com"


def base_url_for_env(env: str) -> str:
    if env == "production":
        return _BASE_URL_PROD
    if env == "test":
        return _BASE_URL_TEST
    raise ValueError(f"AMADEUS_ENV must be 'test' or 'production', got {env!r}")


class AmadeusClient:
    def __init__(self, *, http: httpx.AsyncClient, base_url: str,
                 client_id: str, client_secret: str):
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._tokens = TokenCache(
            client=http, base_url=base_url,
            client_id=client_id, client_secret=client_secret,
        )

    async def search(self, params: SearchFlightsInput) -> list[FlightOffer]:
        token = await self._tokens.get_token()
        query = self._build_query(params)
        try:
            response = await self._http.get(
                f"{self._base_url}/v2/shopping/flight-offers",
                params=query,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as e:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, f"Search network error: {e}") from e

        self._raise_for_status(response)
        parsed = AmadeusSearchResponse.model_validate(response.json())
        offers = normalize_offers(parsed)
        if not offers:
            raise ToolError(ErrorCode.NO_RESULTS, "Amadeus returned no offers.")
        return offers

    def _build_query(self, p: SearchFlightsInput) -> dict[str, str]:
        q: dict[str, str] = {
            "originLocationCode": p.origin,
            "destinationLocationCode": p.destination,
            "departureDate": p.departure_date,
            "adults": str(p.adults),
            "travelClass": p.cabin_class.value,
            "currencyCode": p.currency,
            "max": str(p.max_results),
        }
        if p.return_date:
            q["returnDate"] = p.return_date
        if p.children:
            q["children"] = str(p.children)
        if p.infants:
            q["infants"] = str(p.infants)
        if p.non_stop_only:
            q["nonStop"] = "true"
        return q

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        sc = response.status_code
        if sc == 200:
            return
        if sc == 401:
            raise ToolError(ErrorCode.AUTH_FAILED, "Amadeus rejected credentials.")
        if sc == 429:
            body_text = ""
            try:
                body = response.json()
                body_text = " ".join(
                    str(err.get("detail", "")) for err in body.get("errors", [])
                ).lower()
            except Exception:
                pass
            if "quota" in body_text:
                raise ToolError(ErrorCode.QUOTA_EXCEEDED,
                                "Amadeus monthly quota exhausted.", retryable=False)
            raise ToolError(ErrorCode.RATE_LIMITED,
                            "Amadeus rate limit hit.", retryable=True)
        if sc >= 500:
            raise ToolError(ErrorCode.UPSTREAM_ERROR,
                            f"Amadeus returned {sc}.", retryable=True)
        if sc == 400:
            raise ToolError(ErrorCode.UPSTREAM_ERROR,
                            f"Amadeus rejected request: {response.text[:200]}")
        raise ToolError(ErrorCode.UPSTREAM_ERROR,
                        f"Unexpected Amadeus status {sc}: {response.text[:200]}")
