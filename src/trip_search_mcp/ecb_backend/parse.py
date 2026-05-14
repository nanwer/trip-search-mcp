"""Parse the ECB daily reference-rates XML feed.

Schema (https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml):

  <gesmes:Envelope xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
    <gesmes:Sender>...</gesmes:Sender>
    <Cube>
      <Cube time="2026-05-13">
        <Cube currency="USD" rate="1.1715"/>
        <Cube currency="JPY" rate="184.83"/>
        ...
      </Cube>
    </Cube>
  </gesmes:Envelope>

All rates are quoted against EUR (1 EUR = `rate` of the listed currency).
EUR itself is implicit — never in the list.

Pure parser; no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

_NS = {
    "gesmes": "http://www.gesmes.org/xml/2002-08-01",
    "exr": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref",
}


@dataclass(frozen=True)
class EcbRates:
    """One day of ECB reference rates against EUR."""
    rate_date: str                       # "YYYY-MM-DD"
    rates: dict[str, float]              # currency code → rate (1 EUR = `rate` X)


class EcbParseError(ValueError):
    """Raised when the XML isn't in the expected ECB shape."""


def parse_ecb_xml(xml_bytes: bytes) -> EcbRates:
    """Pure parser: XML bytes → EcbRates.

    Raises EcbParseError if the document doesn't have the expected
    structure (no inner Cube with @time, no rate cubes).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise EcbParseError(f"ECB XML failed to parse: {e}") from e

    inner_cubes = root.findall(".//exr:Cube[@time]", _NS)
    if not inner_cubes:
        raise EcbParseError(
            "ECB XML missing the dated inner <Cube> element — schema may have changed."
        )
    inner = inner_cubes[0]
    rate_date = inner.get("time", "")
    if not rate_date:
        raise EcbParseError("ECB XML's dated Cube has no time attribute.")

    rates: dict[str, float] = {}
    for cube in inner.findall("exr:Cube", _NS):
        cur = cube.get("currency")
        rate_str = cube.get("rate")
        if not cur or not rate_str:
            continue
        try:
            rates[cur.upper()] = float(rate_str)
        except ValueError:
            # Skip a single malformed entry rather than failing the whole
            # feed; the others stay usable.
            continue

    if not rates:
        raise EcbParseError("ECB XML produced an empty rates dict.")

    # EUR is implicit — quote it as 1.0 against itself so downstream
    # `EUR → EUR` math returns rate=1.0 and converted=amount unchanged.
    rates["EUR"] = 1.0
    return EcbRates(rate_date=rate_date, rates=rates)


def convert(
    rates: EcbRates,
    *,
    amount: float,
    from_currency: str,
    to_currency: str,
) -> tuple[float, float]:
    """EUR-pivot conversion math.

    Returns (converted_amount, rate). `rate` is the multiplier such that
    `amount * rate == converted_amount`.

    Raises KeyError if either currency isn't in the rates dict — caller
    surfaces a clean INVALID_INPUT envelope.
    """
    fc = from_currency.upper()
    tc = to_currency.upper()
    if fc not in rates.rates:
        raise KeyError(fc)
    if tc not in rates.rates:
        raise KeyError(tc)
    # rates[X] means "1 EUR = X units of currency".
    # Convert from_currency → EUR → to_currency.
    in_eur = amount / rates.rates[fc]
    converted = in_eur * rates.rates[tc]
    # Effective rate (from → to).
    rate = rates.rates[tc] / rates.rates[fc]
    return converted, rate
