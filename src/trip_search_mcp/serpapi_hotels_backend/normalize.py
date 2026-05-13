"""Translate SerpAPI google_hotels response into our StayOffer shape.

Pure functions, no I/O. Called by `client.py` after each upstream HTTP call.
All filtering and sorting that isn't naturally handled by SerpAPI's query
parameters happens here as a post-pass so behavior is predictable.
"""
from __future__ import annotations

import hashlib
from datetime import date
from urllib.parse import quote_plus

from trip_search_mcp.models import HotelSortBy, Source, StayOffer
from trip_search_mcp.serpapi_hotels_backend.raw import (
    SerpHotelProperty,
    SerpHotelsResponse,
)

# Canonical OTA names. SerpAPI returns sources with inconsistent casing
# ("booking.com" vs "Booking.com" vs "Booking.Com" across queries) — we
# rewrite to a known-canonical form. Title-casing a name like
# "booking.com" produces "Booking.Com" (uppercase C), which is wrong, so
# this map exists. Anything not in the map gets title-cased.
_CANONICAL_OTA_NAMES: dict[str, str] = {
    "booking.com": "Booking.com",
    "hotels.com": "Hotels.com",
    "bluepillow.com": "Bluepillow.com",
    "expedia": "Expedia",
    "expedia.com": "Expedia",
    "agoda": "Agoda",
    "agoda.com": "Agoda",
    "vrbo": "VRBO",
    "vrbo.com": "VRBO",
    "airbnb": "Airbnb",
    "airbnb.com": "Airbnb",
    "vacasa": "Vacasa",
    "vacasa.com": "Vacasa",
    "trivago": "Trivago",
    "trip.com": "Trip.com",
    "kayak": "Kayak",
}


def _canonicalize_source_name(raw_name: str | None) -> str:
    if not raw_name:
        return "Unknown"
    key = raw_name.strip().casefold()
    if key in _CANONICAL_OTA_NAMES:
        return _CANONICAL_OTA_NAMES[key]
    # Fall back to title case, but preserve dotted suffixes like ".com" lowercase.
    base = raw_name.strip()
    if "." in base:
        head, _, tail = base.rpartition(".")
        return f"{head.title()}.{tail.lower()}" if head else base
    return base.title()


def _category_from_type(raw_type: str | None) -> str:
    """Map SerpAPI's free-text `type` to our canonical category.

    Per Phase 0 fixtures the values observed are "hotel" and "vacation
    rental"; we map both. Unknown values fall back to "hotel" because
    that's the only category SerpAPI returns when vacation_rentals=false
    and the historical default behavior.
    """
    if raw_type and "vacation" in raw_type.casefold():
        return "vacation_rental"
    return "hotel"


def _parse_essential_info(facts: list[str]) -> tuple[int | None, int | None, int | None]:
    """Pull (bedrooms, bathrooms, sleeps) out of SerpAPI's essential_info.

    Phase 0 captured strings like "Sleeps 8", "2 bedrooms", "1 bathroom".
    Singular and plural both appear. Anything we can't parse stays None.
    """
    bedrooms: int | None = None
    bathrooms: int | None = None
    sleeps: int | None = None
    for fact in facts:
        s = (fact or "").strip().casefold()
        if not s:
            continue
        # Match patterns like "2 bedrooms" / "1 bedroom" / "Sleeps 8".
        parts = s.split()
        # "2 bedrooms" / "1 bedroom"
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].rstrip("s") == "bedroom":
            bedrooms = int(parts[0])
            continue
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].rstrip("s") == "bathroom":
            bathrooms = int(parts[0])
            continue
        # "Sleeps 8"
        if len(parts) >= 2 and parts[0] == "sleeps" and parts[1].isdigit():
            sleeps = int(parts[1])
            continue
    return bedrooms, bathrooms, sleeps


