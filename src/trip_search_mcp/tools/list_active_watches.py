"""`list_active_watches` — show the user's active watches and refresh stale ones.

This is where the "deal hunting" experience lives: the user (or Claude)
calls this tool, and any watch whose last check is older than the cutoff
gets re-run against fli. Watches whose new price ≤ threshold flip to
`alerted` and surface prominently in the response.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from trip_search_mcp.errors import ErrorCode, error_response
from trip_search_mcp.fli_backend.client import FliClient
from trip_search_mcp.logging_config import log_event
from trip_search_mcp.monitoring import db, refresh

TOOL_NAME = "list_active_watches"

TOOL_DESCRIPTION = """\
🎯 **RENDERING DIRECTIVE — READ FIRST.** When 2+ watches come back, render them as an **HTML/React artifact** — one card per watch with a clear "Cancel" **button** (callable via `cancel_watch(watch_id)`) and, for alerted watches, a "Book now" **button** to the flight booking URL. Alerted watches must be visually flagged (e.g. green badge or 🎯 callout). 1 watch may be prose.

List the user's active flight price watches. Re-runs any watch whose latest check is older than `refresh_after_hours` (default 6h) and flips status to "alerted" when the latest price is at or below the watch's threshold.

USE THIS TOOL WHEN: the user asks "any deals?", "what's the price of [route] looking like?", "show my watches", "anything trigger yet?", "did the Lisbon trip get cheaper?". Also use it proactively at the start of a session if you know the user has watches set up.

Returns a list of watch objects, each with:
- `watch_id`, `route` (formatted "ORIGIN → DESTINATION"), `departure_date`, `return_date`
- `threshold_price`, `currency`
- `status`: `"active"` (no alert) or `"alerted"` (price hit threshold during refresh)
- `last_price` / `last_currency` / `last_offer_id`: the latest observed price
- `last_checked_at`: timestamp of the latest refresh
- `alerted_at`: when the alert fired (null if not alerted)
- `note`: the user's optional note from creation time
- `gap`: numeric `last_price - threshold_price` (negative = below threshold, positive = above). Use this to summarize "X EUR below your target" or "still Y EUR above target".

Each refresh costs ONE fli call. Refresh frequency is bounded by `refresh_after_hours` — repeated calls within the cutoff window are free (no fli traffic). If you want a forced refresh, pass `refresh_after_hours=0`.

RESULT PRESENTATION: If any watch is `alerted`, lead with it (a small "🎯 Deal!" callout works well). For non-alerted watches, show the current gap ("currently 53 EUR above target, last checked 2h ago"). For never-checked watches, say so.

If `include_cancelled=true`, also include watches the user cancelled — useful when they ask "show me everything" or "what did I cancel last week?"."""

_logger = logging.getLogger("trip_search_mcp")


def _format_watch(row: dict) -> dict[str, Any]:
    threshold = row.get("threshold_price")
    last = row.get("last_price")
    gap = None
    if threshold is not None and last is not None:
        gap = round(last - threshold, 2)
    route = f"{row['origin']} → {row['destination']}"
    return {
        "watch_id": row["watch_id"],
        "route": route,
        "departure_date": row["departure_date"],
        "return_date": row.get("return_date"),
        "threshold_price": threshold,
        "currency": row["currency"],
        "status": row["status"],
        "last_price": last,
        "last_currency": row.get("last_currency"),
        "last_offer_id": row.get("last_offer_id"),
        "last_checked_at": row.get("last_checked_at"),
        "alerted_at": row.get("alerted_at"),
        "note": row.get("note"),
        "gap": gap,
    }


async def list_active_watches(
    *,
    client: FliClient,
    refresh_after_hours: float = 6.0,
    include_cancelled: bool = False,
) -> dict[str, Any]:
    await asyncio.to_thread(db.init_db)
    # Refresh stale active watches first so the snapshot we return is fresh.
    try:
        refreshed = await refresh.maybe_refresh_all(
            client=client, refresh_after_hours=refresh_after_hours,
        )
    except Exception as e:
        log_event(_logger, "tool.upstream_error", tool=TOOL_NAME,
                  error=str(e), level=logging.WARNING)
        # Don't fail the listing if refresh blew up — fall through and
        # return whatever we have on disk.
        refreshed = 0

    if include_cancelled:
        rows = await asyncio.to_thread(db.list_watches, status=None)
    else:
        rows = await asyncio.to_thread(db.list_watches, status="active")
        # status='active' excludes 'alerted'; surface alerted watches too.
        alerted = await asyncio.to_thread(db.list_watches, status="alerted")
        rows = list(rows) + list(alerted)

    formatted = [_format_watch(r) for r in rows]
    log_event(_logger, "tool.success", tool=TOOL_NAME,
              count=len(formatted), refreshed=refreshed)
    return {
        "results": formatted,
        "refreshed_count": refreshed,
        "refresh_after_hours": refresh_after_hours,
    }
