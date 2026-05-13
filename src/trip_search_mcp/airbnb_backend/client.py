"""AirbnbClient — calls pyairbnb.search_all() with a geocoded bbox.

pyairbnb is synchronous, so we use `asyncio.to_thread` to keep the
event loop free during the scrape. Geocoding (location → bbox) and
the actual search are two separate concerns; the geocoder is its own
module so it's testable without pyairbnb in the picture.

The client follows the same injection pattern as SerpAPIHotelsClient:
the `search_fn` and `geocode_fn` callables are overridable for tests.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import httpx
import pyairbnb

from trip_search_mcp.airbnb_backend.geocode import geocode_to_bbox
from trip_search_mcp.airbnb_backend.normalize import normalize_listings
from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.models import SearchStaysInput, StayOffer

_logger = logging.getLogger("trip_search_mcp")

# Default zoom level for pyairbnb's bbox search. 10 ≈ city scale.
DEFAULT_ZOOM = 10

# Type aliases for injectable callables (test seam).
GeocodeFn = Callable[..., Awaitable[tuple[float, float, float, float]]]
SearchFn = Callable[..., list[dict[str, Any]]]


class AirbnbClient:
    """Wraps pyairbnb behind a thin async surface."""

    def __init__(
        self,
        *,
        geocode_fn: GeocodeFn | None = None,
        search_fn: SearchFn | None = None,
        http: httpx.AsyncClient | None = None,
        proxy_url: str = "",
    ):
        self._geocode_fn = geocode_fn or geocode_to_bbox
        self._search_fn = search_fn or pyairbnb.search_all
        self._http = http
        self._proxy_url = proxy_url

    async def search(self, params: SearchStaysInput) -> list[StayOffer]:
        # 1. Geocode the location → bounding box. Surfaces ToolError on
        # unknown/typo'd locations as INVALID_INPUT.
        try:
            bbox = await self._geocode_fn(params.location, http=self._http)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Geocoding failed for {params.location!r}: {e}",
                retryable=True,
            ) from e

        ne_lat, ne_long, sw_lat, sw_long = bbox

        # 2. Run pyairbnb's synchronous search off the event loop.
        try:
            listings = await asyncio.to_thread(
                self._search_fn,
                check_in=params.check_in_date,
                check_out=params.check_out_date,
                ne_lat=ne_lat, ne_long=ne_long,
                sw_lat=sw_lat, sw_long=sw_long,
                zoom_value=DEFAULT_ZOOM,
                currency=params.currency,
                adults=params.adults,
                children=params.children,
                min_bedrooms=(params.min_bedrooms or 0),
                min_bathrooms=(params.min_bathrooms or 0),
                proxy_url=self._proxy_url,
            )
        except TypeError:
            # pyairbnb signature drift between versions — surface but
            # don't try to recover; user should pin the dep.
            raise
        except Exception as e:
            # pyairbnb has no documented exception hierarchy; catch
            # broadly and translate. Anti-scraping pushback (403/429
            # from Airbnb) typically surfaces as RuntimeError or
            # KeyError inside pyairbnb when it can't parse the response.
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Airbnb scrape failed: {type(e).__name__}: {e}. "
                "This may be transient or indicate Airbnb-side blocking. "
                "Retry in a few minutes; if it persists, an upstream "
                "pyairbnb update may be needed.",
                retryable=True,
            ) from e

        if not listings:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                f"No Airbnb listings found for {params.location!r} "
                f"between {params.check_in_date} and {params.check_out_date}.",
            )

        offers = normalize_listings(
            listings,
            check_in=params.check_in_date,
            check_out=params.check_out_date,
            currency=params.currency,
        )

        # Apply cross-category filters that don't have native pyairbnb support.
        # min_review_score / max_price_per_night / required_amenities.
        if params.min_review_score is not None:
            offers = [
                o for o in offers
                if (o.review_score is not None and o.review_score >= params.min_review_score)
            ]
        if params.max_price_per_night is not None:
            offers = [
                o for o in offers
                if o.price_per_night <= params.max_price_per_night
            ]
        # required_amenities is best-effort and likely a no-op for Airbnb
        # (search response is amenity-light); skip to avoid false negatives.

        if not offers:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                "Airbnb listings were returned but all were filtered out by your "
                "criteria. Try relaxing min_review_score or max_price_per_night.",
            )

        return offers
