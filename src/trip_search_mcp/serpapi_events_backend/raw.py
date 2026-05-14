"""Pydantic models for parsing SerpAPI's google_events response.

Per Phase 0 (commit `5717f7f`+, fixture `serpapi_events_*.json`), every
event in the response shares the same shape:

  {title, date{start_date, when}, address[...], link, description,
   thumbnail, image, ticket_info[...], venue{name, rating, reviews, link}}

`extra="ignore"` so SerpAPI additions don't break parsing.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _SerpModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SerpEventDate(_SerpModel):
    start_date: str | None = None        # "May 15", "Jun 21" — month + day, no year
    when: str | None = None              # full display string with year + GMT offset


class SerpEventVenue(_SerpModel):
    name: str | None = None
    rating: float | None = None
    reviews: int | None = None
    link: str | None = None              # Google search URL for the venue


class SerpTicketInfo(_SerpModel):
    source: str | None = None            # "Viagogo", "StubHub", "Eventbrite", ...
    link: str | None = None
    link_type: str | None = None         # "tickets" / "more info" / "info"


class SerpEvent(_SerpModel):
    title: str | None = None
    date: SerpEventDate | None = None
    address: list[str] = Field(default_factory=list)
    link: str | None = None              # primary ticket URL (vendor varies)
    description: str | None = None
    thumbnail: str | None = None
    image: str | None = None
    ticket_info: list[SerpTicketInfo] = Field(default_factory=list)
    venue: SerpEventVenue | None = None


class SerpEventsResponse(_SerpModel):
    events_results: list[SerpEvent] = Field(default_factory=list)
