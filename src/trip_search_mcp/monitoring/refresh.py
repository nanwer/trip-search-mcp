"""Refresh logic — re-run an active watch and update its last_* fields.

`maybe_refresh_all(refresh_after_hours)` is the entry point called by
`list_active_watches`. It iterates active watches, finds the ones whose
`last_checked_at` is older than the cutoff (or null), and re-runs each
via the FliClient. Updates the DB row with the latest cheapest offer's
price. If the new price is at or below the threshold, the row is also
flipped to `status='alerted'` and `alerted_at` is set.

We DO NOT use the search_flights tool function directly (it's an MCP
boundary; brings cache/log machinery in). We go straight to FliClient
and the normalize layer.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from trip_search_mcp.errors import ToolError
from trip_search_mcp.fli_backend.client import FliClient
from trip_search_mcp.models import SearchFlightsInput
from trip_search_mcp.monitoring import db

_logger = logging.getLogger("trip_search_mcp")


def _is_stale(last_checked_at: str | None, cutoff_hours: float) -> bool:
    if not last_checked_at:
        return True
    try:
        dt = datetime.fromisoformat(last_checked_at)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(tz=timezone.utc) - dt > timedelta(hours=cutoff_hours)


async def refresh_one(
    watch: dict, *, client: FliClient,
) -> tuple[float | None, str | None, str | None, bool]:
    """Re-run a single watch's search. Returns
    (price, currency, offer_id, hit_threshold). On upstream failure,
    returns (None, None, None, False) and logs."""
    try:
        params = SearchFlightsInput(
            origin=watch["origin"],
            destination=watch["destination"],
            departure_date=watch["departure_date"],
            return_date=watch.get("return_date"),
            adults=watch.get("adults") or 1,
            cabin_class=watch.get("cabin_class") or "ECONOMY",
            max_stops=watch.get("max_stops") or "ANY",
            max_results=10,
        )
    except Exception as e:
        _logger.warning(f"watch {watch['watch_id']}: invalid params {e}")
        return (None, None, None, False)

    try:
        offers = await client.search(params)
    except ToolError as e:
        _logger.warning(f"watch {watch['watch_id']}: refresh failed: {e.code.value} {e.message}")
        return (None, None, None, False)

    if not offers:
        return (None, None, None, False)

    # Cheapest first (offers are typically already sorted by fli's BEST
    # ranking, which approximates cheapest-of-good-quality, but we re-sort
    # to be safe).
    offers.sort(key=lambda o: o.total_price)
    cheapest = offers[0]
    hit = cheapest.total_price <= watch["threshold_price"]
    return (cheapest.total_price, cheapest.currency, cheapest.offer_id, hit)


async def maybe_refresh_all(
    *, client: FliClient, refresh_after_hours: float = 6.0,
) -> int:
    """Refresh every active watch whose last check is older than the
    cutoff (or never checked). Returns the count refreshed.

    Errors on individual watches are logged and skipped — one watch's
    bad route doesn't poison the rest."""
    watches = await asyncio.to_thread(db.list_watches, status="active")
    refreshed = 0
    for watch in watches:
        if not _is_stale(watch["last_checked_at"], refresh_after_hours):
            continue
        price, currency, offer_id, hit = await refresh_one(watch, client=client)
        if price is None:
            # Don't update last_checked_at on hard failure — we want to
            # retry next time. Soft-skip.
            continue
        await asyncio.to_thread(
            db.record_check,
            watch["watch_id"],
            price=price,
            currency=currency,
            offer_id=offer_id,
            mark_alerted=hit,
        )
        refreshed += 1
    return refreshed
