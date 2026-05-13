"""fli (PyPI: `flights`) Google Flights client.

No HTTP plumbing — fli handles transport, retries, and rate-limit backoff
internally. The client takes optional pre-built SearchFlights/SearchDates
instances so tests can substitute mocks that return fixture data without
touching the network.

fli's search APIs are synchronous and blocking; we wrap each call in
`asyncio.to_thread` to keep the MCP server's event loop free for concurrent
tool calls.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from fli.models import (
    Airline as FliAirline,
    Airport as FliAirport,
    DateSearchFilters,
    FlightSearchFilters,
    FlightSegment,
    MaxStops as FliMaxStops,
    PassengerInfo,
    SeatType,
    SortBy,
    TimeRestrictions,
    TripType,
)
from fli.search import SearchDates, SearchFlights

from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.fli_backend.normalize import (
    booking_url_for,
    build_date_offers,
    build_offers,
)
from trip_search_mcp.models import (
    DatePriceOffer,
    FlightOffer,
    MaxStops,
    SearchCheapestDatesInput,
    SearchFlightsInput,
)


class _SearchProtocol(Protocol):
    """Structural type any flight/date searcher must satisfy."""
    def search(self, filters: Any, *args, **kwargs) -> Any: ...


class FliClient:
    def __init__(
        self,
        *,
        flight_searcher: _SearchProtocol | None = None,
        date_searcher: _SearchProtocol | None = None,
    ):
        self._flight = flight_searcher if flight_searcher is not None else SearchFlights()
        self._date = date_searcher if date_searcher is not None else SearchDates()

    async def search(self, params: SearchFlightsInput) -> list[FlightOffer]:
        filters = self._build_filters(params)
        try:
            raw = await asyncio.to_thread(
                self._flight.search, filters, params.max_results
            )
        except ToolError:
            raise
        except KeyError as e:
            # Airport/Airline lookup miss after we passed validation should
            # have been caught in `_build_filters`; surface anything that
            # leaks here as an upstream issue rather than crashing.
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"fli rejected a lookup: {e}",
                retryable=False,
            ) from e
        except Exception as e:
            # fli's `search_with_retry` exhausts after several 429s/5xx;
            # treat any final failure as upstream.
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"fli search failed: {type(e).__name__}: {e}",
                retryable=True,
            ) from e

        entries: list = list(raw) if raw else []
        if not entries:
            raise ToolError(ErrorCode.NO_RESULTS, "Google Flights returned no options for this search.")

        booking_url = booking_url_for(
            params.origin, params.destination, params.departure_date, params.return_date,
        )

        try:
            offers = build_offers(
                entries,
                cabin=params.cabin_class,
                adults=params.adults,
                booking_url=booking_url,
                departure_date=params.departure_date,
                return_date=params.return_date,
                limit=params.max_results,
                inbound_window=params.inbound_window,
            )
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            # Defensive: a malformed fli result (e.g. an unknown airline enum
            # member, missing leg field) shouldn't escape as a raw exception.
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"fli returned a result we couldn't parse: {e}",
                retryable=True,
            ) from e

        if not offers:
            raise ToolError(ErrorCode.NO_RESULTS, "Google Flights returned no usable offers.")
        return offers

    # ----- filter construction ----------------------------------------------

    @staticmethod
    def _time_restrictions_from_window(window: str | None) -> TimeRestrictions | None:
        """Translate a user `departure_window` ('HH-HH', inclusive start /
        exclusive end) into fli's `TimeRestrictions` (inclusive on both
        bounds when forwarded to Google Flights).

        Subtract 1 from the user's end so the upstream filter caps at the
        last hour the user actually wants included. Example:
          user "8-20"   → fli (earliest=8, latest=19)   admits 08:00-19:59
          user "8-9"    → fli (earliest=8, latest=8)    admits 08:00-08:59
          user "0-1"    → fli (earliest=0, latest=1)    admits 00:00-01:59
                          (slightly looser than requested — fli's
                          `latest_departure` is PositiveInt so we can't
                          pass 0; this is the closest representable bound)
        """
        if not window:
            return None
        start_s, end_s = window.split("-")
        start = int(start_s)
        end = int(end_s)
        latest = max(1, end - 1)
        return TimeRestrictions(earliest_departure=start, latest_departure=latest)

    def _build_filters(self, p: SearchFlightsInput) -> FlightSearchFilters:
        try:
            origin = FliAirport[p.origin]
            destination = FliAirport[p.destination]
        except KeyError as e:
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"Airport code {e.args[0]!r} is not recognized by Google Flights.",
            ) from e

        try:
            airlines = (
                [FliAirline[code] for code in p.airlines] if p.airlines else None
            )
        except KeyError as e:
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"Airline IATA code {e.args[0]!r} is not recognized.",
            ) from e

        time_restrictions = self._time_restrictions_from_window(p.departure_window)

        segments = [
            FlightSegment(
                departure_airport=[[origin, 0]],
                arrival_airport=[[destination, 0]],
                travel_date=p.departure_date,
                time_restrictions=time_restrictions,
            )
        ]
        if p.return_date:
            # fli's outbound-only filter doesn't constrain the return leg;
            # we apply the user's `inbound_window` (if set) as a post-filter
            # in normalize.build_offers. The outbound `time_restrictions`
            # are intentionally NOT reused on the return segment.
            segments.append(FlightSegment(
                departure_airport=[[destination, 0]],
                arrival_airport=[[origin, 0]],
                travel_date=p.return_date,
                time_restrictions=None,
            ))

        return FlightSearchFilters(
            trip_type=TripType.ROUND_TRIP if p.return_date else TripType.ONE_WAY,
            passenger_info=PassengerInfo(
                adults=p.adults,
                children=p.children,
                infants_on_lap=p.infants,
            ),
            seat_type=SeatType[p.cabin_class.value],
            stops=FliMaxStops[p.max_stops.value],
            airlines=airlines,
            sort_by=SortBy.BEST,
            flight_segments=segments,
        )

    # ----- date-flex (search_cheapest_dates) ---------------------------------

    async def search_dates(self, params: SearchCheapestDatesInput) -> list[DatePriceOffer]:
        filters = self._build_dates_filters(params)
        try:
            raw = await asyncio.to_thread(self._date.search, filters)
        except ToolError:
            raise
        except KeyError as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"fli date lookup miss: {e}",
                retryable=False,
            ) from e
        except Exception as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"fli SearchDates failed: {type(e).__name__}: {e}",
                retryable=True,
            ) from e

        entries: list = list(raw) if raw else []
        if not entries:
            raise ToolError(
                ErrorCode.NO_RESULTS,
                "Google Flights returned no price data for this date range.",
            )

        try:
            offers = build_date_offers(entries)
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"fli returned a date-price entry we couldn't parse: {e}",
                retryable=True,
            ) from e

        if not offers:
            raise ToolError(ErrorCode.NO_RESULTS, "No usable date-price entries.")
        return offers

    def _build_dates_filters(self, p: SearchCheapestDatesInput) -> DateSearchFilters:
        try:
            origin = FliAirport[p.origin]
            destination = FliAirport[p.destination]
        except KeyError as e:
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"Airport code {e.args[0]!r} is not recognized by Google Flights.",
            ) from e

        try:
            airlines = (
                [FliAirline[code] for code in p.airlines] if p.airlines else None
            )
        except KeyError as e:
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"Airline IATA code {e.args[0]!r} is not recognized.",
            ) from e

        time_restrictions = self._time_restrictions_from_window(p.departure_window)

        # SearchDates derives the date matrix from from_date/to_date/duration;
        # the flight_segments still need a placeholder travel_date inside the
        # range so fli accepts the filter object.
        segments = [
            FlightSegment(
                departure_airport=[[origin, 0]],
                arrival_airport=[[destination, 0]],
                travel_date=p.start_date,
                time_restrictions=time_restrictions,
            )
        ]
        if p.is_round_trip:
            # trip_duration is guaranteed set at this point by the model
            # validator; mypy can't see that across the Pydantic boundary.
            # departure_window applies to the OUTBOUND segment only — same
            # reasoning as in _build_filters.
            segments.append(FlightSegment(
                departure_airport=[[destination, 0]],
                arrival_airport=[[origin, 0]],
                travel_date=p.start_date,  # placeholder; SearchDates ignores this
                time_restrictions=None,
            ))

        return DateSearchFilters(
            trip_type=TripType.ROUND_TRIP if p.is_round_trip else TripType.ONE_WAY,
            passenger_info=PassengerInfo(adults=p.passengers),
            seat_type=SeatType[p.cabin_class.value],
            stops=FliMaxStops[p.max_stops.value],
            airlines=airlines,
            from_date=p.start_date,
            to_date=p.end_date,
            duration=p.trip_duration,
            flight_segments=segments,
        )
