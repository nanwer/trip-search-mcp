"""Translate SerpAPI google_hotels response into our HotelOffer shape.

Pure functions, no I/O. Called by `client.py` after each upstream HTTP call.
All filtering and sorting that isn't naturally handled by SerpAPI's query
parameters happens here as a post-pass so behavior is predictable.
"""
from __future__ import annotations

import hashlib
from datetime import date
from urllib.parse import quote_plus

from flights_mcp.models import HotelOffer, HotelSortBy
from flights_mcp.serpapi_hotels_backend.raw import (
    SerpHotelProperty,
    SerpHotelsResponse,
)

# Tool-side cap on images to keep response payloads tight. SerpAPI returns
# 8-9 per property; 5 covers a card carousel without bloating Claude's
# context.
_IMAGE_CAP = 5


def booking_url_for(
    location: str,
    check_in: str,
    check_out: str,
    *,
    property_token: str | None = None,
) -> str:
    """Synthesize a Google Hotels URL for this offer.

    When `property_token` is present (true for ~all SerpAPI list results),
    we deep-link to the specific property's Google Hotels entity page
    with check-in/check-out pre-filled. This is the same property_token
    SerpAPI exposes for follow-up `property_details` calls; Google accepts
    it directly in the URL path (returns 302 to the property page).

    When `property_token` is missing — a rare fallback path — we fall back
    to a search URL pre-filled with the user's query so the user still
    lands somewhere useful.
    """
    if property_token:
        return (
            f"https://www.google.com/travel/hotels/entity/{property_token}"
            f"?check_in={check_in}&check_out={check_out}"
        )
    q = f"Hotels in {location} from {check_in} to {check_out}"
    return f"https://www.google.com/travel/hotels?q={quote_plus(q)}"


def _nights_between(check_in: str, check_out: str) -> int:
    ci = date.fromisoformat(check_in)
    co = date.fromisoformat(check_out)
    return (co - ci).days


def _compute_offer_id(
    *,
    property_token: str | None,
    name: str,
    address: str | None,
    check_in: str,
    check_out: str,
) -> str:
    """Use SerpAPI's `property_token` when present — it's their canonical
    stable identifier and is the right input for any future per-property
    follow-up call.

    Fall back to a SHA256 hash of (name, address-or-empty, check-in,
    check-out) when the token is missing. The hash is deterministic per
    query input. Document which case yielded which kind of id by leaving
    the token-based id raw and prefixing the hash-based one with "h:" so
    callers reading the id later can tell them apart.
    """
    if property_token:
        return property_token
    payload = "|".join([name, address or "", check_in, check_out])
    return "h:" + hashlib.sha256(payload.encode()).hexdigest()


def _to_offer(
    raw: SerpHotelProperty,
    *,
    location: str,
    check_in: str,
    check_out: str,
    currency: str,
) -> HotelOffer | None:
    """Build a single HotelOffer. Returns None if the property is missing
    enough data to be useful (no name, no price)."""
    if not raw.name:
        return None
    total = (raw.total_rate.extracted_lowest if raw.total_rate else None)
    per_night = (raw.rate_per_night.extracted_lowest if raw.rate_per_night else None)
    nights = _nights_between(check_in, check_out)
    if total is None and per_night is None:
        return None
    if total is None:
        total = per_night * nights
    if per_night is None:
        per_night = total / nights if nights else total

    images: list[str] = []
    for img in raw.images[:_IMAGE_CAP]:
        # Prefer the larger original; fall back to thumbnail.
        url = img.original_image or img.thumbnail
        if url:
            images.append(url)

    return HotelOffer(
        offer_id=_compute_offer_id(
            property_token=raw.property_token,
            name=raw.name,
            address=None,  # SerpAPI's list endpoint doesn't expose address
            check_in=check_in,
            check_out=check_out,
        ),
        name=raw.name,
        check_in_date=check_in,
        check_out_date=check_out,
        nights=nights,
        price_total=float(total),
        price_per_night=float(per_night),
        currency=currency,
        star_rating=raw.extracted_hotel_class,
        review_score=raw.overall_rating,
        review_count=raw.reviews,
        address=None,
        latitude=raw.gps_coordinates.latitude if raw.gps_coordinates else None,
        longitude=raw.gps_coordinates.longitude if raw.gps_coordinates else None,
        amenities=list(raw.amenities),
        images=images,
        description=raw.description,
        hotel_type=raw.type,
        booking_url=booking_url_for(
            location,
            check_in,
            check_out,
            property_token=raw.property_token,
        ),
    )


