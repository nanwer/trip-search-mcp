from flights_mcp.models import CabinClass
from flights_mcp.serpapi.normalize import (
    _coerce_cabin,
    _iso_duration,
    _iso_local_time,
    _split_flight_number,
    build_one_way_offers,
    build_round_trip_offer,
)
from flights_mcp.serpapi.raw import SerpFlightOption, SerpGoogleFlightsResponse


# ----- formatting helpers ----------------------------------------------------


def test_iso_local_time_converts_space_to_T_and_pads_seconds():
    assert _iso_local_time("2026-05-18 15:00") == "2026-05-18T15:00:00"


def test_iso_local_time_preserves_already_iso():
    assert _iso_local_time("2026-05-18T15:00:00") == "2026-05-18T15:00:00"


def test_iso_local_time_empty_returns_empty():
    assert _iso_local_time(None) == ""
    assert _iso_local_time("") == ""


def test_iso_duration_handles_hours_and_minutes():
    assert _iso_duration(220) == "PT3H40M"


def test_iso_duration_handles_minutes_only():
    assert _iso_duration(45) == "PT45M"


def test_iso_duration_handles_zero():
    assert _iso_duration(0) == "PT0M"


def test_split_flight_number_extracts_iata_prefix():
    assert _split_flight_number("FI 343", "Icelandair") == ("FI", "FI343")


def test_split_flight_number_handles_three_char_prefix():
    assert _split_flight_number("9W 100", "Jet Airways") == ("9W", "9W100")


def test_split_flight_number_falls_back_when_format_unknown():
    airline, number = _split_flight_number(None, "Finnair")
    assert airline == "FINNAIR"
    assert number == "FINNAIR"


# ----- cabin coercion --------------------------------------------------------


def test_coerce_cabin_handles_serpapi_capitalization():
    assert _coerce_cabin("Economy") is CabinClass.ECONOMY
    assert _coerce_cabin("Business") is CabinClass.BUSINESS
    assert _coerce_cabin("First") is CabinClass.FIRST


def test_coerce_cabin_falls_back_on_unknown():
    assert _coerce_cabin("MYSTERY") is CabinClass.ECONOMY


def test_coerce_cabin_handles_none():
    assert _coerce_cabin(None) is CabinClass.ECONOMY


# ----- offer construction ----------------------------------------------------


def test_build_one_way_offers_from_fixture(serpapi_one_way):
    parsed = SerpGoogleFlightsResponse.model_validate(serpapi_one_way)
    all_options = list(parsed.best_flights) + list(parsed.other_flights)
    offers = build_one_way_offers(all_options, currency="USD", adults=1, limit=20)

    assert len(offers) == 2
    direct = offers[0]
    assert direct.offer_id == "ONE_WAY_TOKEN_A"
    assert direct.total_price == 460.0
    assert direct.price_per_adult == 460.0
    assert direct.currency == "USD"
    assert direct.inbound is None
    assert direct.outbound.stops == 0
    assert direct.outbound.duration == "PT10H30M"
    assert direct.outbound.segments[0].airline == "AY"
    assert direct.outbound.segments[0].flight_number == "AY17"
    assert direct.outbound.segments[0].departure_time_local == "2026-05-18T13:00:00"
    assert direct.outbound.segments[0].cabin is CabinClass.ECONOMY

    connecting = offers[1]
    assert connecting.outbound.stops == 1
    assert set(connecting.airlines) == {"KL"}


def test_build_one_way_offers_respects_limit(serpapi_one_way):
    parsed = SerpGoogleFlightsResponse.model_validate(serpapi_one_way)
    all_options = list(parsed.best_flights) + list(parsed.other_flights)
    offers = build_one_way_offers(all_options, currency="USD", adults=1, limit=1)
    assert len(offers) == 1


def test_build_one_way_divides_price_per_adult(serpapi_one_way):
    parsed = SerpGoogleFlightsResponse.model_validate(serpapi_one_way)
    options = list(parsed.best_flights)
    offers = build_one_way_offers(options, currency="USD", adults=2, limit=1)
    assert offers[0].total_price == 460.0
    assert offers[0].price_per_adult == 230.0


def test_build_round_trip_offer_pairs_outbound_and_return(
    serpapi_round_trip_outbound, serpapi_round_trip_return
):
    outbound = SerpGoogleFlightsResponse.model_validate(serpapi_round_trip_outbound)
    returns = SerpGoogleFlightsResponse.model_validate(serpapi_round_trip_return)

    outbound_option = outbound.best_flights[0]
    return_option = returns.best_flights[0]

    offer = build_round_trip_offer(
        outbound_option, return_option, currency="USD", adults=1,
    )

    # offer_id comes from the return-leg's booking_token (the round-trip identifier).
    assert offer.offer_id == "BOOKING_TOKEN_A1"
    # Total price comes from the return-leg call, reflecting the actual round-trip total.
    assert offer.total_price == 742.0
    assert offer.outbound.stops == 1
    assert offer.outbound.duration == "PT11H20M"  # 680 minutes
    assert offer.inbound is not None
    assert offer.inbound.stops == 1
    assert offer.inbound.duration == "PT12H45M"  # 765 minutes
    assert set(offer.airlines) == {"AY", "AA"}
    assert offer.validating_airline == "AY"


def test_offers_have_null_amadeus_only_fields(serpapi_one_way):
    """SerpAPI doesn't carry baggage_allowance, last_ticketing_date, or
    seats_available; these surface as null to be transparent to Claude."""
    parsed = SerpGoogleFlightsResponse.model_validate(serpapi_one_way)
    options = list(parsed.best_flights)
    offer = build_one_way_offers(options, currency="USD", adults=1, limit=1)[0]
    assert offer.baggage_allowance is None
    assert offer.last_ticketing_date is None
    assert offer.seats_available is None
    assert offer.fare_basis == ""
