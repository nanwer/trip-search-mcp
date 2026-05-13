"""Tests for the deal-hunting monitoring layer.

The DB path is redirected to a per-test temp file via the
TRIP_SEARCH_DB_PATH env var; the actual `~/.trip-search-mcp/watches.db`
is never touched.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from trip_search_mcp.errors import ToolError
from trip_search_mcp.monitoring import db, refresh
from trip_search_mcp.tools.cancel_watch import cancel_watch
from trip_search_mcp.tools.list_active_watches import list_active_watches
from trip_search_mcp.tools.watch_flight_price import watch_flight_price


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the DB at a fresh tmp file for each test."""
    path = tmp_path / "watches.db"
    monkeypatch.setenv("TRIP_SEARCH_DB_PATH", str(path))
    db.init_db()
    yield path


# ----- DB layer --------------------------------------------------------------


def test_create_and_get_watch(temp_db):
    wid = db.create_watch(
        origin="HEL", destination="IAD", departure_date="2026-09-15",
        threshold_price=600.0, currency="EUR",
    )
    assert len(wid) == 12
    row = db.get_watch(wid)
    assert row is not None
    assert row["status"] == "active"
    assert row["threshold_price"] == 600.0
    assert row["last_price"] is None


def test_list_watches_filters_by_status(temp_db):
    a = db.create_watch(
        origin="HEL", destination="IAD", departure_date="2026-09-15",
        threshold_price=600.0, currency="EUR",
    )
    db.create_watch(
        origin="HEL", destination="JFK", departure_date="2026-10-01",
        threshold_price=700.0, currency="EUR",
    )
    db.cancel_watch(a)
    active = db.list_watches(status="active")
    cancelled = db.list_watches(status="cancelled")
    everything = db.list_watches(status=None)
    assert len(active) == 1
    assert len(cancelled) == 1
    assert len(everything) == 2


def test_record_check_updates_last_fields(temp_db):
    wid = db.create_watch(
        origin="HEL", destination="IAD", departure_date="2026-09-15",
        threshold_price=600.0, currency="EUR",
    )
    db.record_check(
        wid, price=550.0, currency="EUR",
        offer_id="off-123", mark_alerted=True,
    )
    row = db.get_watch(wid)
    assert row["last_price"] == 550.0
    assert row["last_offer_id"] == "off-123"
    assert row["status"] == "alerted"
    assert row["alerted_at"] is not None


def test_cancel_watch_idempotent(temp_db):
    wid = db.create_watch(
        origin="HEL", destination="IAD", departure_date="2026-09-15",
        threshold_price=600.0, currency="EUR",
    )
    assert db.cancel_watch(wid) is True
    assert db.cancel_watch(wid) is False
    assert db.get_watch(wid)["status"] == "cancelled"


# ----- tool: watch_flight_price ---------------------------------------------


async def test_watch_flight_price_creates_watch(temp_db):
    from datetime import datetime, timedelta, timezone
    tomorrow = (datetime.now(tz=timezone.utc).date() + timedelta(days=1)).isoformat()
    result = await watch_flight_price(
        origin="HEL", destination="IAD",
        departure_date=tomorrow,
        threshold_price=600.0, currency="EUR",
        note="for parents' anniversary",
    )
    assert "watch_id" in result
    assert result["status"] == "active"
    row = db.get_watch(result["watch_id"])
    assert row["origin"] == "HEL"
    assert row["destination"] == "IAD"
    assert row["note"] == "for parents' anniversary"


async def test_watch_flight_price_rejects_negative_threshold(temp_db):
    from datetime import datetime, timedelta, timezone
    tomorrow = (datetime.now(tz=timezone.utc).date() + timedelta(days=1)).isoformat()
    result = await watch_flight_price(
        origin="HEL", destination="IAD",
        departure_date=tomorrow,
        threshold_price=-1.0, currency="EUR",
    )
    assert result["error"]["code"] == "invalid_input"


async def test_watch_flight_price_rejects_bad_currency(temp_db):
    from datetime import datetime, timedelta, timezone
    tomorrow = (datetime.now(tz=timezone.utc).date() + timedelta(days=1)).isoformat()
    result = await watch_flight_price(
        origin="HEL", destination="IAD",
        departure_date=tomorrow,
        threshold_price=600.0, currency="euros",
    )
    assert result["error"]["code"] == "invalid_input"


