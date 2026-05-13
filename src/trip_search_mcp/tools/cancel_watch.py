"""`cancel_watch` — stop monitoring a specific watch."""
from __future__ import annotations

import logging
from typing import Any

from trip_search_mcp.errors import ErrorCode, error_response
from trip_search_mcp.logging_config import log_event
from trip_search_mcp.monitoring import db

TOOL_NAME = "cancel_watch"

TOOL_DESCRIPTION = """\
Cancel a previously created flight price watch by its `watch_id`. Use when the user says "stop watching that route", "cancel the Lisbon watch", "I already booked, take it off the list".

The watch is marked cancelled (not deleted), so it can still appear in `list_active_watches(include_cancelled=true)` if the user asks "what did I cancel?".

Returns `{"watch_id": ..., "status": "cancelled"}` on success.

If the user knows the route but not the watch_id, call `list_active_watches` first to find it, then pass the matching `watch_id` here."""

_logger = logging.getLogger("trip_search_mcp")


async def cancel_watch(*, watch_id: str) -> dict[str, Any]:
    db.init_db()
    if not isinstance(watch_id, str) or not watch_id.strip():
        return error_response(
            ErrorCode.INVALID_INPUT, "watch_id is required.", retryable=False,
        )
    existing = db.get_watch(watch_id)
    if existing is None:
        return error_response(
            ErrorCode.NO_RESULTS,
            f"No watch found with id {watch_id!r}. Call list_active_watches to see existing IDs.",
            retryable=False,
        )
    if existing["status"] == "cancelled":
        return {"watch_id": watch_id, "status": "already_cancelled"}
    updated = db.cancel_watch(watch_id)
    if not updated:
        # Race condition with a parallel cancel; treat as already-cancelled.
        return {"watch_id": watch_id, "status": "already_cancelled"}
    log_event(_logger, "tool.success", tool=TOOL_NAME, watch_id=watch_id)
    return {"watch_id": watch_id, "status": "cancelled"}