def _sources_from_prices(prices) -> list[Source]:
    """Build the offer's `sources` list from SerpAPI's `prices` array."""
    out: list[Source] = []
    for p in prices or []:
        rpn = p.rate_per_night
        out.append(
            Source(
                name=_canonicalize_source_name(p.source),
                price_per_night=(rpn.extracted_lowest if rpn else None),
                before_taxes_fees=(rpn.extracted_before_taxes_fees if rpn else None),
            )
        )
    return out

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
) -> StayOffer | None:
    """Build a single StayOffer. Returns None if the property is missing
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

    bedrooms, bathrooms, sleeps = _parse_essential_info(raw.essential_info)

    return StayOffer(
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
        category=_category_from_type(raw.type),
        star_rating=raw.extracted_hotel_class,
        review_score=raw.overall_rating,
        review_count=raw.reviews,
        address=None,
        latitude=raw.gps_coordinates.latitude if raw.gps_coordinates else None,
        longitude=raw.gps_coordinates.longitude if raw.gps_coordinates else None,
        amenities=list(raw.amenities),
        images=images,
        description=raw.description,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        sleeps=sleeps,
        hotel_type=raw.type,
        sources=_sources_from_prices(raw.prices),
        booking_url=booking_url_for(
            location,
            check_in,
            check_out,
            property_token=raw.property_token,
        ),
    )


# ----- post-filters (apply when SerpAPI doesn't natively filter) ------------


def _passes_min_rating(offer: StayOffer, min_rating: int | None) -> bool:
    if min_rating is None:
        return True
    if offer.star_rating is None:
        # Missing data: a "no info" property doesn't satisfy a minimum claim.
        return False
    return offer.star_rating >= min_rating


def _passes_min_review_score(offer: StayOffer, min_review_score: float | None) -> bool:
    if min_review_score is None:
        return True
    if offer.review_score is None:
        return False
    return offer.review_score >= min_review_score


def _passes_max_price(offer: StayOffer, max_price_per_night: float | None) -> bool:
    if max_price_per_night is None:
        return True
    return offer.price_per_night <= max_price_per_night


def _passes_required_amenities(
    offer: StayOffer, required: list[str] | None,
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


def _sort_key(offer: StayOffer, sort_by: HotelSortBy):
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


def normalize_and_filter(
    response: SerpHotelsResponse,
    *,
    location: str,
    check_in: str,
    check_out: str,
    currency: str,
    min_rating: int | None,
    min_review_score: float | None,
    max_price_per_night: float | None,
    required_amenities: list[str] | None,
) -> list[StayOffer]:
    """Normalize raw SerpAPI properties into offers, then apply post-filters.

    NO sort, NO truncation — caller decides. Used both by the single-call
    `build_offers` path and the merge path (which needs to combine before
    sorting). Splitting this out makes the merge orchestration testable
    end-to-end without re-doing normalize work.
    """
    offers: list[StayOffer] = []
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
    return offers


def sort_and_truncate(
    offers: list[StayOffer], sort_by: HotelSortBy, limit: int,
) -> list[StayOffer]:
    """Sort by user preference and truncate to `limit`. Python's sort is
    stable, so for BEST (which returns a zero key) input order is preserved."""
    offers_sorted = sorted(offers, key=lambda o: _sort_key(o, sort_by))
    return offers_sorted[:limit]


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
) -> list[StayOffer]:
    """Normalize, post-filter, sort, and truncate to `limit`.

    Thin wrapper composing normalize_and_filter + sort_and_truncate, kept
    for the single-call code path. Merge path bypasses this and orchestrates
    dedup between the two pieces.
    """
    offers = normalize_and_filter(
        response,
        location=location, check_in=check_in, check_out=check_out,
        currency=currency,
        min_rating=min_rating, min_review_score=min_review_score,
        max_price_per_night=max_price_per_night,
        required_amenities=required_amenities,
    )
    return sort_and_truncate(offers, sort_by, limit)


# ----- merge / dedup --------------------------------------------------------


def _dedup_key_fallback(offer: StayOffer) -> tuple:
    """Stable tuple identifying a property when its property_token is
    missing or unreliable across category modes.

    Tolerance of 4 decimal places on lat/lon ≈ 11m at the equator —
    tight enough that two different properties on the same block don't
    collapse, loose enough that float-precision drift between SerpAPI's
    hotel and vacation_rental responses doesn't break dedup.
    """
    lat = round(offer.latitude, 4) if offer.latitude is not None else None
    lon = round(offer.longitude, 4) if offer.longitude is not None else None
    return (offer.name.casefold().strip(), lat, lon)


def merge_and_dedup(
    hotels: list[StayOffer], rentals: list[StayOffer],
) -> list[StayOffer]:
    """Combine hotel and rental offers, dedup with two-tier strategy.

    Pass 1: property_token equality. Properties without a token (rare)
    pass through to pass 2.
    Pass 2: (name.casefold(), round(lat,4), round(lon,4)) tuple.

    When duplicates collide, the lower-priced variant wins (price_per_night).
    This handles the case where the same property is listed in both modes
    at different rates — show the user the better deal, don't show both.

    Order of the input lists determines tie-breaking when prices match
    exactly (hotels first by convention — they're the historical default).
    """
    combined: list[StayOffer] = [*hotels, *rentals]
    if not combined:
        return []

    # Pass 1: bucket by property_token.
    by_token: dict[str, StayOffer] = {}
    no_token: list[StayOffer] = []
    for offer in combined:
        if offer.offer_id and not offer.offer_id.startswith("h:"):
            # Real property_token (not our SHA fallback).
            existing = by_token.get(offer.offer_id)
            if existing is None or offer.price_per_night < existing.price_per_night:
                by_token[offer.offer_id] = offer
        else:
            no_token.append(offer)

    # Pass 2: bucket the no-token offers by name+coords tuple.
    by_fallback: dict[tuple, StayOffer] = {}
    for offer in no_token:
        key = _dedup_key_fallback(offer)
        existing = by_fallback.get(key)
        if existing is None or offer.price_per_night < existing.price_per_night:
            by_fallback[key] = offer

    # Order: token-keyed first (in original encounter order), then fallback.
    # We preserve input order via dict insertion order in Python 3.7+.
    return [*by_token.values(), *by_fallback.values()]
