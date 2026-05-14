"""Translate SerpAPI Tripadvisor response into our ActivityOffer shape.

Pure functions, no I/O. `place_type` → `ActivityType` mapping is the
key bit:
  ATTRACTION_PRODUCT → "experience" (bookable tour with a future
                        Viator URL via get_activity_details)
  ATTRACTION         → "sight" (free / non-bookable landmark)
"""
from __future__ import annotations

from trip_search_mcp.models import (
    ActivityOffer,
    ActivityType,
    HighlightedReview,
    PlaceTypeFilter,
)
from trip_search_mcp.tripadvisor_backend.raw import (
    SerpTripadvisorPlace,
    SerpTripadvisorResponse,
)


def _map_place_type(raw_type: str | None) -> ActivityType:
    """SerpAPI's place_type → our ActivityType.

    The two known values from Phase 0 are ATTRACTION (free attractions)
    and ATTRACTION_PRODUCT (bookable experiences/tours). Anything else
    falls back to SIGHT to avoid misleading the LLM into expecting a
    Viator booking URL that doesn't exist.
    """
    if raw_type and raw_type.upper() == "ATTRACTION_PRODUCT":
        return ActivityType.EXPERIENCE
    return ActivityType.SIGHT


def _to_offer(raw: SerpTripadvisorPlace) -> ActivityOffer | None:
    if not raw.title or not raw.place_id:
        return None
    if not raw.link:
        # Without a booking_url the result isn't actionable.
        return None

    activity_type = _map_place_type(raw.place_type)

    highlighted = None
    if raw.highlighted_review and raw.highlighted_review.text:
        highlighted = HighlightedReview(
            text=raw.highlighted_review.text,
            mention_count=raw.highlighted_review.mention_count,
        )

    return ActivityOffer(
        offer_id=raw.place_id,
        name=raw.title,
        activity_type=activity_type,
        rating=raw.rating,
        review_count=raw.reviews,
        description=raw.description,
        location=raw.location,
        thumbnail=raw.thumbnail,
        highlighted_review=highlighted,
        booking_url=raw.link,
    )


def _passes_place_type(offer: ActivityOffer, filt: PlaceTypeFilter) -> bool:
    if filt is PlaceTypeFilter.BOTH:
        return True
    if filt is PlaceTypeFilter.SIGHTS:
        return offer.activity_type is ActivityType.SIGHT
    if filt is PlaceTypeFilter.EXPERIENCES:
        return offer.activity_type is ActivityType.EXPERIENCE
    return True


def _passes_min_rating(offer: ActivityOffer, min_rating: float | None) -> bool:
    if min_rating is None:
        return True
    if offer.rating is None:
        return False
    return offer.rating >= min_rating


def build_offers(
    response: SerpTripadvisorResponse,
    *,
    place_type_filter: PlaceTypeFilter,
    min_rating: float | None,
    limit: int,
) -> list[ActivityOffer]:
    """Normalize, filter, dedupe by offer_id, cap at limit."""
    seen: set[str] = set()
    offers: list[ActivityOffer] = []
    for raw in response.places:
        offer = _to_offer(raw)
        if offer is None:
            continue
        if offer.offer_id in seen:
            continue
        if not _passes_place_type(offer, place_type_filter):
            continue
        if not _passes_min_rating(offer, min_rating):
            continue
        seen.add(offer.offer_id)
        offers.append(offer)
        if len(offers) >= limit:
            break
    return offers
