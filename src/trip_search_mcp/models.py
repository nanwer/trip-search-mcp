"""Pydantic models for tool I/O.

Input validation enforces IATA format, date sanity, passenger constraints, and
enum membership at the boundary — Claude's malformed input never reaches the
provider client. Output models are provider-neutral; the fli parsing types
live inside `trip_search_mcp.fli_backend`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


class CabinClass(str, Enum):
    ECONOMY = "ECONOMY"
    PREMIUM_ECONOMY = "PREMIUM_ECONOMY"
    BUSINESS = "BUSINESS"
    FIRST = "FIRST"


class MaxStops(str, Enum):
    # Names mirror fli.models.MaxStops exactly — "or fewer" semantics, not
    # "exactly N stops". Pass through to fli without translation.
    ANY = "ANY"
    NON_STOP = "NON_STOP"
    ONE_STOP_OR_FEWER = "ONE_STOP_OR_FEWER"
    TWO_OR_FEWER_STOPS = "TWO_OR_FEWER_STOPS"


IataCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$", strip_whitespace=False)]
IataAirlineCode = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{2,3}$", strip_whitespace=False)]
IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
IsoCurrency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
# "HH-HH" — two integer hours in 0-23, end strictly after start. Validation
# is structural here (regex catches typos); semantic checks live in the model.
DepartureWindow = Annotated[str, StringConstraints(pattern=r"^([01]?\d|2[0-3])-([01]?\d|2[0-3])$")]


class SearchFlightsInput(BaseModel):
    origin: IataCode
    destination: IataCode
    departure_date: IsoDate
    return_date: IsoDate | None = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    infants: int = Field(default=0, ge=0, le=9)
    cabin_class: CabinClass = CabinClass.ECONOMY
    # New filter knobs exposed by fli:
    max_stops: MaxStops = MaxStops.ANY
    departure_window: DepartureWindow | None = None
    # Inbound (return-leg) departure window. fli's filter only controls the
    # outbound leg, so this is applied as a post-filter in normalize.py over
    # the inbound's first segment. No effect on one-way searches.
    inbound_window: DepartureWindow | None = None
    airlines: list[IataAirlineCode] | None = None
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
        total = self.adults + self.children + self.infants
        if total > 9:
            raise ValueError(
                f"total travelers ({total}) exceeds the per-search limit of 9"
            )
        return self

    @model_validator(mode="after")
    def _departure_window_end_after_start(self) -> "SearchFlightsInput":
        if self.departure_window is None:
            return self
        start_str, end_str = self.departure_window.split("-")
        start, end = int(start_str), int(end_str)
        if end <= start:
            raise ValueError(
                f"departure_window end hour ({end}) must be after start hour ({start})"
            )
        return self

    @model_validator(mode="after")
    def _inbound_window_end_after_start(self) -> "SearchFlightsInput":
        if self.inbound_window is None:
            return self
        start_str, end_str = self.inbound_window.split("-")
        start, end = int(start_str), int(end_str)
        if end <= start:
            raise ValueError(
                f"inbound_window end hour ({end}) must be after start hour ({start})"
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
    booking_url: str  # Google Flights URL with the search pre-filled. Always populated.


class SearchFlightsResult(BaseModel):
    results: list[FlightOffer]


# ----- search_cheapest_dates: date-flex grid input/output --------------------


class SearchCheapestDatesInput(BaseModel):
    """Input contract for the date-flex tool.

    `start_date` and `end_date` bracket the range of acceptable DEPARTURE
    dates. For round-trip, `trip_duration` (days) determines each candidate
    return date — fli's SearchDates returns (departure, departure+duration)
    pairs across the window. For one-way, return_date in the output is null.
    """
    origin: IataCode
    destination: IataCode
    start_date: IsoDate
    end_date: IsoDate
    # 365-day cap is a typo guardrail (someone types 3000 instead of 30),
    # not a product opinion on how long a trip should be.
    trip_duration: int | None = Field(default=None, ge=1, le=365)
    is_round_trip: bool = False
    passengers: int = Field(default=1, ge=1, le=9)
    cabin_class: CabinClass = CabinClass.ECONOMY
    max_stops: MaxStops = MaxStops.ANY
    departure_window: DepartureWindow | None = None
    airlines: list[IataAirlineCode] | None = None

    @field_validator("start_date")
    @classmethod
    def _start_not_in_past(cls, v: str) -> str:
        d = date.fromisoformat(v)
        today_utc = datetime.now(tz=timezone.utc).date()
        if d < today_utc:
            raise ValueError(f"start_date {v} is before today (UTC) {today_utc.isoformat()}")
        return v

    @model_validator(mode="after")
    def _end_after_start(self) -> "SearchCheapestDatesInput":
        s = date.fromisoformat(self.start_date)
        e = date.fromisoformat(self.end_date)
        if e < s:
            raise ValueError(f"end_date {self.end_date} is before start_date {self.start_date}")
        return self

    @model_validator(mode="after")
    def _round_trip_requires_duration(self) -> "SearchCheapestDatesInput":
        if self.is_round_trip and self.trip_duration is None:
            raise ValueError(
                "trip_duration is required when is_round_trip is true "
                "(e.g. trip_duration=11 for a 1.5-week trip)"
            )
        return self

    @model_validator(mode="after")
    def _departure_window_end_after_start(self) -> "SearchCheapestDatesInput":
        if self.departure_window is None:
            return self
        start_str, end_str = self.departure_window.split("-")
        if int(end_str) <= int(start_str):
            raise ValueError(
                f"departure_window end hour must be after start hour, got {self.departure_window!r}"
            )
        return self


class DatePriceOffer(BaseModel):
    """One (departure_date, return_date_or_null, price) entry in the date grid."""
    departure_date: IsoDate
    return_date: IsoDate | None
    price: float
    currency: IsoCurrency


class SearchCheapestDatesResult(BaseModel):
    results: list[DatePriceOffer]


# ----- search_stays (Google Hotels + vacation rentals via SerpAPI) -----------


class StayCategory(str, Enum):
    """Which backend(s) to query.

    ALL fan-outs to SerpAPI hotels + SerpAPI rentals in parallel and
    merges (~3s wall-clock, 2x SerpAPI quota burn). HOTELS /
    VACATION_RENTALS are single-call paths against SerpAPI. AIRBNB
    bypasses SerpAPI entirely and hits Airbnb directly via pyairbnb —
    use this only when the user specifically asks for Airbnb. Per Phase
    0 verification, Google's SerpAPI aggregation does NOT include
    Airbnb listings; AIRBNB exists to fill that gap.
    """
    ALL = "all"
    HOTELS = "hotels"
    VACATION_RENTALS = "vacation_rentals"
    AIRBNB = "airbnb"


class HotelSortBy(str, Enum):
    BEST = "BEST"                  # preserve SerpAPI's returned order
    PRICE_LOW = "PRICE_LOW"        # price_total ascending
    PRICE_HIGH = "PRICE_HIGH"      # price_total descending
    RATING = "RATING"              # star_rating descending (then review_score)
    REVIEW_SCORE = "REVIEW_SCORE"  # review_score descending (then review_count)


class SearchStaysInput(BaseModel):
    location: str = Field(min_length=1)
    check_in_date: IsoDate
    check_out_date: IsoDate
    adults: int = Field(default=2, ge=1, le=10)
    children: int = Field(default=0, ge=0, le=10)
    rooms: int = Field(default=1, ge=1, le=10)
    # Category selector. Defaults to ALL — Phase 0 latency math shows
    # parallel fanout adds ~0.2s vs a single call, and the merged
    # result covers the dominant "find me a place to stay" use case.
    category: StayCategory = StayCategory.ALL
    # Hotel-only filter (rentals carry no hotel class). Routed to the
    # hotel call only when category=ALL.
    min_rating: int | None = Field(default=None, ge=1, le=5)
    # Vacation-rental-only filters. SerpAPI returns HTTP 400 if these
    # are sent with vacation_rentals=false, so the client routes them
    # to the rental call only when category=ALL.
    min_bedrooms: int | None = Field(default=None, ge=0, le=20)
    min_bathrooms: int | None = Field(default=None, ge=0, le=20)
    # Cross-category filters — apply to whichever calls run.
    min_review_score: float | None = Field(default=None, ge=0.0, le=5.0)
    max_price_per_night: float | None = Field(default=None, gt=0.0)
    required_amenities: list[str] | None = None
    sort_by: HotelSortBy = HotelSortBy.BEST
    max_results: int = Field(default=10, ge=1, le=25)
    # ISO 4217 three-letter code. Defaults to EUR (matches what fli returns
    # for European-IP users so flight+stay totals can be compared directly).
    # Override per call when the user works in a different currency
    # ("budget ¥30000/night" → currency="JPY"). Validated as 3 uppercase
    # letters; SerpAPI will surface unsupported codes via its error body.
    currency: IsoCurrency = "EUR"

    @field_validator("check_in_date")
    @classmethod
    def _check_in_not_in_past(cls, v: str) -> str:
        d = date.fromisoformat(v)
        today_utc = datetime.now(tz=timezone.utc).date()
        if d < today_utc:
            raise ValueError(f"check_in_date {v} is before today (UTC) {today_utc.isoformat()}")
        return v

    @model_validator(mode="after")
    def _check_out_after_check_in(self) -> "SearchStaysInput":
        ci = date.fromisoformat(self.check_in_date)
        co = date.fromisoformat(self.check_out_date)
        if co <= ci:
            raise ValueError(
                f"check_out_date {self.check_out_date} must be strictly after check_in_date {self.check_in_date}"
            )
        return self


class Source(BaseModel):
    """One booking-partner entry derived from SerpAPI's `prices` array.

    Per Phase 0 fixtures, vacation rentals surface OTAs (Booking.com,
    Hotels.com, Bluepillow.com) — NOT Airbnb / VRBO directly. Hotels in
    the fixture lacked the `prices` array entirely.
    """
    name: str                              # OTA name, canonicalized (e.g. "Booking.com")
    price_per_night: float | None = None   # in response currency
    before_taxes_fees: float | None = None # when SerpAPI exposes it


class StayOffer(BaseModel):
    offer_id: str
    name: str
    check_in_date: IsoDate
    check_out_date: IsoDate
    nights: int = Field(ge=1)
    price_total: float
    price_per_night: float
    currency: IsoCurrency
    # "hotel" or "vacation_rental". Mirrored from SerpAPI's `type` field,
    # mapped to one of these two canonical values. Drives card rendering
    # ("Hotel" / "Vacation rental" badge).
    category: Literal["hotel", "vacation_rental"]
    star_rating: int | None
    # Google's native 0–5 review scale (preserved as-is, NOT rescaled to 0–10).
    review_score: float | None
    review_count: int | None
    address: str | None
    latitude: float | None
    longitude: float | None
    amenities: list[str]
    images: list[str]              # capped at 5 in normalize
    description: str | None        # populated on hotels; null on rentals
    # Vacation-rental-only structured facts parsed from SerpAPI's
    # `essential_info` (e.g. "Sleeps 8", "2 bedrooms", "2 bathrooms").
    # Null when SerpAPI didn't surface the value or this is a hotel.
    bedrooms: int | None = None
    bathrooms: int | None = None
    sleeps: int | None = None
    hotel_type: str | None         # raw `type` value: "hotel", "vacation rental", etc.
    # OTA price comparison. Empty list when SerpAPI's `prices` was
    # missing (true for hotels in the Phase 0 fixture). Populated for
    # vacation rentals.
    sources: list[Source] = Field(default_factory=list)
    booking_url: str               # Google Hotels URL with the search pre-filled


class SearchStaysResult(BaseModel):
    results: list[StayOffer]
    # Populated only on the partial-failure path (when category=ALL and
    # one of the two SerpAPI calls errors but the other succeeds). The
    # tool description tells Claude to surface these verbatim above the
    # card grid.
    warnings: list[str] = Field(default_factory=list)


# ----- get_stay_details (Phase 6: SerpAPI property_details follow-up) -------


class BookingPartner(BaseModel):
    """One booking partner with a direct booking-flow link.

    Distinct from the search-time `Source` in that this one carries the
    per-partner `link` (via Google's `/travel/clk?` redirector) that
    lands the user on the actual booking page. Only the property_details
    endpoint populates the link; the list endpoint doesn't.
    """
    name: str                              # OTA name, canonicalized
    price_per_night: float | None = None
    total_price: float | None = None
    link: str | None = None                # google.com/travel/clk?... redirector
    official: bool | None = None           # property's own site vs an OTA
    free_cancellation: bool | None = None


class NearbyPlace(BaseModel):
    name: str
    category: str | None = None            # "airport", "train station", "restaurant", ...
    latitude: float | None = None
    longitude: float | None = None


class StayDetails(BaseModel):
    """Rich per-property detail returned by `get_stay_details`.

    Complements `StayOffer` (the search-time shape) — drills into a
    specific property to surface direct booking-partner links, long-form
    description, and a richer nearby_places list (~14 entries vs ~3 in
    search results).

    NOTE: `address` is NOT included. SerpAPI's property_details endpoint
    does not carry a postal address — only `latitude`/`longitude` and
    nearby landmarks. The previous backlog framing has been corrected.
    """
    property_token: str
    name: str
    category: Literal["hotel", "vacation_rental"]
    description: str | None
    hotel_type: str | None
    star_rating: int | None
    review_score: float | None
    review_count: int | None
    location_rating: float | None = None
    check_in_time: str | None
    check_out_time: str | None
    latitude: float | None
    longitude: float | None
    amenities: list[str]
    excluded_amenities: list[str]
    nearby_places: list[NearbyPlace]
    booking_partners: list[BookingPartner]
    currency: IsoCurrency


class GetStayDetailsInput(BaseModel):
    """Input to `get_stay_details`. Takes a property_token from a prior
    `search_stays` result and a check-in/check-out range (required by
    SerpAPI to price the property for those dates)."""
    property_token: str = Field(min_length=4)
    check_in_date: IsoDate
    check_out_date: IsoDate
    adults: int = Field(default=2, ge=1, le=10)
    currency: IsoCurrency = "EUR"

    @field_validator("check_in_date")
    @classmethod
    def _check_in_not_in_past(cls, v: str) -> str:
        d = date.fromisoformat(v)
        today_utc = datetime.now(tz=timezone.utc).date()
        if d < today_utc:
            raise ValueError(f"check_in_date {v} is before today (UTC) {today_utc.isoformat()}")
        return v

    @model_validator(mode="after")
    def _check_out_after_check_in(self) -> "GetStayDetailsInput":
        ci = date.fromisoformat(self.check_in_date)
        co = date.fromisoformat(self.check_out_date)
        if co <= ci:
            raise ValueError(
                f"check_out_date {self.check_out_date} must be strictly after check_in_date {self.check_in_date}"
            )
        return self
