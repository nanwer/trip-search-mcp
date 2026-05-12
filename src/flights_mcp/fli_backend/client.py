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

from flights_mcp.errors import ErrorCode, ToolError
from flights_mcp.fli_backend.normalize import booking_url_for, build_offers
from flights_mcp.models import FlightOffer, MaxStops, SearchFlightsInput


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

        time_restrictions = None
        if p.departure_window:
            start_s, end_s = p.departure_window.split("-")
            time_restrictions = TimeRestrictions(
                earliest_departure=int(start_s),
                latest_departure=int(end_s),
            )

        segments = [
            FlightSegment(
                departure_airport=[[origin, 0]],
                arrival_airport=[[destination, 0]],
                travel_date=p.departure_date,
                time_restrictions=time_restrictions,
            )
        ]
        if p.return_date:
            segments.append(FlightSegment(
                departure_airport=[[destination, 0]],
                arrival_airport=[[origin, 0]],
                travel_date=p.return_date,
                # Apply the same window to the return leg — users almost
                # always want symmetric departure preferences.
                time_restrictions=time_restrictions,
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
