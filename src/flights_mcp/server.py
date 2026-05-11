"""FastMCP server entry point. Run via `fastmcp run src/flights_mcp/server.py`
or `python -m flights_mcp.server`."""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastmcp import FastMCP

from flights_mcp.amadeus.client import AmadeusClient, base_url_for_env
from flights_mcp.cache import TTLCache
from flights_mcp.logging_config import configure_logging, log_event
from flights_mcp.tools.search_flights import TOOL_DESCRIPTION, search_flights

_logger = configure_logging()


def _build_amadeus() -> AmadeusClient:
    client_id = os.environ["AMADEUS_CLIENT_ID"]
    client_secret = os.environ["AMADEUS_CLIENT_SECRET"]
    env = os.environ.get("AMADEUS_ENV", "test")
    http = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
    return AmadeusClient(
        http=http,
        base_url=base_url_for_env(env),
        client_id=client_id,
        client_secret=client_secret,
    )


_AMADEUS = _build_amadeus()
_CACHE = TTLCache(ttl_seconds=int(os.environ.get("CACHE_TTL_SECONDS", "300")))
_ENV = os.environ.get("AMADEUS_ENV", "test")

mcp = FastMCP("flights-mcp")


@mcp.tool(name="search_flights", description=TOOL_DESCRIPTION)
async def search_flights_tool(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin_class: str = "ECONOMY",
    currency: str = "USD",
    non_stop_only: bool = False,
    max_results: int = 20,
) -> dict[str, Any]:
    return await search_flights(
        amadeus=_AMADEUS,
        cache=_CACHE,
        env=_ENV,
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
        children=children,
        infants=infants,
        cabin_class=cabin_class,
        currency=currency,
        non_stop_only=non_stop_only,
        max_results=max_results,
    )


def main() -> None:
    log_event(_logger, "server.start", env=_ENV)
    mcp.run()


if __name__ == "__main__":
    main()
