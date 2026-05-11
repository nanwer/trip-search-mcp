"""FastMCP server entry point. Run via `fastmcp run src/flights_mcp/server.py`
or `python -m flights_mcp.server`."""
from __future__ import annotations

import os
import warnings
from typing import Any

import httpx

# FastMCP transitively imports authlib.jose, which emits an AuthlibDeprecation-
# Warning at every interpreter start. authlib *replaces* the global warning
# filter list on its own import, so the usual filterwarnings/catch_warnings
# tricks don't stick. Intercept at the display layer instead — drop only its
# own class by name so genuine deprecation warnings still surface.
_original_showwarning = warnings.showwarning

def _drop_authlib_deprecation(message, category, filename, lineno, file=None, line=None):
    if category.__name__ == "AuthlibDeprecationWarning":
        return
    _original_showwarning(message, category, filename, lineno, file, line)

warnings.showwarning = _drop_authlib_deprecation

from fastmcp import FastMCP  # noqa: E402 — must follow the warning hook above

from flights_mcp.cache import TTLCache
from flights_mcp.logging_config import configure_logging, log_event
from flights_mcp.serpapi.client import SerpAPIClient
from flights_mcp.tools.search_flights import TOOL_DESCRIPTION, search_flights

_logger = configure_logging()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. "
            "Copy .env.example to .env and fill in the SerpAPI key, "
            "or export the variable in your shell before running the server."
        )
    return value


def _build_client() -> SerpAPIClient:
    api_key = _require_env("SERPAPI_KEY")
    # AsyncClient is intentionally not closed — process lifetime matches mcp.run().
    http = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
    return SerpAPIClient(http=http, api_key=api_key)


_CLIENT = _build_client()
_CACHE = TTLCache(ttl_seconds=int(os.environ.get("CACHE_TTL_SECONDS", "300")))

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
    max_results: int | None = None,
) -> dict[str, Any]:
    return await search_flights(
        client=_CLIENT,
        cache=_CACHE,
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
    log_event(_logger, "server.start")
    mcp.run()


if __name__ == "__main__":
    main()
