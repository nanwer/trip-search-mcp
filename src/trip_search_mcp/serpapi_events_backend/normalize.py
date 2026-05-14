"""Translate SerpAPI google_events response into our EventOffer shape.

Pure functions, no I/O. Called by `client.py` after each upstream HTTP
call. Defensive against missing fields (some events lack a venue, an
image, etc.).
"""
from __future__ import annotations

import hashlib

from trip_search_mcp.models import EventOffer, TicketSource
from trip_search_mcp.serpapi_events_backend.raw import SerpEvent, SerpEventsResponse


def _compute_offer_id(*, title: str, start_date: str | None, venue_name: str | None) -> str:
    """Stable hash from (title, start_date, venue_name). Same event
    re-ranked across queries gets the same id."""
    payload = "|".join([
        (title or "").casefold().strip(),
        (start_date or "").strip(),
        (venue_name or "").casefold().strip(),
    ])
    return "ev:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _flatten_address(parts: list[str] | None) -> str | None:
    """SerpAPI returns address as a 2-3 element list of strings. Join with
    ', ' for display. Strip + dedupe empty entries."""
    if not parts:
        return None
    clean = [p.strip() for p in parts if p and p.strip()]
    if not clean:
        return None
    return ", ".join(clean)


def _to_offer(raw: SerpEvent) -> EventOffer | None:
    if not raw.title:
        return None
    if not raw.link:
        # An event without a ticket URL isn't actionable. Skip.
        return None

    venue = raw.venue
    date = raw.date
    venue_name = venue.name if venue else None
    start_date = date.start_date if date else None

    ticket_sources: list[TicketSource] = []
    for entry in raw.ticket_info:
        if not entry.source or not entry.link:
            continue
        ticket_sources.append(TicketSource(
            source=entry.source,
            link=entry.link,
            link_type=entry.link_type,
        ))

    return EventOffer(
        offer_id=_compute_offer_id(
            title=raw.title, start_date=start_date, venue_name=venue_name,
        ),
        title=raw.title,
        start_date_raw=start_date,
        when_text=date.when if date else None,
        venue_name=venue_name,
        venue_rating=venue.rating if venue else None,
        venue_review_count=venue.reviews if venue else None,
        address=_flatten_address(raw.address),
        description=raw.description,
        thumbnail=raw.thumbnail,
        image=raw.image,
        ticket_url=raw.link,
        ticket_sources=ticket_sources,
    )


def build_offers(response: SerpEventsResponse, *, limit: int) -> list[EventOffer]:
    """Normalize + cap at limit. Dedupe by offer_id while preserving
    SerpAPI's returned order (the first occurrence of a duplicate wins).

    SerpAPI rarely returns dupes in a single call, but if a future query
    style triggers it we don't want both rows surfacing."""
    seen: set[str] = set()
    offers: list[EventOffer] = []
    for raw in response.events_results:
        offer = _to_offer(raw)
        if offer is None:
            continue
        if offer.offer_id in seen:
            continue
        seen.add(offer.offer_id)
        offers.append(offer)
        if len(offers) >= limit:
            break
    return offers
