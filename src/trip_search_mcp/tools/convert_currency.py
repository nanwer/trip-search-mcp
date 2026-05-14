"""The `convert_currency` tool function.

Wraps the ECB daily reference-rate feed. EUR-pivoted conversion:
any → EUR → any. Same-currency conversions short-circuit to rate=1.0
without fetching the feed.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from trip_search_mcp.ecb_backend.client import EcbClient
from trip_search_mcp.ecb_backend.parse import convert as _ecb_convert
from trip_search_mcp.errors import ErrorCode, ToolError, error_response
from trip_search_mcp.logging_config import log_event
from trip_search_mcp.models import ConvertCurrencyInput

TOOL_NAME = "convert_currency"

_LEVEL_FOR_CODE = {
    ErrorCode.RATE_LIMITED: logging.WARNING,
    ErrorCode.UPSTREAM_ERROR: logging.WARNING,
}

TOOL_DESCRIPTION = """\
Convert a numeric amount between two ISO 4217 currencies using the European Central Bank's daily reference rates.

USE THIS TOOL WHEN:
- The user asks for a conversion ("how much is ¥30,000 in euros?", "what's $200 in pounds?")
- You're presenting mixed-currency trip totals (flights in EUR + hotel in USD + activity in GBP) and want to give one consolidated number
- The user wants to compare prices across vendors quoting in different currencies

Inputs:
- `amount` (float, > 0) — the numeric value to convert.
- `from_currency` (3-letter ISO 4217 code, uppercase) — e.g. "EUR", "USD", "JPY".
- `to_currency` (3-letter ISO 4217 code, uppercase) — e.g. "EUR", "USD", "GBP".

Returns:
- `converted_amount` — the result, rounded to 2 decimal places in your response (the raw float is precise).
- `rate` — the effective rate (1 from_currency = rate to_currency).
- `rate_date` — the ISO date of ECB's published rates. ECB updates daily around 16:00 CET. **Weekend / holiday queries return the previous business day's rates** — disclose this if the gap is more than 3 days.
- `source` — always `"ECB"`.

Powered by the European Central Bank's daily reference rates feed (free, no API key). 29+ currencies covered (USD, EUR, JPY, GBP, CAD, AUD, CHF, SEK, NOK, DKK, INR, MXN, BRL, SGD, KRW, CNY, THB, HKD, NZD, CZK, HUF, IDR, ILS, ISK, MYR, PHP, PLN, RON, TRY, ZAR). If the user names a currency we don't recognize, we return `invalid_input`.

RESULT PRESENTATION: inline prose, not an artifact. Example: *"¥30,000 = €182.45 (rate as of 13 May 2026 via ECB)."* For a multi-line trip-cost summary, include the rate_date once at the bottom rather than per-line."""

_logger = logging.getLogger("trip_search_mcp")


async def convert_currency(
    *,
    client: EcbClient,
    amount: float,
    from_currency: str,
    to_currency: str,
) -> dict[str, Any]:
    raw_input = dict(
        amount=amount, from_currency=from_currency, to_currency=to_currency,
    )

    # 1. Input validation (ISO 4217 regex + amount > 0).
    try:
        params = ConvertCurrencyInput.model_validate(raw_input)
    except ValidationError as e:
        first = e.errors()[0]
        field_path = ".".join(str(p) for p in first.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first.get("msg"))
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    # 2. Same-currency short-circuit — saves a fetch.
    if params.from_currency == params.to_currency:
        log_event(_logger, "tool.success", tool=TOOL_NAME,
                  short_circuit=True, currency=params.from_currency)
        return {
            "amount": params.amount,
            "from_currency": params.from_currency,
            "to_currency": params.to_currency,
            "converted_amount": params.amount,
            "rate": 1.0,
            "rate_date": None,
            "source": "identity",
        }

    # 3. Fetch (cached at the client) + convert.
    try:
        rates = await client.get_rates()
    except ToolError as e:
        log_event(_logger, "tool.upstream_error", tool=TOOL_NAME,
                  code=e.code.value,
                  level=_LEVEL_FOR_CODE.get(e.code, logging.WARNING))
        return error_response(e.code, e.message, retryable=e.retryable)

    try:
        converted, rate = _ecb_convert(
            rates,
            amount=params.amount,
            from_currency=params.from_currency,
            to_currency=params.to_currency,
        )
    except KeyError as e:
        # The currency code passed our regex but isn't in ECB's list.
        unknown = e.args[0] if e.args else "?"
        msg = (
            f"ECB doesn't quote {unknown!r}. The feed covers: "
            f"{', '.join(sorted(rates.rates.keys()))}."
        )
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, error=msg)
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    log_event(
        _logger, "tool.success", tool=TOOL_NAME,
        from_=params.from_currency, to=params.to_currency,
        amount=params.amount, rate_date=rates.rate_date,
    )
    return {
        "amount": params.amount,
        "from_currency": params.from_currency,
        "to_currency": params.to_currency,
        "converted_amount": converted,
        "rate": rate,
        "rate_date": rates.rate_date,
        "source": "ECB",
    }
