"""Tests for the ECB backend + convert_currency tool.

Uses the Phase 0 fixture (real ECB daily XML for 2026-05-13) plus
httpx.MockTransport for client-orchestration tests. No live ECB
fetches in CI.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from trip_search_mcp.ecb_backend.client import EcbClient
from trip_search_mcp.ecb_backend.parse import (
    EcbParseError,
    EcbRates,
    convert,
    parse_ecb_xml,
)
from trip_search_mcp.errors import ErrorCode, ToolError
from trip_search_mcp.tools.convert_currency import convert_currency

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def ecb_xml() -> bytes:
    return (FIXTURES / "ecb_eurofxref_daily.xml").read_bytes()


# ----- parse_ecb_xml --------------------------------------------------------


def test_parse_real_fixture_extracts_rate_date(ecb_xml):
    rates = parse_ecb_xml(ecb_xml)
    assert rates.rate_date == "2026-05-13"


def test_parse_real_fixture_covers_all_realistic_currencies(ecb_xml):
    rates = parse_ecb_xml(ecb_xml)
    expected = {"USD", "JPY", "GBP", "CAD", "AUD", "CHF", "SEK", "NOK",
                "DKK", "INR", "MXN", "BRL", "SGD", "KRW", "CNY", "THB",
                "HKD", "NZD"}
    missing = expected - set(rates.rates.keys())
    assert not missing, f"missing currencies: {missing}"


def test_parse_real_fixture_includes_eur_as_one(ecb_xml):
    """EUR is implicit in the feed; we synthesize it as 1.0 so EUR→EUR
    math works without special-casing in callers."""
    rates = parse_ecb_xml(ecb_xml)
    assert rates.rates["EUR"] == 1.0


def test_parse_rejects_malformed_xml():
    with pytest.raises(EcbParseError):
        parse_ecb_xml(b"<not really xml")


def test_parse_rejects_xml_without_dated_cube():
    """Empty envelope: schema looks valid but no inner dated Cube."""
    minimal = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"'
        b' xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
        b'<gesmes:subject>Reference rates</gesmes:subject>'
        b'</gesmes:Envelope>'
    )
    with pytest.raises(EcbParseError):
        parse_ecb_xml(minimal)


# ----- convert (EUR-pivot math) ---------------------------------------------


def _rates() -> EcbRates:
    """Hand-built rates dict for math-only tests. Doesn't touch the
    real fixture so the assertions stay stable across ECB updates."""
    return EcbRates(
        rate_date="2026-05-13",
        rates={"EUR": 1.0, "USD": 1.10, "GBP": 0.85, "JPY": 165.0},
    )


def test_convert_same_currency_via_math():
    """EUR → EUR via math (not the short-circuit) still yields 1.0."""
    converted, rate = convert(_rates(), amount=100, from_currency="EUR", to_currency="EUR")
    assert converted == 100.0
    assert rate == 1.0


def test_convert_eur_to_usd_uses_direct_rate():
    converted, rate = convert(_rates(), amount=100, from_currency="EUR", to_currency="USD")
    assert converted == pytest.approx(110.0, rel=1e-9)
    assert rate == pytest.approx(1.10, rel=1e-9)


def test_convert_usd_to_eur_is_division():
    converted, rate = convert(_rates(), amount=110, from_currency="USD", to_currency="EUR")
    assert converted == pytest.approx(100.0, rel=1e-9)
    assert rate == pytest.approx(1.0 / 1.10, rel=1e-9)


def test_convert_cross_pair_pivots_via_eur():
    """JPY → USD must equal (1/JPY_rate) * USD_rate per EUR-pivot."""
    converted, rate = convert(_rates(), amount=165_000, from_currency="JPY", to_currency="USD")
    # 165,000 JPY / 165 (JPY per EUR) = 1000 EUR; * 1.10 = 1100 USD.
    assert converted == pytest.approx(1100.0, rel=1e-9)
    assert rate == pytest.approx(1.10 / 165.0, rel=1e-9)


def test_convert_unknown_currency_raises_keyerror():
    with pytest.raises(KeyError) as exc:
        convert(_rates(), amount=100, from_currency="EUR", to_currency="XYZ")
    assert exc.value.args[0] == "XYZ"


# ----- EcbClient ------------------------------------------------------------


def _stub_client(xml_bytes: bytes) -> EcbClient:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=xml_bytes),
    )
    http = httpx.AsyncClient(transport=transport)
    return EcbClient(http=http)


async def test_client_fetches_and_parses(ecb_xml):
    client = _stub_client(ecb_xml)
    rates = await client.get_rates()
    assert rates.rate_date == "2026-05-13"
    assert "USD" in rates.rates


async def test_client_caches_within_ttl(ecb_xml):
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, content=ecb_xml)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = EcbClient(http=http)
    await client.get_rates()
    await client.get_rates()
    await client.get_rates()
    assert call_count["n"] == 1, "expected exactly one ECB fetch — cache hit on calls 2+3"


async def test_client_500_maps_to_upstream_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    http = httpx.AsyncClient(transport=transport)
    client = EcbClient(http=http)
    with pytest.raises(ToolError) as exc:
        await client.get_rates()
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR


async def test_client_unparseable_body_maps_to_upstream_error():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"<not valid xml")
    )
    http = httpx.AsyncClient(transport=transport)
    client = EcbClient(http=http)
    with pytest.raises(ToolError) as exc:
        await client.get_rates()
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR


# ----- convert_currency tool function ---------------------------------------


async def test_tool_happy_path_eur_to_usd(ecb_xml):
    client = _stub_client(ecb_xml)
    result = await convert_currency(
        client=client, amount=100, from_currency="EUR", to_currency="USD",
    )
    assert "error" not in result
    assert result["from_currency"] == "EUR"
    assert result["to_currency"] == "USD"
    assert result["converted_amount"] > 0
    assert result["source"] == "ECB"
    assert result["rate_date"] == "2026-05-13"


async def test_tool_same_currency_short_circuits(ecb_xml):
    """EUR → EUR returns immediately without fetching ECB."""
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, content=ecb_xml)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = EcbClient(http=http)
    result = await convert_currency(
        client=client, amount=100, from_currency="EUR", to_currency="EUR",
    )
    assert call_count["n"] == 0
    assert result["converted_amount"] == 100
    assert result["rate"] == 1.0
    assert result["source"] == "identity"


async def test_tool_unknown_currency_returns_invalid_input(ecb_xml):
    client = _stub_client(ecb_xml)
    result = await convert_currency(
        client=client, amount=100, from_currency="XYZ", to_currency="USD",
    )
    assert result["error"]["code"] == "invalid_input"


async def test_tool_rejects_negative_amount(ecb_xml):
    client = _stub_client(ecb_xml)
    result = await convert_currency(
        client=client, amount=-50, from_currency="EUR", to_currency="USD",
    )
    assert result["error"]["code"] == "invalid_input"


async def test_tool_rejects_lowercase_currency(ecb_xml):
    """ISO 4217 codes are uppercase. Lowercase 'usd' should be rejected."""
    client = _stub_client(ecb_xml)
    result = await convert_currency(
        client=client, amount=100, from_currency="usd", to_currency="EUR",
    )
    assert result["error"]["code"] == "invalid_input"


async def test_tool_known_jpy_to_eur_matches_ecb_rate(ecb_xml):
    """30,000 JPY at the fixture's rate (184.83) should be ~162.31 EUR."""
    client = _stub_client(ecb_xml)
    result = await convert_currency(
        client=client, amount=30_000, from_currency="JPY", to_currency="EUR",
    )
    assert result["converted_amount"] == pytest.approx(30_000 / 184.83, rel=1e-6)


async def test_tool_upstream_failure_returns_envelope():
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    http = httpx.AsyncClient(transport=transport)
    client = EcbClient(http=http)
    result = await convert_currency(
        client=client, amount=100, from_currency="EUR", to_currency="USD",
    )
    assert result["error"]["code"] == "upstream_error"