# ----- post-filters (apply when SerpAPI doesn't natively filter) ------------


def _passes_min_rating(offer: HotelOffer, min_rating: int | None) -> bool:
    if min_rating is None:
        return True
    if offer.star_rating is None:
        # Missing data: a "no info" property doesn't satisfy a minimum claim.
        return False
    return offer.star_rating >= min_rating


def _passes_min_review_score(offer: HotelOffer, min_review_score: float | None) -> bool:
    if min_review_score is None:
        return True
    if offer.review_score is None:
        return False
    return offer.review_score >= min_review_score


def _passes_max_price(offer: HotelOffer, max_price_per_night: float | None) -> bool:
    if max_price_per_night is None:
        return True
    return offer.price_per_night <= max_price_per_night


def _passes_required_amenities(
    offer: HotelOffer, required: list[str] | None,
) -> bool:
    """Best-effort match, case-insensitive AND punctuation-insensitive.

    SerpAPI's amenity strings are free-text ('Free breakfast', 'Free Wi-Fi',
    'Pet-friendly'). Users naturally say "wifi" not "wi-fi" and "petfriendly"
    not "pet-friendly". We strip non-alphanumeric chars on both sides before
    substring-matching so the obvious cases work.

    Documented as 'best effort' in the tool description.
    """
    if not required:
        return True
    def _norm(s: str) -> str:
        return "".join(c.lower() for c in s if c.isalnum())
    haystack = "|".join(_norm(a) for a in offer.amenities)
    return all(_norm(want) in haystack for want in required)


def _sort_key(offer: HotelOffer, sort_by: HotelSortBy):
    """Return a tuple used as a stable sort key. Lower-is-better in every
    branch, so we use negative for descending dimensions."""
    if sort_by is HotelSortBy.PRICE_LOW:
        return (offer.price_total,)
    if sort_by is HotelSortBy.PRICE_HIGH:
        return (-offer.price_total,)
    if sort_by is HotelSortBy.RATING:
        # Star rating first (higher better), then review_score as tie-break.
        return (
            -(offer.star_rating or 0),
            -(offer.review_score or 0.0),
        )
    if sort_by is HotelSortBy.REVIEW_SCORE:
        # Review score first, then review count as tie-break (rated 9.5
        # with 5 reviews shouldn't beat 9.4 with 5000).
        return (
            -(offer.review_score or 0.0),
            -(offer.review_count or 0),
        )
    # BEST: preserve SerpAPI's returned order. Use a zero key so Python's
    # stable sort is a no-op on input order.
    return (0,)


def build_offers(
    response: SerpHotelsResponse,
    *,
    location: str,
    check_in: str,
    check_out: str,
    currency: str,
    sort_by: HotelSortBy,
    min_rating: int | None,
    min_review_score: float | None,
    max_price_per_night: float | None,
    required_amenities: list[str] | None,
    limit: int,
) -> list[HotelOffer]:
    """Normalize, post-filter, sort, and truncate to `limit`.

    Post-filters run BEFORE truncation so a tight filter doesn't silently
    shrink the result list because of unrelated pagination cutoff.
    """
    offers: list[HotelOffer] = []
    for raw in response.properties:
        offer = _to_offer(
            raw, location=location, check_in=check_in,
            check_out=check_out, currency=currency,
        )
        if offer is None:
            continue
        if not _passes_min_rating(offer, min_rating):
            continue
        if not _passes_min_review_score(offer, min_review_score):
            continue
        if not _passes_max_price(offer, max_price_per_night):
            continue
        if not _passes_required_amenities(offer, required_amenities):
            continue
        offers.append(offer)

    offers.sort(key=lambda o: _sort_key(o, sort_by))
    return offers[:limit]
