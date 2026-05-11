"""Pydantic models for tool I/O.

Input validation enforces IATA format, date sanity, passenger constraints, and
enum membership at the boundary — Claude's malformed input never reaches the
provider client. Output models are provider-neutral; the SerpAPI parsing types
live in `flights_mcp.serpapi.raw`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


class CabinClass(str, Enum):
    ECONOMY = "ECONOMY"
    PREMIUM_ECONOMY = "PREMIUM_ECONOMY"
    BUSINESS = "BUSINESS"
    FIRST = "FIRST"


IataCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$", strip_whitespace=False)]
IataAirlineCode = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{2,3}$", strip_whitespace=False)]
IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
IsoCurrency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]

# Provider-imposed caps. SerpAPI's Google Flights endpoint returns ~9 outbound
# options per call; round-trips need a follow-up call per outbound to fetch the
# matching return leg, so we cap round-trip max_results to keep the upstream
# quota math predictable. One-way uses a single call and inherits the looser cap.
ROUND_TRIP_MAX_RESULTS = 5


class SearchFlightsInput(BaseModel):
    origin: IataCode
    destination: IataCode
    departure_date: IsoDate
    return_date: IsoDate | None = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    infants: int = Field(default=0, ge=0, le=9)
    cabin_class: CabinClass = CabinClass.ECONOMY
    currency: IsoCurrency = "USD"
    non_stop_only: bool = False
    max_results: int = Field(default=20, ge=1, le=50)

    @field_validator("departure_date")
    @classmethod
    def _departure_not_in_past(cls, v: str) -> str:
        d = date.fromisoformat(v)
        today_utc = datetime.now(tz=timezone.utc).date()
        if d < today_utc:
            raise ValueError(f"departure_date {v} is before today (UTC) {today_utc.isoformat()}")
        return v

    @model_validator(mode="after")
    def _return_after_departure(self) -> "SearchFlightsInput":
        if self.return_date is None:
            return self
        dep = date.fromisoformat(self.departure_date)
        ret = date.fromisoformat(self.return_date)
        if ret < dep:
            raise ValueError(f"return_date {self.return_date} is before departure_date {self.departure_date}")
        return self

    @model_validator(mode="after")
    def _infants_le_adults(self) -> "SearchFlightsInput":
        if self.infants > self.adults:
            raise ValueError(
                f"infants ({self.infants}) must be <= adults ({self.adults}) — lap-infant rule"
            )
        return self

    @model_validator(mode="after")
    def _total_travelers_within_provider_limit(self) -> "SearchFlightsInput":
        # Google Flights (via SerpAPI) accepts up to 9 travelers per search;
        # the same limit holds across most GDS feeds.
        total = self.adults + self.children + self.infants
        if total > 9:
            raise ValueError(
                f"total travelers ({total}) exceeds the per-search limit of 9"
            )
        return self

    @model_validator(mode="after")
    def _round_trip_max_results_cap(self) -> "SearchFlightsInput":
        # Round-trip needs 1 + N upstream calls (one outbound, N return-leg
        # follow-ups). Cap N at 5 so a single search never burns more than ~6
        # SerpAPI calls. One-way is single-call and stays at the looser 50 cap.
        if self.return_date is not None and self.max_results > ROUND_TRIP_MAX_RESULTS:
            raise ValueError(
                f"max_results {self.max_results} exceeds the round-trip cap of "
                f"{ROUND_TRIP_MAX_RESULTS} (set max_results <= {ROUND_TRIP_MAX_RESULTS} "
                "for round-trip searches, or omit return_date for a one-way search)"
            )
        return self


class Segment(BaseModel):
    airline: IataAirlineCode
    flight_number: str
    departure_airport: IataCode
    departure_time_local: str  # ISO 8601 datetime, no offset, local to departure_airport
    arrival_airport: IataCode
    arrival_time_local: str
    cabin: CabinClass
    booking_class: str


class Itinerary(BaseModel):
    duration: str  # ISO 8601 duration
    stops: int = Field(ge=0)
    segments: list[Segment] = Field(min_length=1)


class FlightOffer(BaseModel):
    offer_id: str
    total_price: float
    currency: IsoCurrency
    price_per_adult: float
    airlines: list[IataAirlineCode]
    validating_airline: IataAirlineCode
    outbound: Itinerary
    inbound: Itinerary | None
    seats_available: int | None
    last_ticketing_date: str | None
    fare_basis: str
    baggage_allowance: str | None


class SearchFlightsResult(BaseModel):
    results: list[FlightOffer]
