"""Pydantic models for tool I/O and internal Amadeus parsing.

Input validation enforces IATA format, date sanity, passenger constraints, and
enum membership at the boundary — Claude's malformed input never reaches the
Amadeus client.
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
    def _total_travelers_within_amadeus_limit(self) -> "SearchFlightsInput":
        total = self.adults + self.children + self.infants
        if total > 9:
            raise ValueError(
                f"total travelers ({total}) exceeds the Amadeus limit of 9 per search"
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
    # TODO: switch monetary fields to Decimal once Task 8's normalizer is in place.
    # Amadeus returns prices as strings ("742.18"); float is fine for pass-through
    # but loses precision under arithmetic. Decimal(str(raw)) is the correct path.
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
