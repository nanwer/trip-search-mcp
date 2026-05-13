"""`watch_flight_price` — register a persistent price watch on a route.

The MCP server is stdio-only, so "watches" don't have an always-on
daemon. Instead, they're recorded in SQLite and refreshed on demand
when the user (or Claude) calls `list_active_watches`.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from trip_search_mcp.errors import ErrorCode, error_response
from trip_search_mcp.logging_config import log_event
from trip_search_mcp.models import SearchFlightsInput
from trip_search_mcp.monitoring import db

TOOL_NAME = "watch_flight_price"

TOOL_DESCRIPTION = """\
Register a persistent watch on a specific flight route, departure date, and price threshold. When the user later asks "any deals?", `list_active_watches` re-runs the search and reports whether the latest price has dropped to or below the threshold.

USE THIS TOOL WHEN: the user says something like "watch this route", "tell me if the price drops below X", "alert me if Y becomes cheaper", "monitor flights from A to B around date Z".

DO NOT USE THIS TOOL FOR ONE-OFF SEARCHES — use `search_flights` for those.

Inputs are the same shape as `search_flights` (origin, destination, departure_date, optional return_date, etc.) plus:
- `threshold_price`: numeric ceiling in the currency you also pass. The watch "fires" (status='alerted') when a refresh observes price ≤ threshold.
- `currency`: ISO 4217 currency code (e.g. "EUR", "USD"). Must match the units of `threshold_price`.
- `note`: optional free-text reminder ("for parents' anniversary", "cap to budget for Q3").

Returns the new watch's `watch_id` (a 12-character hex string). Hand it back to the user so they can cancel later with `cancel_watch(watch_id)`.

The watch persists across restarts (it's in SQLite under `~/.trip-search-mcp/watches.db`). Closing Claude Desktop doesn't lose your watches.

PRE-CALL ELICITATION:
- Confirm the route and dates the user wants to watch.
- **Confirm the threshold price AND its currency** explicitly — mixing currencies silently breaks the alert logic. Example: "I want to fly to Tokyo if it drops below 800 EUR" → `threshold_price=800, currency="EUR"`.
- If the user said "any time" or "flexible dates", offer to use `search_cheapest_dates` first to pick a candidate date, then watch THAT specific date.

The watch makes ONE fli call when refreshed (per active watch). Refresh frequency is controlled by `list_active_watches.refresh_after_hours` (default 6h)."""

_logger = logging.getLogger("trip_search_mcp")


async def watch_flight_price(
    *,
    origin: str,
    destination: str,
    departure_date: str,
    threshold_price: float,
    currency: str = "EUR",
    return_date: str | None = None,
    adults: int = 1,
    cabin_class: str = "ECONOMY",
    max_stops: str = "ANY",
    note: str | None = None,
) -> dict[str, Any]:
    # Reuse SearchFlightsInput validators (date sanity, IATA, etc.)
    raw_input = dict(
        origin=origin, destination=destination, departure_date=departure_date,
        return_date=return_date, adults=adults, cabin_class=cabin_class,
        max_stops=max_stops,
    )
    try:
        params = SearchFlightsInput.model_validate(raw_input)
    except ValidationError as e:
        first = e.errors()[0]
        msg = f"Invalid input on '{'.'.join(str(p) for p in first.get('loc', []))}': {first.get('msg')}"
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    if threshold_price <= 0:
        return error_response(
            ErrorCode.INVALID_INPUT,
            "threshold_price must be > 0.", retryable=False,
        )
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper():
        return error_response(
            ErrorCode.INVALID_INPUT,
            "currency must be a 3-letter uppercase ISO 4217 code (e.g. 'EUR', 'USD').",
            retryable=False,
        )

    # Ensure schema exists before insert.
    db.init_db()
    watch_id = db.create_watch(
        origin=params.origin,
        destination=params.destination,
        departure_date=params.departure_date,
        return_date=params.return_date,
        threshold_price=float(threshold_price),
        currency=currency,
        adults=params.adults,
        cabin_class=params.cabin_class.value,
        max_stops=params.max_stops.value,
        note=note,
    )
    log_event(_logger, "tool.success", tool=TOOL_NAME,
              watch_id=watch_id,
              route=f"{params.origin}->{params.destination}",
              threshold=threshold_price, currency=currency)
    return {
        "watch_id": watch_id,
        "status": "active",
        "route": f"{params.origin} → {params.destination}",
        "departure_date": params.departure_date,
        "return_date": params.return_date,
        "threshold_price": float(threshold_price),
        "currency": currency,
        "note": note,
        "message": (
            f"Watching {params.origin}→{params.destination} on "
            f"{params.departure_date}. Will alert when price ≤ "
            f"{threshold_price:g} {currency}. Cancel with "
            f"cancel_watch(watch_id=\"{watch_id}\")."
        ),
    }
