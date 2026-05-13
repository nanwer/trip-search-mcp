"""Pydantic models for parsing SerpAPI's google_hotels response.

Captures only the fields we read in `normalize.py`. SerpAPI returns many more
(`brands`, `ads`, `nearby_places`, `reviews_breakdown`, `serpapi_*_link`
follow-up URLs, etc.) but `extra="ignore"` lets future SerpAPI additions
slip through silently rather than break parsing.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _SerpModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SerpHotelImage(_SerpModel):
    thumbnail: str | None = None
    original_image: str | None = None


class SerpHotelGPS(_SerpModel):
    latitude: float | None = None
    longitude: float | None = None


class SerpHotelRate(_SerpModel):
    """SerpAPI returns both a formatted string ('€111') and a numeric form
    (`extracted_lowest: 111`). We read the numeric form so we don't need to
    parse currency symbols. `before_taxes_fees` exists in parallel; we use
    `extracted_lowest` (the with-taxes/fees total) as the user-facing price.
    """
    lowest: str | None = None
    extracted_lowest: float | None = None
    before_taxes_fees: str | None = None
    extracted_before_taxes_fees: float | None = None


class SerpHotelPrice(_SerpModel):
    """One booking-partner entry inside a property's `prices` array.

    Captured for the search_stays merge work: vacation rentals always
    surface this (typically with OTAs like Booking.com, Hotels.com,
    Bluepillow.com — NOT Airbnb / VRBO directly per Phase 0 fixtures).
    Hotel properties may surface this too, though the Phase 0 hotel
    fixture didn't carry it for the captured Tampere query.
    """
    source: str | None = None             # OTA name, e.g. "Booking.com"
    logo: str | None = None
    num_guests: int | None = None
    rate_per_night: SerpHotelRate | None = None


class SerpHotelProperty(_SerpModel):
    name: str
    property_token: str | None = None
    description: str | None = None
    type: str | None = None              # "hotel", "vacation rental", etc.
    hotel_class: str | None = None       # "4-star hotel" formatted form
    extracted_hotel_class: int | None = None  # int form, our star_rating
    overall_rating: float | None = None  # 0–5 scale (Google's native)
    reviews: int | None = None
    location_rating: float | None = None
    amenities: list[str] = Field(default_factory=list)
    gps_coordinates: SerpHotelGPS | None = None
    images: list[SerpHotelImage] = Field(default_factory=list)
    rate_per_night: SerpHotelRate | None = None
    total_rate: SerpHotelRate | None = None
    # Vacation-rental-only structured facts list. Phase 0 fixture shape:
    # ["Entire apartment", "Sleeps 8", "2 bedrooms", "2 bathrooms",
    #  "5 beds", "786 sq ft"]. We parse this in the normalize layer
    # into structured bedrooms/bathrooms/sleeps fields.
    essential_info: list[str] = Field(default_factory=list)
    # OTA price comparison. Empty list on hotels in the Phase 0 fixture,
    # populated on every rental there. Surfaces in the normalize layer
    # as the StayOffer.sources field.
    prices: list[SerpHotelPrice] = Field(default_factory=list)


class SerpHotelsResponse(_SerpModel):
    properties: list[SerpHotelProperty] = Field(default_factory=list)
