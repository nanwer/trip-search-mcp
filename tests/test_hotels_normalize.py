"""Tests for serpapi_hotels_backend.normalize.

Pure-function tests against the synthetic fixture in conftest.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from trip_search_mcp.models import HotelSortBy
from trip_search_mcp.serpapi_hotels_backend.normalize import (
    _compute_offer_id,
    booking_url_for,
    build_offers,
)
from trip_search_mcp.serpapi_hotels_backend.raw import SerpHotelsResponse


def _build(resp_dict, **overrides):
    """Run build_offers with sensible defaults; tests override what they care about."""
    response = SerpHotelsResponse.model_validate(resp_dict)
    kwargs = dict(
        location="Tampere",
        check_in="2026-06-15",
        check_out="2026-06-18",
        currency="EUR",
        sort_by=HotelSortBy.BEST,
        min_rating=None,
        min_review_score=None,
        max_price_per_night=None,
        required_amenities=None,
        limit=10,
    )
    kwargs.update(overrides)
    return build_offers(response, **kwargs)


# ----- booking_url -----------------------------------------------------------


def test_booking_url_search_fallback_when_no_property_token():
    """No token → fall back to the pre-filled search URL."""
    url = booking_url_for("Notting Hill, London", "2026-06-15", "2026-06-18")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.google.com"
    assert parsed.path == "/travel/hotels"
    q = parse_qs(parsed.query)["q"][0]
    assert "Notting Hill, London" in q
    assert "2026-06-15" in q
    assert "2026-06-18" in q
    assert " " not in url  # spaces URL-encoded


def test_booking_url_deep_links_to_property_entity_when_token_present():
    """Token → deep-link to the property's entity page with dates pre-filled."""
    token = "ChoI4oTlisut8aeaARoNL2cvMTFkZjgyOXc2ORAB"
    url = booking_url_for(
        "Tampere", "2026-06-15", "2026-06-18",
        property_token=token,
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.google.com"
    assert parsed.path == f"/travel/hotels/entity/{token}"
    q = parse_qs(parsed.query)
    assert q["check_in"] == ["2026-06-15"]
    assert q["check_out"] == ["2026-06-18"]


def test_booking_urls_differ_per_property(serpapi_hotels_success):
    """Regression: every offer should have a DISTINCT booking_url (was the
    same generic search URL before the property_token fix)."""
    offers = _build(serpapi_hotels_success)
    urls = [o.booking_url for o in offers]
    assert len(set(urls)) == len(urls), f"booking_urls not distinct: {urls}"


# ----- offer_id --------------------------------------------------------------


def test_offer_id_prefers_property_token_when_present():
    """SerpAPI's property_token is the canonical stable identifier."""
    token = "ChoI4oTlisut8aeaARoNL2cvMTFkZjgyOXc2ORAB"
    assert _compute_offer_id(
        property_token=token,
        name="anything",
        address=None,
        check_in="2026-06-15",
        check_out="2026-06-18",
    ) == token


def test_offer_id_falls_back_to_hash_when_token_missing():
    a = _compute_offer_id(
        property_token=None,
        name="Foo Hotel",
        address="123 Test St",
        check_in="2026-06-15",
        check_out="2026-06-18",
    )
    b = _compute_offer_id(
        property_token=None,
        name="Foo Hotel",
        address="123 Test St",
        check_in="2026-06-15",
        check_out="2026-06-18",
    )
    assert a == b
    assert a.startswith("h:")  # prefix marks the fallback case


def test_offer_id_hash_differs_for_different_inputs():
    base = dict(
        property_token=None, name="Foo Hotel",
        address="123 Test St", check_in="2026-06-15", check_out="2026-06-18",
    )
    diff_name = {**base, "name": "Bar Hotel"}
    diff_date = {**base, "check_in": "2026-06-16"}
    assert _compute_offer_id(**base) != _compute_offer_id(**diff_name)
    assert _compute_offer_id(**base) != _compute_offer_id(**diff_date)


# ----- build_offers (happy path) --------------------------------------------


def test_build_offers_from_fixture(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success)
    assert len(offers) == 3
    # Fixture order is preserved with BEST (default).
    assert offers[0].name == "Lillan Hotel & Kök"
    assert offers[0].star_rating == 4
    assert offers[0].review_score == 4.6
    assert offers[0].review_count == 686
    assert offers[0].nights == 3
    assert offers[0].price_total == 333.0
    assert offers[0].price_per_night == 111.0
    assert offers[0].currency == "EUR"
    assert offers[0].hotel_type == "hotel"
    assert "Free breakfast" in offers[0].amenities
    assert offers[0].latitude == 61.5
    # Now deep-links to the specific property's entity page.
    assert offers[0].booking_url.startswith(
        "https://www.google.com/travel/hotels/entity/"
    )


def test_build_offers_caps_images_at_5(serpapi_hotels_success):
    """Lillan has 6 images in the fixture; we cap at 5."""
    offers = _build(serpapi_hotels_success)
    lillan = next(o for o in offers if o.name.startswith("Lillan"))
    assert len(lillan.images) == 5


def test_build_offers_handles_sparse_data(serpapi_hotels_success):
    """The Backpackers entry has hotel_class=null — star_rating should pass through as None."""
    offers = _build(serpapi_hotels_success)
    bp = next(o for o in offers if "Backpackers" in o.name)
    assert bp.star_rating is None
    assert bp.hotel_type == "vacation rental"


def test_build_offers_uses_property_token_as_offer_id(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success)
    for offer in offers:
        # All three fixture entries have a property_token.
        assert not offer.offer_id.startswith("h:")
        assert offer.offer_id.startswith("Cho")


# ----- sort_by ---------------------------------------------------------------


def test_sort_by_price_low(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success, sort_by=HotelSortBy.PRICE_LOW)
    assert [o.price_total for o in offers] == [105.0, 333.0, 720.0]


def test_sort_by_price_high(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success, sort_by=HotelSortBy.PRICE_HIGH)
    assert [o.price_total for o in offers] == [720.0, 333.0, 105.0]


def test_sort_by_rating_orders_by_star_then_review(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success, sort_by=HotelSortBy.RATING)
    # Torni (5★) first, Lillan (4★) second, Backpackers (no stars → treated as 0) last.
    assert [o.name for o in offers] == [
        "Solo Sokos Hotel Torni",
        "Lillan Hotel & Kök",
        "Tampere Backpackers Loft",
    ]


def test_sort_by_review_score(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success, sort_by=HotelSortBy.REVIEW_SCORE)
    # 4.8 > 4.6 > 3.8
    assert [o.review_score for o in offers] == [4.8, 4.6, 3.8]


def test_sort_by_best_preserves_fixture_order(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success, sort_by=HotelSortBy.BEST)
    assert [o.name for o in offers] == [
        "Lillan Hotel & Kök",
        "Tampere Backpackers Loft",
        "Solo Sokos Hotel Torni",
    ]


# ----- post-filters ----------------------------------------------------------


def test_min_rating_excludes_properties_without_star(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success, min_rating=4)
    # Backpackers has no star rating → excluded. Lillan (4) and Torni (5) pass.
    names = {o.name for o in offers}
    assert "Tampere Backpackers Loft" not in names
    assert len(offers) == 2


def test_min_review_score_filters(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success, min_review_score=4.5)
    # Only Lillan (4.6) and Torni (4.8) pass.
    assert len(offers) == 2
    assert all(o.review_score >= 4.5 for o in offers)


def test_max_price_per_night_filters(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success, max_price_per_night=150.0)
    # Only Lillan ($111) and Backpackers ($35) pass.
    assert len(offers) == 2
    assert all(o.price_per_night <= 150.0 for o in offers)


def test_required_amenities_substring_match(serpapi_hotels_success):
    """Case-insensitive substring match against the property's amenity strings."""
    offers = _build(serpapi_hotels_success, required_amenities=["pool"])
    # Only Torni has Pool.
    assert len(offers) == 1
    assert offers[0].name == "Solo Sokos Hotel Torni"


def test_required_amenities_all_must_match(serpapi_hotels_success):
    """When multiple amenities are required, ALL must be present."""
    offers = _build(serpapi_hotels_success, required_amenities=["wifi", "breakfast"])
    # Only Lillan has both Free Wi-Fi AND Free breakfast.
    assert len(offers) == 1
    assert offers[0].name == "Lillan Hotel & Kök"


def test_required_amenities_no_match(serpapi_hotels_success):
    offers = _build(serpapi_hotels_success, required_amenities=["heliport"])
    assert offers == []


def test_filters_apply_before_limit(serpapi_hotels_success):
    """A tight filter shouldn't silently shrink the result list because
    of pagination — limit is applied AFTER filtering."""
    offers = _build(serpapi_hotels_success, min_review_score=4.5, limit=1)
    # Two pass the filter (4.6 and 4.8); limit truncates to 1.
    assert len(offers) == 1


# ----- empty input -----------------------------------------------------------


def test_empty_response_returns_empty_list(serpapi_hotels_empty):
    offers = _build(serpapi_hotels_empty)
    assert offers == []
