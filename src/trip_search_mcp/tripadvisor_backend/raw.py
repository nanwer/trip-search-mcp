"""Pydantic models for parsing SerpAPI's Tripadvisor engine response.

Per Phase 0 (commit `4982152`+, fixtures `serpapi_tripadvisor_*.json`),
the response's results live under `places[]`. Every entry has
identical shape:

  {position, title, place_type, place_id, link, serpapi_link,
   description?, rating, reviews, location, thumbnail,
   highlighted_review{text, highlighted_texts, mention_count}}

`extra="ignore"` so SerpAPI additions don't break parsing.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _SerpModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SerpHighlightedReview(_SerpModel):
    text: str | None = None
    highlighted_texts: list[str] = Field(default_factory=list)
    mention_count: int | None = None


class SerpTripadvisorPlace(_SerpModel):
    position: int | None = None
    title: str | None = None
    place_type: str | None = None        # "ATTRACTION" or "ATTRACTION_PRODUCT"
    place_id: str | None = None
    link: str | None = None              # Tripadvisor listing URL
    serpapi_link: str | None = None      # SerpAPI follow-up endpoint
    description: str | None = None
    rating: float | None = None
    reviews: int | None = None
    location: str | None = None          # free-text "City, Country"
    thumbnail: str | None = None
    highlighted_review: SerpHighlightedReview | None = None


class SerpTripadvisorResponse(_SerpModel):
    places: list[SerpTripadvisorPlace] = Field(default_factory=list)
