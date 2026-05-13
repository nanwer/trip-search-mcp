"""Normalize pyairbnb's dict output into our StayOffer shape.

pyairbnb returns a `list[dict]` of listings. Each listing exposes the
fields we need (name, room_id, coordinates, price, rating, images)
but with different key names and a price structure that differs from
SerpAPI's.

This file lives in airbnb_backend/ rather than serpapi_hotels_backend/
because the input shape is provider-specific. The OUTPUT (StayOffer) is
shared with the SerpAPI normalizer.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

from trip_search_mcp.models import Source, StayOffer

_IMAGE_CAP = 5


def _nights_between(check_in: str, check_out: str) -> int:
    ci = date.fromisoformat(check_in)
    co = date.fromisoformat(check_out)
    return (co - ci).days


def _safe_get(d: dict, *keys, default=None):
    """Walk nested dict keys, returning default at the first missing one."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _airbnb_booking_url(room_id: str | int | None, check_in: str, check_out: str) -> str:
    """Synthesize the canonical Airbnb listing URL with dates pre-filled."""
    if room_id is None:
        return "https://www.airbnb.com"
    return (
        f"https://www.airbnb.com/rooms/{room_id}"
        f"?check_in={check_in}&check_out={check_out}"
    )


def _extract_images(listing: dict) -> list[str]:
    """pyairbnb surfaces images as either a list of dicts (with `picture`
    field) or a list of strings. Be defensive."""
    raw = listing.get("images") or []
    out: list[str] = []
    for item in raw[:_IMAGE_CAP]:
        if isinstance(item, dict):
            url = item.get("picture") or item.get("url")
        elif isinstance(item, str):
            url = item
        else:
            url = None
        if url:
            out.append(url)
    return out


def _extract_price_per_night(listing: dict) -> float | None:
    """pyairbnb's price keys have varied across versions. Try a few."""
    # Most common (per pyairbnb 2.2.x): listing["price"]["unit"]["amount"]
    amount = _safe_get(listing, "price", "unit", "amount")
    if isinstance(amount, (int, float)):
        return float(amount)
    # Sometimes: listing["price"]["amount"]
    amount = _safe_get(listing, "price", "amount")
    if isinstance(amount, (int, float)):
        return float(amount)
    # Top-level numeric fallback.
    amount = listing.get("price_per_night")
    if isinstance(amount, (int, float)):
        return float(amount)
    return None


def _to_offer(
    listing: dict,
    *,
    check_in: str,
    check_out: str,
    currency: str,
) -> StayOffer | None:
    """Translate one pyairbnb listing dict into a StayOffer.

    Returns None when essential fields are missing (no name, no price).
    """
    name = listing.get("name") or listing.get("title")
    if not name:
        return None

    per_night = _extract_price_per_night(listing)
    if per_night is None:
        return None

    nights = _nights_between(check_in, check_out)
    total = per_night * nights if nights else per_night

    room_id = listing.get("room_id") or listing.get("id")
    offer_id = f"airbnb:{room_id}" if room_id is not None else f"airbnb:{quote_plus(name)}"

    coords = listing.get("coordinates") or {}
    lat = coords.get("latitude") if isinstance(coords, dict) else None
    lon = coords.get("longitude") if isinstance(coords, dict) else None
    if lat is None and "lat" in listing:
        lat = listing.get("lat")
    if lon is None and "lng" in listing:
        lon = listing.get("lng")

    rating = listing.get("rating") or {}
    if isinstance(rating, dict):
        review_score = rating.get("value") or rating.get("average")
        review_count = rating.get("count") or rating.get("reviewsCount")
    else:
        review_score = float(rating) if isinstance(rating, (int, float)) else None
        review_count = listing.get("review_count")

    # Convert Airbnb's 5-star scale to our standard 0-5 (already matches).
    if isinstance(review_score, (int, float)):
        review_score = float(review_score)

    return StayOffer(
        offer_id=offer_id,
        name=str(name),
        check_in_date=check_in,
        check_out_date=check_out,
        nights=nights,
        price_total=float(total),
        price_per_night=float(per_night),
        currency=currency,
        category="vacation_rental",  # Airbnb listings are always rentals
        star_rating=None,             # Airbnb doesn't use hotel-class stars
        review_score=review_score,
        review_count=review_count if isinstance(review_count, int) else None,
        address=None,                 # not surfaced by pyairbnb's search
        latitude=lat if isinstance(lat, (int, float)) else None,
        longitude=lon if isinstance(lon, (int, float)) else None,
        amenities=[],                 # pyairbnb's search response is amenity-light
        images=_extract_images(listing),
        description=None,
        bedrooms=listing.get("bedrooms"),
        bathrooms=listing.get("bathrooms"),
        sleeps=listing.get("person_capacity") or listing.get("max_guest"),
        hotel_type="airbnb rental",
        sources=[Source(name="Airbnb", price_per_night=float(per_night))],
        booking_url=_airbnb_booking_url(room_id, check_in, check_out),
    )


def normalize_listings(
    listings: list[dict],
    *,
    check_in: str,
    check_out: str,
    currency: str,
) -> list[StayOffer]:
    """Convert a list of pyairbnb listing dicts → list of StayOffer.
    Listings with missing essentials are silently dropped."""
    offers: list[StayOffer] = []
    for listing in listings or []:
        offer = _to_offer(
            listing, check_in=check_in, check_out=check_out, currency=currency,
        )
        if offer is not None:
            offers.append(offer)
    return offers