# ----- tool: cancel_watch ----------------------------------------------------


async def test_cancel_watch_tool(temp_db):
    wid = db.create_watch(
        origin="HEL", destination="IAD", departure_date="2026-09-15",
        threshold_price=600.0, currency="EUR",
    )
    result = await cancel_watch(watch_id=wid)
    assert result["status"] == "cancelled"
    second = await cancel_watch(watch_id=wid)
    assert second["status"] == "already_cancelled"


async def test_cancel_watch_unknown_id(temp_db):
    result = await cancel_watch(watch_id="not-a-real-id")
    assert result["error"]["code"] == "no_results"


# ----- refresh + list_active_watches ----------------------------------------


class _StubFliClient:
    """Returns predictable offers; tracks how many times search() is called."""
    def __init__(self, offers):
        self._offers = offers
        self.call_count = 0

    async def search(self, params):
        self.call_count += 1
        return list(self._offers)


def _make_offer(*, total_price: float, currency: str = "EUR",
                offer_id: str = "off-1"):
    """Minimal FlightOffer-shaped object — only the fields refresh.py reads."""
    class _Offer:
        pass
    o = _Offer()
    o.total_price = total_price
    o.currency = currency
    o.offer_id = offer_id
    return o


async def test_list_active_watches_refreshes_stale_watches(temp_db):
    db.create_watch(
        origin="HEL", destination="IAD", departure_date="2026-09-15",
        threshold_price=600.0, currency="EUR",
    )
    client = _StubFliClient([_make_offer(total_price=520.0)])
    result = await list_active_watches(client=client, refresh_after_hours=0)
    assert client.call_count == 1
    # The watch should now be alerted (520 ≤ 600).
    matching = [w for w in result["results"] if w["last_price"] == 520.0]
    assert len(matching) == 1
    assert matching[0]["status"] == "alerted"
    assert matching[0]["gap"] == -80.0  # 520 - 600


async def test_list_active_watches_does_not_alert_when_above_threshold(temp_db):
    db.create_watch(
        origin="HEL", destination="IAD", departure_date="2026-09-15",
        threshold_price=600.0, currency="EUR",
    )
    client = _StubFliClient([_make_offer(total_price=750.0)])
    result = await list_active_watches(client=client, refresh_after_hours=0)
    matching = result["results"][0]
    assert matching["status"] == "active"
    assert matching["last_price"] == 750.0
    assert matching["gap"] == 150.0


async def test_list_active_watches_skips_recently_checked(temp_db):
    """Watches checked within `refresh_after_hours` should NOT be re-run."""
    wid = db.create_watch(
        origin="HEL", destination="IAD", departure_date="2026-09-15",
        threshold_price=600.0, currency="EUR",
    )
    db.record_check(wid, price=700.0, currency="EUR", offer_id="x", mark_alerted=False)
    client = _StubFliClient([_make_offer(total_price=500.0)])
    # 6h cutoff; just-checked → no refresh.
    result = await list_active_watches(client=client, refresh_after_hours=6.0)
    assert client.call_count == 0
    assert result["results"][0]["last_price"] == 700.0


async def test_list_active_watches_empty(temp_db):
    client = _StubFliClient([])
    result = await list_active_watches(client=client)
    assert result["results"] == []
    assert client.call_count == 0


async def test_list_active_watches_include_cancelled(temp_db):
    a = db.create_watch(
        origin="HEL", destination="IAD", departure_date="2026-09-15",
        threshold_price=600.0, currency="EUR",
    )
    db.create_watch(
        origin="HEL", destination="JFK", departure_date="2026-10-01",
        threshold_price=700.0, currency="EUR",
    )
    db.cancel_watch(a)
    client = _StubFliClient([])
    result = await list_active_watches(
        client=client, refresh_after_hours=0, include_cancelled=True,
    )
    statuses = {w["status"] for w in result["results"]}
    assert "cancelled" in statuses
    assert "active" in statuses
