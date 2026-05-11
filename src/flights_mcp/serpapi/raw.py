"""Pydantic models for parsing SerpAPI's Google Flights response.

Kept inside the provider package so the public output models stay free of
provider-specific shapes. Only `normalize.py` and `client.py` import from here.

`extra="ignore"` is set on the base so SerpAPI can add fields without breaking
us (carbon_emissions, airline_logo, legroom, airplane, extensions etc are
silently dropped — they're either cosmetic or not in our output contract).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _SerpModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SerpEndpoint(_SerpModel):
    id: str                # IATA code
    name: str | None = None
    time: str | None = None  # "YYYY-MM-DD HH:MM" local airport time, no offset


class SerpSegment(_SerpModel):
    departure_airport: SerpEndpoint
    arrival_airport: SerpEndpoint
    duration: int          # minutes
    airline: str | None = None
    flight_number: str | None = None
    travel_class: str | None = None


class SerpLayover(_SerpModel):
    duration: int          # minutes
    id: str | None = None  # IATA code of the layover airport
    name: str | None = None


class SerpFlightOption(_SerpModel):
    """One outbound option from the initial search, or one return-leg option
    from the follow-up call. Both shapes share these fields."""
    flights: list[SerpSegment]
    layovers: list[SerpLayover] = Field(default_factory=list)
    total_duration: int    # minutes — outbound only on the initial call,
                           # round-trip total on the follow-up call
    price: int             # USD (or whatever currency was requested)
    type: str | None = None  # "Round trip" / "One way"
    # Only present on the initial outbound response. Carries the state needed
    # to fetch the matching return leg in a follow-up call.
    departure_token: str | None = None
    # Only present on return-leg responses. The opaque round-trip identifier
    # we surface as offer_id.
    booking_token: str | None = None


class SerpGoogleFlightsResponse(_SerpModel):
    best_flights: list[SerpFlightOption] = Field(default_factory=list)
    other_flights: list[SerpFlightOption] = Field(default_factory=list)


class SerpError(_SerpModel):
    """SerpAPI surfaces errors as a top-level `error` string on 200-OK responses
    as well as on non-2xx bodies. Models the documented shape."""
    error: str
