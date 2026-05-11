import time

import pytest

from flights_mcp.cache import TTLCache, canonical_key


def test_canonical_key_lowercases_iata_and_sorts():
    k1 = canonical_key({"origin": "HEL", "destination": "iad", "adults": 1})
    k2 = canonical_key({"adults": 1, "destination": "IAD", "origin": "hel"})
    assert k1 == k2


def test_canonical_key_distinguishes_inputs():
    k1 = canonical_key({"origin": "HEL", "destination": "IAD", "departure_date": "2026-05-18"})
    k2 = canonical_key({"origin": "HEL", "destination": "IAD", "departure_date": "2026-05-19"})
    assert k1 != k2


def test_cache_returns_stored_value_within_ttl():
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", {"v": 1})
    assert cache.get("k") == {"v": 1}


def test_cache_returns_none_after_ttl(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("flights_mcp.cache.time.monotonic", lambda: now[0])
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", {"v": 1})
    now[0] += 30
    assert cache.get("k") == {"v": 1}
    now[0] += 31
    assert cache.get("k") is None


def test_cache_returns_none_for_unknown_key():
    cache = TTLCache(ttl_seconds=60)
    assert cache.get("missing") is None


def test_canonical_key_collapses_cabin_class_case():
    k_upper = canonical_key({"origin": "HEL", "destination": "IAD", "cabin_class": "BUSINESS"})
    k_lower = canonical_key({"origin": "HEL", "destination": "IAD", "cabin_class": "business"})
    assert k_upper == k_lower


def test_cache_set_overwrites_previous_value():
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", {"v": 1})
    cache.set("k", {"v": 2})
    assert cache.get("k") == {"v": 2}
