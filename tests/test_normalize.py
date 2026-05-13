"""Tests for fli_backend.normalize.

Tests use fixtures of pre-validated `FlightResult` instances (see conftest).
The normalizer is pure — no I/O — so these are straightforward.
"""
from urllib.parse import parse_qs, urlparse

import pytest

from flights_mcp.fli_backend.normalize import (
    _compute_offer_id,
    _iso_duration,
    booking_url_for,
    build_date_offers,
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


def test_offer_id_airlines_order_invariant():
    """Airlines are sorted set-like — caller order doesn't change the hash."""
    a = _compute_offer_id(
        airlines=["FI", "AY"],
        segments=[("FI343", "2026-05-18T15:00:00"), ("AY15", "2026-05-18T20:00:00")],
        departure_date="2026-05-18", return_date="2026-05-29",
    )
    b = _compute_offer_id(
        airlines=["AY", "FI"],
        segments=[("FI343", "2026-05-18T15:00:00"), ("AY15", "2026-05-18T20:00:00")],
        departure_date="2026-05-18", return_date="2026-05-29",
    )
    assert a == b


def test_offer_id_segments_order_matters():
    """Segment order IS meaningful — outbound-then-inbound ordering distinguishes
    itineraries that fly the same legs in different directions."""
    a = _compute_offer_id(
        airlines=["FI"],
        segments=[("FI343", "2026-05-18T15:00:00"), ("FI645", "2026-05-18T16:50:00")],
        departure_date="2026-05-18", return_date=None,
    )
    b = _compute_offer_id(
        airlines=["FI"],
        segments=[("FI645", "2026-05-18T16:50:00"), ("FI343", "2026-05-18T15:00:00")],
        departure_date="2026-05-18", return_date=None,
    )
    assert a != b


def test_offer_id_segment_timing_matters():
    """Same flight number on different days produces different hashes — the bug
    Phase 2.5 fixed was that timing wasn't in the hash."""
    a = _compute_offer_id(
        airlines=["FI"],
        segments=[("FI343", "2026-05-18T15:00:00")],
        departure_date="2026-05-18", return_date=None,
    )
    b = _compute_offer_id(
        airlines=["FI"],
        segments=[("FI343", "2026-05-19T15:00:00")],  # next-day variant
        departure_date="2026-05-18", return_date=None,
    )
    assert a != b


def test_offer_id_distinguishes_one_way_from_round_trip_on_same_outbound():
    one_way = _compute_offer_id(
        airlines=["FI"],
        segments=[("FI343", "2026-05-18T15:00:00")],
        departure_date="2026-05-18", return_date=None,
    )
    round_trip = _compute_offer_id(
        airlines=["FI"],
        segments=[("FI343", "2026-05-18T15:00:00")],
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


# ----- inbound_window post-filter -------------------------------------------


def test_inbound_window_exclusive_upper_bound_drops_hour_at_end(fli_round_trip):
    """Fixture inbound first-segment departures: entry[0]=20:30 (hour 20),
    entry[1]=19:00 (hour 19). Window 6-20 admits hours 6 through 19 ONLY
    (exclusive end), so hour 20 is dropped and hour 19 is kept."""
    offers = build_offers(
        fli_round_trip,
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url="x",
        departure_date="2026-05-18", return_date="2026-05-29",
        limit=20,
        inbound_window="6-20",
    )
    assert len(offers) == 1
    seg = offers[0].inbound.segments[0]
    assert seg.departure_time_local.startswith("2026-05-29T19:00")


def test_inbound_window_widening_includes_both(fli_round_trip):
    """Window 6-21 admits hours 6 through 20 → both fixture entries pass."""
    offers = build_offers(
        fli_round_trip,
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url="x",
        departure_date="2026-05-18", return_date="2026-05-29",
        limit=20,
        inbound_window="6-21",
    )
    assert len(offers) == 2


def test_inbound_window_can_filter_everything(fli_round_trip):
    """Tight window that excludes all inbound times → empty list."""
    offers = build_offers(
        fli_round_trip,
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url="x",
        departure_date="2026-05-18", return_date="2026-05-29",
        limit=20,
        inbound_window="6-15",
    )
    assert offers == []


def test_inbound_window_no_effect_on_one_way(fli_one_way):
    """One-way offers have inbound=None and pass the filter trivially."""
    offers = build_offers(
        fli_one_way,
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url="x",
        departure_date="2026-05-18", return_date=None,
        limit=20,
        inbound_window="6-7",  # tight window that would block round-trip
    )
    assert len(offers) == 2


def test_inbound_window_none_disables_filter(fli_round_trip):
    """Passing None for inbound_window leaves all offers untouched."""
    offers = build_offers(
        fli_round_trip,
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url="x",
        departure_date="2026-05-18", return_date="2026-05-29",
        limit=20,
        inbound_window=None,
    )
    assert len(offers) == 2


# ----- inbound_window boundary semantics (Phase 2.5 acceptance criteria) ----


@pytest.mark.parametrize("hour,window,expected", [
    (20, "8-20", False),  # exclusive end: hour 20 is NOT inside "8-20"
    (19, "8-20", True),   # one below the end is inside
    (8,  "8-20", True),   # inclusive start: hour 8 IS inside
    (7,  "8-20", False),  # one below the start is outside
    (0,  "0-24", True),   # widest practical window: hour 0 inside
    (23, "0-24", True),   # hour 23 inside (under exclusive 24)
])
def test_inbound_window_boundary_table(hour, window, expected):
    """Spec'd boundary cases. Built end-to-end through build_offers using a
    minimal synthetic FlightResult so we exercise the _inbound_hour_within
    call site, not just the helper in isolation."""
    from datetime import datetime
    from fli.models import Airline, Airport, FlightLeg, FlightResult

    def _leg(dep_dt):
        return FlightLeg(
            airline=Airline.FI,
            flight_number="100",
            departure_airport=Airport.IAD,
            arrival_airport=Airport.HEL,
            departure_datetime=dep_dt,
            arrival_datetime=dep_dt.replace(year=dep_dt.year, day=dep_dt.day + 1),
            duration=600,
        )

    outbound = FlightResult(
        legs=[FlightLeg(
            airline=Airline.FI, flight_number="200",
            departure_airport=Airport.HEL, arrival_airport=Airport.IAD,
            departure_datetime=datetime(2026, 5, 18, 12, 0),
            arrival_datetime=datetime(2026, 5, 18, 22, 0),
            duration=600,
        )],
        price=500.0, currency="EUR", duration=600, stops=0,
    )
    inbound = FlightResult(
        legs=[_leg(datetime(2026, 5, 29, hour, 0))],
        price=500.0, currency="EUR", duration=600, stops=0,
    )
    offers = build_offers(
        [(outbound, inbound)],
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url="x",
        departure_date="2026-05-18", return_date="2026-05-29",
        limit=10,
        inbound_window=window,
    )
    assert (len(offers) == 1) is expected


# ----- offer_id collision (Phase 2.5 regression test) ------------------------


def test_offer_id_distinguishes_same_flights_different_layover_timing():
    """End-to-end regression: two FlightResults with identical airlines,
    identical flight numbers, identical departure_date and return_date — but
    different connection timing (same-day vs overnight layover at KEF). The
    pre-Phase-2.5 hash collided on these. The fix is that segment timing
    enters the hash via (flight_number, departure_time_local) tuples.

    Exercises the wiring inside _to_offer (building the tuple list in
    outbound-then-inbound order, ISO 8601 formatting of departure_time_local),
    not just _compute_offer_id in isolation.
    """
    from datetime import datetime
    from fli.models import Airline, Airport, FlightLeg, FlightResult

    def _leg(flight_number, dep_airport, arr_airport, dep_dt, arr_dt, duration):
        return FlightLeg(
            airline=Airline.FI,
            flight_number=flight_number,
            departure_airport=Airport[dep_airport],
            arrival_airport=Airport[arr_airport],
            departure_datetime=dep_dt,
            arrival_datetime=arr_dt,
            duration=duration,
        )

    # Same-day connection: HEL 15:00 → KEF 15:40, KEF 16:50 → IAD 19:20
    same_day = FlightResult(
        legs=[
            _leg("343", "HEL", "KEF", datetime(2026, 5, 18, 15, 0), datetime(2026, 5, 18, 15, 40), 220),
            _leg("645", "KEF", "IAD", datetime(2026, 5, 18, 16, 50), datetime(2026, 5, 18, 19, 20), 450),
        ],
        price=666.0, currency="EUR", duration=680, stops=1,
    )

    # Overnight at KEF: identical first leg, FI645 the FOLLOWING day at the same clock time.
    overnight = FlightResult(
        legs=[
            _leg("343", "HEL", "KEF", datetime(2026, 5, 18, 15, 0), datetime(2026, 5, 18, 15, 40), 220),
            _leg("645", "KEF", "IAD", datetime(2026, 5, 19, 16, 50), datetime(2026, 5, 19, 19, 20), 450),
        ],
        price=750.0, currency="EUR", duration=2120, stops=1,
    )

    offers = build_offers(
        [same_day, overnight],
        cabin=CabinClass.ECONOMY, adults=1,
        booking_url="x",
        departure_date="2026-05-18", return_date=None,
        limit=10,
    )
    assert len(offers) == 2
    # Same airlines, same flight numbers, same dates — but the (flight_number,
    # departure_time_local) tuple for FI645 differs between the two offers,
    # so the hashes diverge.
    assert offers[0].offer_id != offers[1].offer_id
    assert set(offers[0].airlines) == set(offers[1].airlines) == {"FI"}


# ----- build_date_offers (Phase 2) ------------------------------------------


def test_build_date_offers_from_fixture(fli_dates_flex):
    """Fixture has 5 round-trip entries, unsorted by price."""
    offers = build_date_offers(fli_dates_flex)
    assert len(offers) == 5
    # All round-trip → return_date populated.
    for o in offers:
        assert o.return_date is not None
        assert o.currency == "EUR"
    # Values should preserve fixture order, not sort.
    departures = [o.departure_date for o in offers]
    assert departures == ["2026-05-20", "2026-05-21", "2026-05-24", "2026-05-15", "2026-05-18"]


def test_build_date_offers_one_way_has_null_return():
    """One-way DatePrice entries have a 1-tuple date; return_date should be null."""
    from datetime import datetime
    from fli.search import DatePrice
    entries = [
        DatePrice(date=(datetime(2026, 5, 18),), price=300.0, currency="EUR"),
    ]
    offers = build_date_offers(entries)
    assert len(offers) == 1
    assert offers[0].departure_date == "2026-05-18"
    assert offers[0].return_date is None


def test_build_date_offers_currency_fallback():
    """Some entries may have currency=None; fallback should kick in."""
    from datetime import datetime
    from fli.search import DatePrice
    entries = [
        DatePrice(date=(datetime(2026, 5, 18), datetime(2026, 5, 29)), price=540.0, currency=None),
    ]
    offers = build_date_offers(entries, currency_fallback="USD")
    assert offers[0].currency == "USD"
