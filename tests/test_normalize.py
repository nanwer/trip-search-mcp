"""Tests for fli_backend.normalize.

Tests use fixtures of pre-validated `FlightResult` instances (see conftest).
The normalizer is pure — no I/O — so these are straightforward.
"""
from urllib.parse import parse_qs, urlparse

from flights_mcp.fli_backend.normalize import (
    _compute_offer_id,
    _iso_duration,
    booking_url_for,
    build_offers,
)
from flights_mcp.models import CabinClass


# ----- formatting helpers ----------------------------------------------------


def test_iso_duration_handles_hours_and_minutes():
    assert _iso_duration(220) == "PT3H40M"


def test_iso_duration_handles_minutes_only():
    assert _iso_duration(45) == "PT45M"


def test_iso_duration_handles_zero():
    assert _iso_duration(0) == "PT0M"


def test_iso_duration_clamps_negatives_to_zero():
    assert _iso_duration(-5) == "PT0M"


# ----- booking_url -----------------------------------------------------------


def test_booking_url_one_way_contains_origin_destination_date():
    url = booking_url_for("HEL", "IAD", "2026-05-18", None)
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.google.com"
    assert parsed.path == "/travel/flights"
    q = parse_qs(parsed.query)["q"][0]
    assert "HEL" in q
    assert "IAD" in q
    assert "2026-05-18" in q
    assert "through" not in q.lower()


def test_booking_url_round_trip_contains_both_dates():
    url = booking_url_for("HEL", "IAD", "2026-05-18", "2026-05-29")
    parsed = urlparse(url)
    q = parse_qs(parsed.query)["q"][0]
    assert "2026-05-18" in q
    assert "2026-05-29" in q
    assert "through" in q.lower()


def test_booking_url_is_url_encoded():
    url = booking_url_for("HEL", "IAD", "2026-05-18", None)
    assert " " not in url
    assert "+" in urlparse(url).query


# ----- offer_id --------------------------------------------------------------


def test_offer_id_is_deterministic():
    a = _compute_offer_id(
        airlines=["FI", "AY"], flight_numbers=["FI343", "AY15"],
        departure_date="2026-05-18", return_date="2026-05-29",
    )
    b = _compute_offer_id(
        airlines=["AY", "FI"], flight_numbers=["AY15", "FI343"],
        departure_date="2026-05-18", return_date="2026-05-29",
    )
    # Sorted inputs → same hash regardless of caller ordering.
    assert a == b


def test_offer_id_distinguishes_one_way_from_round_trip_on_same_outbound():
    one_way = _compute_offer_id(
        airlines=["FI"], flight_numbers=["FI343"],
        departure_date="2026-05-18", return_date=None,
    )
    round_trip = _compute_offer_id(
        airlines=["FI"], flight_numbers=["FI343"],
        departure_date="2026-05-18", return_date="2026-05-29",
    )
    assert one_way != round_trip


# ----- build_offers (one-way) ------------------------------------------------


def test_build_one_way_offers(fli_one_way):
    booking_url = booking_url_for("HEL", "IAD", "2026-05-18", None)
    offers = build_offers(
        fli_one_way,
        cabin=CabinClass.ECONOMY,
        adults=1,
        booking_url=booking_url,
        departure_date="2026-05-18",
        return_date=None,
        limit=20,
    )
    assert len(offers) == 2

    direct = offers[0]
    assert direct.inbound is None
    assert direct.outbound.stops == 0
    assert direct.outbound.segments[0].airline == "AY"
    assert direct.outbound.segments[0].flight_number == "AY17"
    assert direct.outbound.segments[0].departure_time_local == "2026-05-18T13:00:00"
    assert direct.outbound.segments[0].cabin is CabinClass.ECONOMY
    assert direct.outbound.duration == "PT10H30M"  # 630 minutes
    assert direct.total_price == 460.0
    assert direct.currency == "EUR"
    assert direct.booking_url == booking_url

    connecting = offers[1]
    assert connecting.outbound.stops == 1
    assert set(connecting.airlines) == {"KL"}


def test_build_one_way_respects_limit(fli_one_way):
    offers = build_offers(
        fli_one_way,
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url="x", departure_date="2026-05-18", return_date=None, limit=1,
    )
    assert len(offers) == 1


def test_build_one_way_divides_price_per_adult(fli_one_way):
    offers = build_offers(
        fli_one_way,
        cabin=CabinClass.ECONOMY, adults=2,
        booking_url="x", departure_date="2026-05-18", return_date=None, limit=1,
    )
    assert offers[0].total_price == 460.0
    assert offers[0].price_per_adult == 230.0


# ----- build_offers (round-trip) --------------------------------------------


def test_build_round_trip_offers(fli_round_trip):
    booking_url = booking_url_for("HEL", "IAD", "2026-05-18", "2026-05-29")
    offers = build_offers(
        fli_round_trip,
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url=booking_url,
        departure_date="2026-05-18", return_date="2026-05-29",
        limit=20,
    )
    assert len(offers) == 2

    first = offers[0]
    assert first.inbound is not None
    assert first.outbound.stops == 1
    assert first.inbound.stops == 1
    assert first.outbound.duration == "PT11H20M"  # 680 minutes
    assert first.inbound.duration == "PT11H30M"   # 690 minutes
    assert first.total_price == 666.0
    assert first.currency == "EUR"
    assert set(first.airlines) == {"FI"}
    assert first.validating_airline == "FI"
    assert first.booking_url == booking_url


def test_every_offer_has_populated_booking_url(fli_round_trip):
    booking_url = booking_url_for("HEL", "IAD", "2026-05-18", "2026-05-29")
    offers = build_offers(
        fli_round_trip,
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url=booking_url,
        departure_date="2026-05-18", return_date="2026-05-29", limit=20,
    )
    for offer in offers:
        assert offer.booking_url == booking_url
        assert offer.booking_url


def test_fli_only_nulls_carry_through(fli_one_way):
    """fli doesn't surface baggage, last-ticketing-date, or seat counts."""
    offers = build_offers(
        fli_one_way,
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url="x", departure_date="2026-05-18", return_date=None, limit=1,
    )
    offer = offers[0]
    assert offer.baggage_allowance is None
    assert offer.last_ticketing_date is None
    assert offer.seats_available is None
    assert offer.fare_basis == ""
