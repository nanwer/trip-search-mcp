"""Tests for the city-code expansion map and the fan-out integration
inside the flights tools.

Pure-function tests for cities.py are fast; the fanout tests substitute
a stub FliClient that records calls and returns deterministic offers.
"""
from __future__ import annotations

import pytest

from trip_search_mcp.cities import (
    CITY_TO_AIRPORTS,
    MAX_AIRPORTS_PER_SIDE,
    expand_to_airports,
    is_known_city,
)


# ----- pure expansion -------------------------------------------------------


def test_known_city_expands_to_list():
    assert expand_to_airports("WAS") == ["IAD", "DCA", "BWI"]


def test_unknown_code_passes_through_as_single_element_list():
    """JFK is an airport (not a city) — should pass through unchanged."""
    assert expand_to_airports("JFK") == ["JFK"]


def test_truncates_to_max_airports_per_side():
    """LON has 4 airports (LHR, LGW, STN, LCY) — should truncate to top 3."""
    out = expand_to_airports("LON")
    assert len(out) == MAX_AIRPORTS_PER_SIDE
    assert out == ["LHR", "LGW", "STN"]


def test_is_known_city():
    assert is_known_city("WAS") is True
    assert is_known_city("JFK") is False


def test_every_city_in_map_has_at_least_one_airport():
    """Sanity: no empty lists in the map (would cause silent zero-pair fanouts)."""
    for city, airports in CITY_TO_AIRPORTS.items():
        assert airports, f"city {city} has no airports"


# ----- integration: fanout inside search_flights ----------------------------


def _normalize_one_way(fli_one_way, *, origin="HEL", destination="IAD"):
    """Helper: turn the raw fli FlightResult fixture into normalized
    FlightOffer instances the way FliClient.search() would.

    Pair-specific origin/destination is threaded by feeding distinct
    booking_urls so the offer_ids encode the pair (drives meaningful
    dedup behaviour across pairs)."""
    from trip_search_mcp.fli_backend.normalize import booking_url_for, build_offers
    from trip_search_mcp.models import CabinClass
    booking = booking_url_for(origin, destination, "2026-05-18", None)
    return build_offers(
        fli_one_way,
        cabin=CabinClass.ECONOMY,
        adults=1,
        booking_url=booking,
        departure_date="2026-05-18",
        return_date=None,
        limit=10,
    )


async def test_city_origin_fans_out_to_three_calls(fli_one_way):
    """`origin='WAS'` triggers expansion to [IAD, DCA, BWI]. With a
    single-airport destination, we expect exactly 3 fli.search() calls."""
    from datetime import datetime, timedelta, timezone

    from trip_search_mcp.cache import TTLCache
    from trip_search_mcp.tools.search_flights import search_flights

    call_pairs: list[tuple[str, str]] = []

    class StubClient:
        async def search(self, params):
            call_pairs.append((params.origin, params.destination))
            return _normalize_one_way(
                fli_one_way,
                origin=params.origin, destination=params.destination,
            )

    tomorrow = (datetime.now(tz=timezone.utc).date() + timedelta(days=1)).isoformat()
    result = await search_flights(
        client=StubClient(),
        cache=TTLCache(ttl_seconds=300),
        origin="WAS", destination="LHR",
        departure_date=tomorrow,
    )

    origins_called = {o for o, d in call_pairs}
    assert origins_called == {"IAD", "DCA", "BWI"}
    assert {d for o, d in call_pairs} == {"LHR"}
    assert "results" in result


async def test_airport_pair_makes_single_call(fli_one_way):
    """Hot path: both sides are airport codes → exactly 1 fli.search() call."""
    from datetime import datetime, timedelta, timezone

    from trip_search_mcp.cache import TTLCache
    from trip_search_mcp.tools.search_flights import search_flights

    call_count = {"n": 0}

    class StubClient:
        async def search(self, params):
            call_count["n"] += 1
            return _normalize_one_way(
                fli_one_way,
                origin=params.origin, destination=params.destination,
            )

    tomorrow = (datetime.now(tz=timezone.utc).date() + timedelta(days=1)).isoformat()
    await search_flights(
        client=StubClient(),
        cache=TTLCache(ttl_seconds=300),
        origin="HEL", destination="JFK",
        departure_date=tomorrow,
    )
    assert call_count["n"] == 1


async def test_city_pair_merge_keeps_cheaper_variant(fli_one_way):
    """Two pairs returning overlapping offer_ids → dedup keeps cheaper."""
    import copy
    from datetime import datetime, timedelta, timezone

    from trip_search_mcp.cache import TTLCache
    from trip_search_mcp.tools.search_flights import search_flights

    # Per-pair price multiplier. We force ALL pairs to produce identical
    # offer_ids by normalizing with the SAME (origin, destination)
    # regardless of `params` — that way dedup-by-offer_id has something
    # to dedup. The price differs per pair so cheapest wins.
    multipliers = {"IAD": 1.0, "DCA": 0.5, "BWI": 2.0}

    class StubClient:
        async def search(self, params):
            offers = _normalize_one_way(
                fli_one_way,
                origin="WAS", destination=params.destination,
            )
            mult = multipliers.get(params.origin, 1.0)
            for o in offers:
                o.total_price = o.total_price * mult
            return offers

    tomorrow = (datetime.now(tz=timezone.utc).date() + timedelta(days=1)).isoformat()
    result = await search_flights(
        client=StubClient(),
        cache=TTLCache(ttl_seconds=300),
        origin="WAS", destination="LHR",
        departure_date=tomorrow,
    )
    assert "results" in result
    base = _normalize_one_way(fli_one_way, origin="WAS", destination="LHR")
    base_min = min(o.total_price for o in base)
    # Halved variants (0.5x) should win the dedup vs 1.0x and 2.0x.
    for offer in result["results"]:
        assert offer["total_price"] <= base_min