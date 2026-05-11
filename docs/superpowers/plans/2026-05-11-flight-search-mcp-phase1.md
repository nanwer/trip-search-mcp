# Flight Search MCP — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a local stdio FastMCP server exposing one tool, `search_flights`, that wraps Amadeus Flight Offers Search with full error contract, response caching, time-format discipline, and fixture-driven tests.

**Architecture:** Layered Python package. Pydantic at the boundary (input validation, typed output, structured errors). Thin async Amadeus client with substitutable HTTP transport (httpx) for fixture-driven tests. TTL response cache keyed on canonical input hash, async-safe token cache with refresh lock. FastMCP wires it all into a stdio tool.

**Tech Stack:** Python 3.12, FastMCP 2.x, Pydantic v2, httpx (async), pytest + pytest-asyncio. Package layout under `src/flights_mcp/`.

---

## Prerequisites & Parallel Tracks

**Before development can fully verify Phase 1, the user must complete the Phase 0 gates from SPEC.md.** Specifically:

1. **Phase 0.1** — sign up for Amadeus Self-Service, call Flight Offers Search via Postman for HEL→IAD, save the response as `tests/fixtures/hel_iad_round_trip.json`, AND confirm the `at` field format (offset or no offset).
2. **Phase 0.2** — confirm "HTTP + static bearer in Phase 2, claude.ai web deferred to Phase 5" is acceptable.
3. **Phase 0.3** — confirm "this MCP can never book, only search" is acceptable.

**Parallel-track strategy:** While the user does Phase 0.1, development proceeds against a **synthetic fixture** (`tests/fixtures/synthetic_round_trip.json`) authored against the documented Amadeus v2 response schema. The synthetic fixture is sufficient for unit/integration tests of all normalization, caching, error, and tool logic. Once Phase 0.1's real fixture lands, an additional acceptance test validates that the same pipeline produces the documented output shape from the real response. **Do not block Tasks 1–15 on Phase 0.1.**

The only thing that genuinely requires real Amadeus credentials is acceptance criterion #3 (live MCP Inspector call). Everything else can be verified against fixtures.

---

## File Structure (locked-in)

```
.
├── SPEC.md                                   # Already written
├── README.md                                 # Created in Task 15
├── pyproject.toml                            # Created in Task 1
├── .env.example                              # Created in Task 1
├── .gitignore                                # Created in Task 1
├── src/flights_mcp/
│   ├── __init__.py                           # Created in Task 1
│   ├── server.py                             # Created in Task 13 — FastMCP app
│   ├── logging_config.py                     # Created in Task 2
│   ├── errors.py                             # Created in Task 3 — error codes + response model
│   ├── models.py                             # Created in Tasks 4–6 — Pydantic I/O models
│   ├── cache.py                              # Created in Task 9 — TTL response cache
│   ├── tools/
│   │   ├── __init__.py                       # Created in Task 1
│   │   └── search_flights.py                 # Created in Task 12 — tool function + description
│   └── amadeus/
│       ├── __init__.py                       # Created in Task 1
│       ├── normalize.py                      # Created in Task 8 — raw → clean
│       ├── token.py                          # Created in Task 10 — OAuth token cache
│       └── client.py                         # Created in Task 11 — HTTP client + error mapping
├── tests/
│   ├── __init__.py                           # Created in Task 1
│   ├── conftest.py                           # Created in Task 7 — fixture loaders
│   ├── fixtures/
│   │   ├── synthetic_round_trip.json         # Created in Task 7
│   │   ├── empty_results.json                # Created in Task 7
│   │   └── auth_failed.json                  # Created in Task 7
│   ├── test_models.py                        # Created in Tasks 4–6
│   ├── test_normalize.py                     # Created in Task 8
│   ├── test_cache.py                         # Created in Task 9
│   ├── test_token.py                         # Created in Task 10
│   ├── test_client.py                        # Created in Task 11
│   └── test_search_flights.py                # Created in Task 14
└── docs/
    └── superpowers/
        └── plans/
            └── 2026-05-11-flight-search-mcp-phase1.md   # This file
```

Notes:
- Single-package layout (no `flights-mcp/` subdir) — the working directory is the project root.
- `errors.py` lives at the package root (not in `amadeus/`) because the error codes are part of the tool contract, not Amadeus-specific.
- `token.py` is split from `client.py` to keep the lock-protected token cache isolated and easy to test in isolation.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/flights_mcp/__init__.py`
- Create: `src/flights_mcp/tools/__init__.py`
- Create: `src/flights_mcp/amadeus/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "flights-mcp"
version = "0.1.0"
description = "MCP server wrapping the Amadeus Flight Offers Search API"
requires-python = ">=3.12"
dependencies = [
    "fastmcp>=2.5,<3.0",
    "httpx>=0.27,<1.0",
    "pydantic>=2.7,<3.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
]

[project.scripts]
flights-mcp = "flights_mcp.server:main"

[tool.hatch.build.targets.wheel]
packages = ["src/flights_mcp"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.py[cod]
.venv/
.env
*.egg-info/
dist/
build/
.pytest_cache/
logs/
.DS_Store
```

- [ ] **Step 3: Write `.env.example`**

```
AMADEUS_CLIENT_ID=your-client-id-here
AMADEUS_CLIENT_SECRET=your-client-secret-here
AMADEUS_ENV=test
# LOG_FILE_PATH defaults to ~/.flights-mcp/logs/flight-search.log
# LOG_FILE_PATH=/absolute/path/to/log.jsonl
# LOG_LEVEL=INFO
# CACHE_TTL_SECONDS=300
```

- [ ] **Step 4: Create empty `__init__.py` files**

```bash
touch src/flights_mcp/__init__.py
touch src/flights_mcp/tools/__init__.py
touch src/flights_mcp/amadeus/__init__.py
touch tests/__init__.py
```

- [ ] **Step 5: Install in editable mode and verify**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -c "import flights_mcp; print('ok')"
```

Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore .env.example src tests
git commit -m "chore: scaffold project structure and dependencies"
```

---

## Task 2: Logging config

**Files:**
- Create: `src/flights_mcp/logging_config.py`
- Test: (deferred — config code is exercised by integration tests in Task 14)

- [ ] **Step 1: Write `src/flights_mcp/logging_config.py`**

```python
"""Structured JSON-line logging to a configurable absolute path.

The log path must be absolute to be robust to stdio's CWD inheritance.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


def _default_log_path() -> Path:
    return Path.home() / ".flights-mcp" / "logs" / "flight-search.log"


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = getattr(record, "extra_fields", {}) or {}
        payload: dict = dict(extras)
        payload["ts"] = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        payload["level"] = record.levelname
        payload["logger"] = record.name
        payload["msg"] = record.getMessage()
        payload.pop("exc", None)  # user-supplied "exc" cannot impersonate a traceback
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> logging.Logger:
    raw_path = os.environ.get("LOG_FILE_PATH")
    log_path = Path(raw_path) if raw_path else _default_log_path()
    if not log_path.is_absolute():
        raise ValueError(
            f"LOG_FILE_PATH must be an absolute path, got {log_path!r}. "
            "stdio inherits CWD from the MCP client and relative paths are unpredictable."
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("flights_mcp")
    logger.setLevel(level)
    logger.propagate = False
    # Once a handler is attached, LOG_FILE_PATH changes are ignored for the lifetime of the process.
    if logger.handlers:
        return logger

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(JsonLineFormatter())
    logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, msg: str, *, level: int = logging.INFO, **fields) -> None:
    """Emit a structured log line with arbitrary extra fields.

    `level` is checked against the logger's effective level. `fields` are merged
    into the JSON payload; core fields (ts, level, logger, msg, exc) win over
    user-supplied fields with the same name.
    """
    if not logger.isEnabledFor(level):
        return
    record = logger.makeRecord(
        logger.name, level, __file__, 0, msg, (), None
    )
    record.extra_fields = fields  # type: ignore[attr-defined]
    logger.handle(record)
```

- [ ] **Step 2: Smoke-test the config in a REPL session**

```bash
LOG_FILE_PATH=/tmp/flights-mcp-test.log python -c "
from flights_mcp.logging_config import configure_logging, log_event
log = configure_logging()
log_event(log, 'test', tool='search_flights', cache_hit=False)
print(open('/tmp/flights-mcp-test.log').read())
"
```

Expected output: A JSON line with `ts`, `level`, `msg`, `tool`, `cache_hit`.

- [ ] **Step 3: Commit**

```bash
git add src/flights_mcp/logging_config.py
git commit -m "feat: structured JSON-line logger with absolute-path enforcement"
```

---

## Task 3: Error contract

**Files:**
- Create: `src/flights_mcp/errors.py`
- Test: `tests/test_errors.py`

- [ ] **Step 1: Write failing test `tests/test_errors.py`**

```python
from flights_mcp.errors import ErrorCode, ToolError, error_response


def test_error_code_values_match_spec():
    assert ErrorCode.NO_RESULTS.value == "no_results"
    assert ErrorCode.INVALID_INPUT.value == "invalid_input"
    assert ErrorCode.QUOTA_EXCEEDED.value == "quota_exceeded"
    assert ErrorCode.RATE_LIMITED.value == "rate_limited"
    assert ErrorCode.UPSTREAM_ERROR.value == "upstream_error"
    assert ErrorCode.AUTH_FAILED.value == "auth_failed"


def test_error_response_shape():
    out = error_response(ErrorCode.NO_RESULTS, "No flights found.", retryable=False)
    assert out == {
        "error": {
            "code": "no_results",
            "message": "No flights found.",
            "retryable": False,
        }
    }


def test_tool_error_carries_code_and_message():
    err = ToolError(ErrorCode.AUTH_FAILED, "bad creds")
    assert err.code == ErrorCode.AUTH_FAILED
    assert err.message == "bad creds"
    assert err.retryable is False
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_errors.py -v
```

Expected: ImportError / ModuleNotFoundError on `flights_mcp.errors`.

- [ ] **Step 3: Write `src/flights_mcp/errors.py`**

```python
"""Tool error contract — every failure path returns one of these codes."""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    NO_RESULTS = "no_results"
    INVALID_INPUT = "invalid_input"
    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    AUTH_FAILED = "auth_failed"


class ToolError(Exception):
    """Raised internally, caught at the tool boundary, converted to error_response."""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def error_response(code: ErrorCode, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "error": {
            "code": code.value,
            "message": message,
            "retryable": retryable,
        }
    }
```

- [ ] **Step 4: Run, expect pass**

```bash
pytest tests/test_errors.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flights_mcp/errors.py tests/test_errors.py
git commit -m "feat: tool error contract (ErrorCode enum, ToolError, error_response)"
```

---

## Task 4: Pydantic input model

**Files:**
- Create: `src/flights_mcp/models.py` (start)
- Test: `tests/test_models.py` (start)

- [ ] **Step 1: Write failing test `tests/test_models.py`**

```python
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from flights_mcp.models import CabinClass, SearchFlightsInput

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)
NEXT_WEEK = TODAY + timedelta(days=7)


def test_accepts_valid_round_trip():
    m = SearchFlightsInput(
        origin="HEL",
        destination="IAD",
        departure_date=TOMORROW.isoformat(),
        return_date=NEXT_WEEK.isoformat(),
        adults=2,
    )
    assert m.origin == "HEL"
    assert m.cabin_class is CabinClass.ECONOMY
    assert m.currency == "USD"
    assert m.max_results == 20


def test_rejects_lowercase_iata():
    with pytest.raises(ValidationError):
        SearchFlightsInput(origin="hel", destination="IAD", departure_date=TOMORROW.isoformat())


def test_rejects_wrong_length_iata():
    with pytest.raises(ValidationError):
        SearchFlightsInput(origin="HELS", destination="IAD", departure_date=TOMORROW.isoformat())


def test_rejects_digits_in_iata():
    with pytest.raises(ValidationError):
        SearchFlightsInput(origin="H1L", destination="IAD", departure_date=TOMORROW.isoformat())


def test_rejects_past_departure_date():
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        SearchFlightsInput(origin="HEL", destination="IAD", departure_date=yesterday)


def test_rejects_return_before_departure():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL",
            destination="IAD",
            departure_date=NEXT_WEEK.isoformat(),
            return_date=TOMORROW.isoformat(),
        )


def test_rejects_infants_exceeding_adults():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL",
            destination="IAD",
            departure_date=TOMORROW.isoformat(),
            adults=1,
            infants=2,
        )


def test_rejects_max_results_above_50():
    with pytest.raises(ValidationError):
        SearchFlightsInput(
            origin="HEL", destination="IAD", departure_date=TOMORROW.isoformat(), max_results=51
        )


def test_cabin_class_enum():
    m = SearchFlightsInput(
        origin="HEL",
        destination="IAD",
        departure_date=TOMORROW.isoformat(),
        cabin_class="BUSINESS",
    )
    assert m.cabin_class is CabinClass.BUSINESS
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_models.py -v
```

Expected: ImportError on `flights_mcp.models`.

- [ ] **Step 3: Write `src/flights_mcp/models.py`**

```python
"""Pydantic models for tool I/O and internal Amadeus parsing.

Input validation enforces IATA format, date sanity, passenger constraints, and
enum membership at the boundary — Claude's malformed input never reaches the
Amadeus client.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


class CabinClass(str, Enum):
    ECONOMY = "ECONOMY"
    PREMIUM_ECONOMY = "PREMIUM_ECONOMY"
    BUSINESS = "BUSINESS"
    FIRST = "FIRST"


IataCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$", strip_whitespace=False)]
IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
IsoCurrency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class SearchFlightsInput(BaseModel):
    origin: IataCode
    destination: IataCode
    departure_date: IsoDate
    return_date: IsoDate | None = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    infants: int = Field(default=0, ge=0, le=9)
    cabin_class: CabinClass = CabinClass.ECONOMY
    currency: IsoCurrency = "USD"
    non_stop_only: bool = False
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
```

- [ ] **Step 4: Run, expect pass**

```bash
pytest tests/test_models.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flights_mcp/models.py tests/test_models.py
git commit -m "feat: SearchFlightsInput with IATA/date/passenger validators"
```

---

## Task 5: Pydantic output models

**Files:**
- Modify: `src/flights_mcp/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Append failing tests to `tests/test_models.py`**

```python
from flights_mcp.models import FlightOffer, Itinerary, Segment, SearchFlightsResult


def _make_segment(**overrides):
    base = dict(
        airline="AY",
        flight_number="AY15",
        departure_airport="HEL",
        departure_time_local="2026-05-18T15:30:00",
        arrival_airport="JFK",
        arrival_time_local="2026-05-18T17:45:00",
        cabin="ECONOMY",
        booking_class="V",
    )
    base.update(overrides)
    return Segment(**base)


def test_segment_round_trips():
    s = _make_segment()
    assert s.airline == "AY"
    assert s.departure_time_local == "2026-05-18T15:30:00"


def test_itinerary_holds_segments():
    it = Itinerary(duration="PT10H30M", stops=1, segments=[_make_segment(), _make_segment(flight_number="AY99")])
    assert it.stops == 1
    assert len(it.segments) == 2


def test_flight_offer_round_trip_shape():
    it = Itinerary(duration="PT10H30M", stops=0, segments=[_make_segment()])
    offer = FlightOffer(
        offer_id="1",
        total_price=850.50,
        currency="USD",
        price_per_adult=850.50,
        airlines=["AY"],
        validating_airline="AY",
        outbound=it,
        inbound=None,
        seats_available=7,
        last_ticketing_date="2026-05-15",
        fare_basis="VLOWFI",
        baggage_allowance="1 checked bag",
    )
    assert offer.inbound is None
    assert offer.baggage_allowance == "1 checked bag"


def test_flight_offer_allows_null_optional_fields():
    it = Itinerary(duration="PT10H30M", stops=0, segments=[_make_segment()])
    offer = FlightOffer(
        offer_id="1",
        total_price=850.50,
        currency="USD",
        price_per_adult=850.50,
        airlines=["AY"],
        validating_airline="AY",
        outbound=it,
        inbound=None,
        seats_available=None,
        last_ticketing_date=None,
        fare_basis="VLOWFI",
        baggage_allowance=None,
    )
    assert offer.seats_available is None
    assert offer.last_ticketing_date is None
    assert offer.baggage_allowance is None


def test_search_flights_result_wraps_offers():
    it = Itinerary(duration="PT10H30M", stops=0, segments=[_make_segment()])
    offer = FlightOffer(
        offer_id="1", total_price=850.5, currency="USD", price_per_adult=850.5,
        airlines=["AY"], validating_airline="AY", outbound=it, inbound=None,
        seats_available=None, last_ticketing_date=None, fare_basis="V", baggage_allowance=None,
    )
    result = SearchFlightsResult(results=[offer])
    assert len(result.results) == 1
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_models.py -v
```

Expected: ImportError on `FlightOffer`/`Itinerary`/`Segment`/`SearchFlightsResult`.

- [ ] **Step 3: Append to `src/flights_mcp/models.py`**

```python
class Segment(BaseModel):
    airline: IataCode
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
    segments: list[Segment]


class FlightOffer(BaseModel):
    offer_id: str
    total_price: float
    currency: IsoCurrency
    price_per_adult: float
    airlines: list[IataCode]
    validating_airline: IataCode
    outbound: Itinerary
    inbound: Itinerary | None
    seats_available: int | None
    last_ticketing_date: str | None
    fare_basis: str
    baggage_allowance: str | None


class SearchFlightsResult(BaseModel):
    results: list[FlightOffer]
```

- [ ] **Step 4: Run, expect pass**

```bash
pytest tests/test_models.py -v
```

Expected: All previous + 5 new = 14 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flights_mcp/models.py tests/test_models.py
git commit -m "feat: FlightOffer/Itinerary/Segment output models"
```

---

## Task 6: Raw Amadeus models

**Files:**
- Modify: `src/flights_mcp/models.py`
- Modify: `tests/test_models.py`

The raw models match the verbose Amadeus response shape. They are used by `normalize.py` (Task 8) to translate into the clean output models. Capturing them as Pydantic gives us structural validation when fixtures drift.

- [ ] **Step 1: Append failing test**

```python
import json
from pathlib import Path

from flights_mcp.models import AmadeusSearchResponse


def test_amadeus_search_response_parses_minimal_payload():
    payload = {
        "meta": {"count": 1},
        "data": [{
            "type": "flight-offer",
            "id": "1",
            "source": "GDS",
            "lastTicketingDate": "2026-05-15",
            "numberOfBookableSeats": 7,
            "itineraries": [{
                "duration": "PT10H30M",
                "segments": [{
                    "id": "1",
                    "carrierCode": "AY",
                    "number": "15",
                    "departure": {"iataCode": "HEL", "at": "2026-05-18T15:30:00"},
                    "arrival": {"iataCode": "JFK", "at": "2026-05-18T17:45:00"},
                    "numberOfStops": 0,
                }],
            }],
            "price": {"currency": "USD", "total": "850.50", "base": "700.00"},
            "validatingAirlineCodes": ["AY"],
            "travelerPricings": [{
                "travelerId": "1",
                "fareOption": "STANDARD",
                "travelerType": "ADULT",
                "price": {"currency": "USD", "total": "850.50", "base": "700.00"},
                "fareDetailsBySegment": [{
                    "segmentId": "1",
                    "cabin": "ECONOMY",
                    "fareBasis": "VLOWFI",
                    "class": "V",
                }],
            }],
        }],
    }
    parsed = AmadeusSearchResponse.model_validate(payload)
    assert parsed.meta.count == 1
    assert parsed.data[0].id == "1"
    assert parsed.data[0].itineraries[0].segments[0].carrier_code == "AY"
    assert parsed.data[0].price.total == "850.50"
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_models.py::test_amadeus_search_response_parses_minimal_payload -v
```

Expected: ImportError on `AmadeusSearchResponse`.

- [ ] **Step 3: Append to `src/flights_mcp/models.py`**

```python
from pydantic import ConfigDict


class _AmadeusModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AmadeusEndpoint(_AmadeusModel):
    iata_code: str = Field(alias="iataCode")
    at: str  # local-airport time, no offset
    terminal: str | None = None


class AmadeusSegment(_AmadeusModel):
    id: str
    carrier_code: str = Field(alias="carrierCode")
    number: str
    departure: AmadeusEndpoint
    arrival: AmadeusEndpoint
    number_of_stops: int = Field(alias="numberOfStops", default=0)
    operating: dict | None = None


class AmadeusItinerary(_AmadeusModel):
    duration: str
    segments: list[AmadeusSegment]


class AmadeusPrice(_AmadeusModel):
    currency: str
    total: str
    base: str | None = None


class AmadeusFareDetail(_AmadeusModel):
    segment_id: str = Field(alias="segmentId")
    cabin: str
    fare_basis: str = Field(alias="fareBasis")
    class_: str = Field(alias="class")
    included_checked_bags: dict | None = Field(alias="includedCheckedBags", default=None)


class AmadeusTravelerPricing(_AmadeusModel):
    traveler_id: str = Field(alias="travelerId")
    traveler_type: str = Field(alias="travelerType")
    price: AmadeusPrice
    fare_details_by_segment: list[AmadeusFareDetail] = Field(alias="fareDetailsBySegment")


class AmadeusFlightOfferRaw(_AmadeusModel):
    id: str
    last_ticketing_date: str | None = Field(alias="lastTicketingDate", default=None)
    number_of_bookable_seats: int | None = Field(alias="numberOfBookableSeats", default=None)
    itineraries: list[AmadeusItinerary]
    price: AmadeusPrice
    validating_airline_codes: list[str] = Field(alias="validatingAirlineCodes")
    traveler_pricings: list[AmadeusTravelerPricing] = Field(alias="travelerPricings")


class AmadeusMeta(_AmadeusModel):
    count: int


class AmadeusSearchResponse(_AmadeusModel):
    meta: AmadeusMeta
    data: list[AmadeusFlightOfferRaw]
```

- [ ] **Step 4: Run, expect pass**

```bash
pytest tests/test_models.py -v
```

Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flights_mcp/models.py tests/test_models.py
git commit -m "feat: raw Amadeus response models for parsing"
```

---

## Task 7: Test fixtures

**Files:**
- Create: `tests/fixtures/synthetic_round_trip.json`
- Create: `tests/fixtures/empty_results.json`
- Create: `tests/fixtures/auth_failed.json`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write `tests/fixtures/synthetic_round_trip.json`**

This is the authoritative dev fixture until the user supplies the real Phase 0.1 response. Shape matches Amadeus Flight Offers Search v2.

```json
{
  "meta": {"count": 2},
  "data": [
    {
      "type": "flight-offer",
      "id": "1",
      "source": "GDS",
      "instantTicketingRequired": false,
      "nonHomogeneous": false,
      "oneWay": false,
      "lastTicketingDate": "2026-05-15",
      "numberOfBookableSeats": 7,
      "itineraries": [
        {
          "duration": "PT11H15M",
          "segments": [
            {
              "id": "1",
              "carrierCode": "AY",
              "number": "15",
              "aircraft": {"code": "359"},
              "departure": {"iataCode": "HEL", "terminal": "2", "at": "2026-05-18T15:30:00"},
              "arrival": {"iataCode": "JFK", "terminal": "1", "at": "2026-05-18T17:45:00"},
              "duration": "PT9H15M",
              "numberOfStops": 0,
              "blacklistedInEU": false
            },
            {
              "id": "2",
              "carrierCode": "AA",
              "number": "423",
              "aircraft": {"code": "738"},
              "departure": {"iataCode": "JFK", "terminal": "8", "at": "2026-05-18T20:00:00"},
              "arrival": {"iataCode": "IAD", "at": "2026-05-18T21:30:00"},
              "duration": "PT1H30M",
              "numberOfStops": 0,
              "operating": {"carrierCode": "AA"},
              "blacklistedInEU": false
            }
          ]
        },
        {
          "duration": "PT10H45M",
          "segments": [
            {
              "id": "3",
              "carrierCode": "AA",
              "number": "424",
              "aircraft": {"code": "738"},
              "departure": {"iataCode": "IAD", "at": "2026-05-29T07:00:00"},
              "arrival": {"iataCode": "JFK", "terminal": "8", "at": "2026-05-29T08:30:00"},
              "duration": "PT1H30M",
              "numberOfStops": 0,
              "blacklistedInEU": false
            },
            {
              "id": "4",
              "carrierCode": "AY",
              "number": "16",
              "aircraft": {"code": "359"},
              "departure": {"iataCode": "JFK", "terminal": "1", "at": "2026-05-29T11:00:00"},
              "arrival": {"iataCode": "HEL", "terminal": "2", "at": "2026-05-30T03:45:00"},
              "duration": "PT8H45M",
              "numberOfStops": 0,
              "blacklistedInEU": false
            }
          ]
        }
      ],
      "price": {
        "currency": "USD",
        "total": "742.18",
        "base": "523.00",
        "fees": [{"amount": "0.00", "type": "SUPPLIER"}],
        "grandTotal": "742.18"
      },
      "pricingOptions": {"fareType": ["PUBLISHED"], "includedCheckedBagsOnly": true},
      "validatingAirlineCodes": ["AY"],
      "travelerPricings": [
        {
          "travelerId": "1",
          "fareOption": "STANDARD",
          "travelerType": "ADULT",
          "price": {"currency": "USD", "total": "742.18", "base": "523.00"},
          "fareDetailsBySegment": [
            {"segmentId": "1", "cabin": "ECONOMY", "fareBasis": "VLOWFI", "class": "V", "includedCheckedBags": {"quantity": 1}},
            {"segmentId": "2", "cabin": "ECONOMY", "fareBasis": "VLOWFI", "class": "V", "includedCheckedBags": {"quantity": 1}},
            {"segmentId": "3", "cabin": "ECONOMY", "fareBasis": "VLOWFI", "class": "V", "includedCheckedBags": {"quantity": 1}},
            {"segmentId": "4", "cabin": "ECONOMY", "fareBasis": "VLOWFI", "class": "V", "includedCheckedBags": {"quantity": 1}}
          ]
        }
      ]
    },
    {
      "type": "flight-offer",
      "id": "2",
      "source": "GDS",
      "lastTicketingDate": "2026-05-15",
      "numberOfBookableSeats": 4,
      "itineraries": [
        {
          "duration": "PT10H30M",
          "segments": [
            {
              "id": "10",
              "carrierCode": "AY",
              "number": "17",
              "aircraft": {"code": "359"},
              "departure": {"iataCode": "HEL", "terminal": "2", "at": "2026-05-18T13:00:00"},
              "arrival": {"iataCode": "IAD", "at": "2026-05-18T17:30:00"},
              "duration": "PT10H30M",
              "numberOfStops": 0,
              "blacklistedInEU": false
            }
          ]
        },
        {
          "duration": "PT8H45M",
          "segments": [
            {
              "id": "11",
              "carrierCode": "AY",
              "number": "18",
              "aircraft": {"code": "359"},
              "departure": {"iataCode": "IAD", "at": "2026-05-29T19:00:00"},
              "arrival": {"iataCode": "HEL", "terminal": "2", "at": "2026-05-30T11:45:00"},
              "duration": "PT8H45M",
              "numberOfStops": 0,
              "blacklistedInEU": false
            }
          ]
        }
      ],
      "price": {"currency": "USD", "total": "920.00", "base": "680.00", "grandTotal": "920.00"},
      "pricingOptions": {"fareType": ["PUBLISHED"], "includedCheckedBagsOnly": false},
      "validatingAirlineCodes": ["AY"],
      "travelerPricings": [
        {
          "travelerId": "1",
          "fareOption": "STANDARD",
          "travelerType": "ADULT",
          "price": {"currency": "USD", "total": "920.00", "base": "680.00"},
          "fareDetailsBySegment": [
            {"segmentId": "10", "cabin": "ECONOMY", "fareBasis": "MLOWFI", "class": "M"},
            {"segmentId": "11", "cabin": "ECONOMY", "fareBasis": "MLOWFI", "class": "M"}
          ]
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Write `tests/fixtures/empty_results.json`**

```json
{"meta": {"count": 0}, "data": []}
```

- [ ] **Step 3: Write `tests/fixtures/auth_failed.json`**

Captures Amadeus's documented 401 body. Used in Task 11 to verify error mapping.

```json
{
  "errors": [
    {
      "code": 38187,
      "title": "Invalid HTTP header",
      "detail": "Missing or invalid bearer token",
      "status": 401
    }
  ]
}
```

- [ ] **Step 4: Write `tests/conftest.py`**

```python
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open() as f:
        return json.load(f)


@pytest.fixture
def synthetic_round_trip() -> dict:
    return _load("synthetic_round_trip.json")


@pytest.fixture
def empty_results() -> dict:
    return _load("empty_results.json")


@pytest.fixture
def auth_failed_body() -> dict:
    return _load("auth_failed.json")
```

- [ ] **Step 5: Smoke-test the fixtures parse against the raw models**

```bash
pytest -v --co tests/  # Check tests are collected
python -c "
import json
from flights_mcp.models import AmadeusSearchResponse
with open('tests/fixtures/synthetic_round_trip.json') as f:
    AmadeusSearchResponse.model_validate(json.load(f))
print('synthetic fixture parses')
with open('tests/fixtures/empty_results.json') as f:
    AmadeusSearchResponse.model_validate(json.load(f))
print('empty fixture parses')
"
```

Expected: both lines print.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures tests/conftest.py
git commit -m "test: synthetic Amadeus fixtures for fixture-driven development"
```

---

## Task 8: Response normalization

**Files:**
- Create: `src/flights_mcp/amadeus/normalize.py`
- Test: `tests/test_normalize.py`

- [ ] **Step 1: Write failing test `tests/test_normalize.py`**

```python
from flights_mcp.amadeus.normalize import normalize_offers
from flights_mcp.models import AmadeusSearchResponse


def test_normalize_round_trip_synthetic(synthetic_round_trip):
    raw = AmadeusSearchResponse.model_validate(synthetic_round_trip)
    offers = normalize_offers(raw)

    assert len(offers) == 2

    offer_one_stop = offers[0]
    assert offer_one_stop.offer_id == "1"
    assert offer_one_stop.total_price == 742.18
    assert offer_one_stop.currency == "USD"
    assert offer_one_stop.price_per_adult == 742.18
    assert offer_one_stop.validating_airline == "AY"
    assert set(offer_one_stop.airlines) == {"AY", "AA"}
    assert offer_one_stop.outbound.stops == 1
    assert len(offer_one_stop.outbound.segments) == 2
    assert offer_one_stop.inbound is not None
    assert offer_one_stop.inbound.stops == 1
    assert offer_one_stop.fare_basis == "VLOWFI"
    assert offer_one_stop.baggage_allowance == "1 checked bag"
    assert offer_one_stop.seats_available == 7
    assert offer_one_stop.last_ticketing_date == "2026-05-15"

    first_seg = offer_one_stop.outbound.segments[0]
    assert first_seg.airline == "AY"
    assert first_seg.flight_number == "AY15"
    assert first_seg.departure_airport == "HEL"
    assert first_seg.departure_time_local == "2026-05-18T15:30:00"
    assert first_seg.arrival_airport == "JFK"
    assert first_seg.cabin.value == "ECONOMY"
    assert first_seg.booking_class == "V"

    offer_non_stop = offers[1]
    assert offer_non_stop.outbound.stops == 0
    assert offer_non_stop.baggage_allowance is None  # no includedCheckedBags in fixture


def test_normalize_empty_results(empty_results):
    raw = AmadeusSearchResponse.model_validate(empty_results)
    assert normalize_offers(raw) == []
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_normalize.py -v
```

Expected: ImportError on `flights_mcp.amadeus.normalize`.

- [ ] **Step 3: Write `src/flights_mcp/amadeus/normalize.py`**

```python
"""Translate verbose Amadeus offers into the clean tool output shape."""
from __future__ import annotations

from collections import OrderedDict

from flights_mcp.models import (
    AmadeusFareDetail,
    AmadeusFlightOfferRaw,
    AmadeusItinerary,
    AmadeusSearchResponse,
    CabinClass,
    FlightOffer,
    Itinerary,
    Segment,
)


def _baggage_summary(detail: AmadeusFareDetail | None) -> str | None:
    if detail is None or detail.included_checked_bags is None:
        return None
    bag = detail.included_checked_bags
    qty = bag.get("quantity")
    if qty is not None:
        if qty == 0:
            return "no checked bag"
        return f"{qty} checked bag" if qty == 1 else f"{qty} checked bags"
    weight = bag.get("weight")
    unit = bag.get("weightUnit") or ""
    if weight is not None:
        return f"{weight}{unit} checked baggage".strip()
    return None


def _normalize_itinerary(it: AmadeusItinerary, fares_by_segment_id: dict[str, AmadeusFareDetail]) -> Itinerary:
    segments: list[Segment] = []
    for seg in it.segments:
        fare = fares_by_segment_id.get(seg.id)
        cabin = (fare.cabin if fare else "ECONOMY").upper()
        booking_class = fare.class_ if fare else ""
        segments.append(Segment(
            airline=seg.carrier_code,
            flight_number=f"{seg.carrier_code}{seg.number}",
            departure_airport=seg.departure.iata_code,
            departure_time_local=seg.departure.at,
            arrival_airport=seg.arrival.iata_code,
            arrival_time_local=seg.arrival.at,
            cabin=CabinClass(cabin),
            booking_class=booking_class,
        ))
    stops = max(0, len(it.segments) - 1)
    return Itinerary(duration=it.duration, stops=stops, segments=segments)


def _normalize_offer(raw: AmadeusFlightOfferRaw) -> FlightOffer:
    # Build a segmentId -> fareDetail map from the first traveler pricing.
    # Phase 1 keeps it simple: per-traveler fare details are assumed homogeneous.
    fares_by_segment_id: dict[str, AmadeusFareDetail] = {}
    if raw.traveler_pricings:
        for fd in raw.traveler_pricings[0].fare_details_by_segment:
            fares_by_segment_id[fd.segment_id] = fd

    outbound = _normalize_itinerary(raw.itineraries[0], fares_by_segment_id)
    inbound = (
        _normalize_itinerary(raw.itineraries[1], fares_by_segment_id)
        if len(raw.itineraries) > 1
        else None
    )

    # Operating carriers across all segments, preserving order, deduplicated.
    airlines = OrderedDict()
    for it in raw.itineraries:
        for seg in it.segments:
            airlines[seg.carrier_code] = None

    total_price = float(raw.price.total)
    price_per_adult = (
        float(raw.traveler_pricings[0].price.total)
        if raw.traveler_pricings
        else total_price
    )

    # Use the fare detail for the FIRST segment as the representative fare_basis
    # and baggage summary. Multi-leg itineraries occasionally vary; capturing
    # every variant is overkill for Phase 1.
    representative_fare = next(iter(fares_by_segment_id.values()), None)
    fare_basis = representative_fare.fare_basis if representative_fare else ""
    baggage = _baggage_summary(representative_fare)

    return FlightOffer(
        offer_id=raw.id,
        total_price=total_price,
        currency=raw.price.currency,
        price_per_adult=price_per_adult,
        airlines=list(airlines.keys()),
        validating_airline=raw.validating_airline_codes[0],
        outbound=outbound,
        inbound=inbound,
        seats_available=raw.number_of_bookable_seats,
        last_ticketing_date=raw.last_ticketing_date,
        fare_basis=fare_basis,
        baggage_allowance=baggage,
    )


def normalize_offers(response: AmadeusSearchResponse) -> list[FlightOffer]:
    return [_normalize_offer(raw) for raw in response.data]
```

- [ ] **Step 4: Run, expect pass**

```bash
pytest tests/test_normalize.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flights_mcp/amadeus/normalize.py tests/test_normalize.py
git commit -m "feat: normalize Amadeus offers into clean tool output"
```

---

## Task 9: TTL response cache

**Files:**
- Create: `src/flights_mcp/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write failing test `tests/test_cache.py`**

```python
import time

import pytest

from flights_mcp.cache import TTLCache, canonical_key


def test_canonical_key_lowercases_iata_and_sorts():
    k1 = canonical_key({"origin": "HEL", "destination": "iad", "adults": 1})
    k2 = canonical_key({"adults": 1, "destination": "IAD", "origin": "hel"})
    assert k1 == k2


def test_canonical_key_distinguishes_inputs():
    k1 = canonical_key({"origin": "HEL", "destination": "IAD", "departure_date": "2026-05-18"})
    k2 = canonical_key({"origin": "HEL", "destination": "IAD", "departure_date": "2026-05-19"})
    assert k1 != k2


def test_cache_returns_stored_value_within_ttl():
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", {"v": 1})
    assert cache.get("k") == {"v": 1}


def test_cache_returns_none_after_ttl(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("flights_mcp.cache.time.monotonic", lambda: now[0])
    cache = TTLCache(ttl_seconds=60)
    cache.set("k", {"v": 1})
    now[0] += 30
    assert cache.get("k") == {"v": 1}
    now[0] += 31
    assert cache.get("k") is None


def test_cache_returns_none_for_unknown_key():
    cache = TTLCache(ttl_seconds=60)
    assert cache.get("missing") is None
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_cache.py -v
```

Expected: ImportError on `flights_mcp.cache`.

- [ ] **Step 3: Write `src/flights_mcp/cache.py`**

```python
"""In-memory TTL cache keyed on canonical input hash.

Phase 1 only — no persistence, no eviction beyond TTL. Quota protection for
2,000 calls/month, not a production cache.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any


def canonical_key(params: dict[str, Any]) -> str:
    """Stable hash of input parameters.

    IATA fields (origin, destination) are lowercased so case variations collapse.
    All other keys are stringified as-is and sorted.
    """
    normalized: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str) and k in {"origin", "destination", "currency"}:
            normalized[k] = v.lower()
        else:
            normalized[k] = v
    payload = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class TTLCache:
    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic() + self._ttl, value)
```

- [ ] **Step 4: Run, expect pass**

```bash
pytest tests/test_cache.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flights_mcp/cache.py tests/test_cache.py
git commit -m "feat: TTL response cache with canonical key"
```

---

## Task 10: OAuth token cache

**Files:**
- Create: `src/flights_mcp/amadeus/token.py`
- Test: `tests/test_token.py`

- [ ] **Step 1: Write failing test `tests/test_token.py`**

```python
import asyncio

import httpx
import pytest

from flights_mcp.amadeus.token import TokenCache


def _make_transport(responses):
    """Return a MockTransport that yields the given responses in order."""
    iter_responses = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(iter_responses)

    return httpx.MockTransport(handler)


async def test_first_call_fetches_token():
    transport = _make_transport([
        httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800}),
    ])
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        token = await cache.get_token()
        assert token == "tok-1"


async def test_second_call_returns_cached_token():
    """Only one HTTP call should be made if the token is still valid."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        await cache.get_token()
        await cache.get_token()
        assert call_count["n"] == 1


async def test_concurrent_callers_share_one_refresh():
    """Lock contract: two concurrent get_token() calls produce exactly one HTTP call."""
    call_count = {"n": 0}
    gate = asyncio.Event()

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        await gate.wait()
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800})

    # MockTransport's handler can be a coroutine.
    transport = httpx.MockTransport(slow_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        # Kick off two concurrent fetches.
        task_a = asyncio.create_task(cache.get_token())
        task_b = asyncio.create_task(cache.get_token())
        # Let them block on the lock + the gated HTTP response.
        await asyncio.sleep(0.05)
        gate.set()
        a, b = await asyncio.gather(task_a, task_b)
        assert a == b == "tok-1"
        assert call_count["n"] == 1


async def test_401_raises_auth_failed():
    from flights_mcp.errors import ErrorCode, ToolError

    transport = _make_transport([
        httpx.Response(401, json={"errors": [{"code": 38187, "title": "bad creds"}]}),
    ])
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        with pytest.raises(ToolError) as exc:
            await cache.get_token()
        assert exc.value.code is ErrorCode.AUTH_FAILED


async def test_5xx_raises_upstream_error():
    from flights_mcp.errors import ErrorCode, ToolError

    transport = _make_transport([httpx.Response(503)])
    async with httpx.AsyncClient(transport=transport) as client:
        cache = TokenCache(client=client, base_url="https://test.api.amadeus.com",
                           client_id="id", client_secret="sec")
        with pytest.raises(ToolError) as exc:
            await cache.get_token()
        assert exc.value.code is ErrorCode.UPSTREAM_ERROR
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_token.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/flights_mcp/amadeus/token.py`**

```python
"""OAuth2 client-credentials token cache with async-safe refresh."""
from __future__ import annotations

import asyncio
import time

import httpx

from flights_mcp.errors import ErrorCode, ToolError

_REFRESH_BUFFER_SECONDS = 60


class TokenCache:
    def __init__(self, *, client: httpx.AsyncClient, base_url: str,
                 client_id: str, client_secret: str):
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        if self._is_valid():
            return self._token  # type: ignore[return-value]
        async with self._lock:
            # Re-check under lock — another task may have refreshed while we waited.
            if self._is_valid():
                return self._token  # type: ignore[return-value]
            await self._refresh()
            return self._token  # type: ignore[return-value]

    def _is_valid(self) -> bool:
        return self._token is not None and time.monotonic() < self._expires_at

    async def _refresh(self) -> None:
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as e:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, f"Token fetch network error: {e}") from e

        if response.status_code == 401:
            raise ToolError(ErrorCode.AUTH_FAILED, "Amadeus rejected credentials during token fetch.")
        if response.status_code >= 500:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, f"Amadeus token endpoint returned {response.status_code}.")
        if response.status_code != 200:
            raise ToolError(ErrorCode.UPSTREAM_ERROR,
                            f"Unexpected token endpoint status {response.status_code}: {response.text[:200]}")

        body = response.json()
        token = body.get("access_token")
        expires_in = body.get("expires_in")
        if not token or not isinstance(expires_in, int):
            raise ToolError(ErrorCode.UPSTREAM_ERROR, "Malformed token response.")
        self._token = token
        self._expires_at = time.monotonic() + max(0, expires_in - _REFRESH_BUFFER_SECONDS)
```

- [ ] **Step 4: Run, expect pass**

```bash
pytest tests/test_token.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flights_mcp/amadeus/token.py tests/test_token.py
git commit -m "feat: async-safe OAuth token cache with refresh lock"
```

---

## Task 11: Amadeus search client

**Files:**
- Create: `src/flights_mcp/amadeus/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write failing test `tests/test_client.py`**

```python
import json
from pathlib import Path

import httpx
import pytest

from flights_mcp.amadeus.client import AmadeusClient
from flights_mcp.errors import ErrorCode, ToolError
from flights_mcp.models import SearchFlightsInput

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_client(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return AmadeusClient(
        http=client,
        base_url="https://test.api.amadeus.com",
        client_id="id",
        client_secret="sec",
    )


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800})


async def test_search_returns_normalized_offers(synthetic_round_trip):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(200, json=synthetic_round_trip)

    client = _make_client(handler)
    inp = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", adults=1,
    )
    offers = await client.search(inp)
    assert len(offers) == 2
    assert offers[0].offer_id == "1"


async def test_search_passes_max_param_to_amadeus(synthetic_round_trip):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=synthetic_round_trip)

    client = _make_client(handler)
    inp = SearchFlightsInput(
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29",
        max_results=10,
    )
    await client.search(inp)
    assert captured["params"]["max"] == "10"


async def test_search_empty_data_raises_no_results(empty_results):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(200, json=empty_results)

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.NO_RESULTS


async def test_search_429_with_quota_message_maps_to_quota_exceeded():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(429, json={"errors": [{"code": 38194, "detail": "Monthly quota exceeded"}]})

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.QUOTA_EXCEEDED


async def test_search_429_transient_maps_to_rate_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(429, json={"errors": [{"detail": "Too many requests"}]})

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.RATE_LIMITED


async def test_search_5xx_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return _token_response()
        return httpx.Response(503)

    client = _make_client(handler)
    inp = SearchFlightsInput(origin="HEL", destination="IAD", departure_date="2026-05-18")
    with pytest.raises(ToolError) as exc:
        await client.search(inp)
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_client.py -v
```

Expected: ImportError on `flights_mcp.amadeus.client`.

- [ ] **Step 3: Write `src/flights_mcp/amadeus/client.py`**

```python
"""Amadeus Flight Offers Search client.

Substitutable HTTP transport via the injected `httpx.AsyncClient`. Token cache
is constructed internally because its lifecycle is identical to the client's.
"""
from __future__ import annotations

import httpx

from flights_mcp.amadeus.normalize import normalize_offers
from flights_mcp.amadeus.token import TokenCache
from flights_mcp.errors import ErrorCode, ToolError
from flights_mcp.models import AmadeusSearchResponse, FlightOffer, SearchFlightsInput

_BASE_URL_TEST = "https://test.api.amadeus.com"
_BASE_URL_PROD = "https://api.amadeus.com"


def base_url_for_env(env: str) -> str:
    if env == "production":
        return _BASE_URL_PROD
    if env == "test":
        return _BASE_URL_TEST
    raise ValueError(f"AMADEUS_ENV must be 'test' or 'production', got {env!r}")


class AmadeusClient:
    def __init__(self, *, http: httpx.AsyncClient, base_url: str,
                 client_id: str, client_secret: str):
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._tokens = TokenCache(
            client=http, base_url=base_url,
            client_id=client_id, client_secret=client_secret,
        )

    async def search(self, params: SearchFlightsInput) -> list[FlightOffer]:
        token = await self._tokens.get_token()
        query = self._build_query(params)
        try:
            response = await self._http.get(
                f"{self._base_url}/v2/shopping/flight-offers",
                params=query,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as e:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, f"Search network error: {e}") from e

        self._raise_for_status(response)
        parsed = AmadeusSearchResponse.model_validate(response.json())
        offers = normalize_offers(parsed)
        if not offers:
            raise ToolError(ErrorCode.NO_RESULTS, "Amadeus returned no offers.")
        return offers

    def _build_query(self, p: SearchFlightsInput) -> dict[str, str]:
        q: dict[str, str] = {
            "originLocationCode": p.origin,
            "destinationLocationCode": p.destination,
            "departureDate": p.departure_date,
            "adults": str(p.adults),
            "travelClass": p.cabin_class.value,
            "currencyCode": p.currency,
            "max": str(p.max_results),
        }
        if p.return_date:
            q["returnDate"] = p.return_date
        if p.children:
            q["children"] = str(p.children)
        if p.infants:
            q["infants"] = str(p.infants)
        if p.non_stop_only:
            q["nonStop"] = "true"
        return q

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        sc = response.status_code
        if sc == 200:
            return
        if sc == 401:
            raise ToolError(ErrorCode.AUTH_FAILED, "Amadeus rejected credentials.")
        if sc == 429:
            body_text = ""
            try:
                body = response.json()
                body_text = " ".join(
                    str(err.get("detail", "")) for err in body.get("errors", [])
                ).lower()
            except Exception:
                pass
            if "quota" in body_text:
                raise ToolError(ErrorCode.QUOTA_EXCEEDED,
                                "Amadeus monthly quota exhausted.", retryable=False)
            raise ToolError(ErrorCode.RATE_LIMITED,
                            "Amadeus rate limit hit.", retryable=True)
        if sc >= 500:
            raise ToolError(ErrorCode.UPSTREAM_ERROR,
                            f"Amadeus returned {sc}.", retryable=True)
        if sc == 400:
            raise ToolError(ErrorCode.UPSTREAM_ERROR,
                            f"Amadeus rejected request: {response.text[:200]}")
        raise ToolError(ErrorCode.UPSTREAM_ERROR,
                        f"Unexpected Amadeus status {sc}: {response.text[:200]}")
```

- [ ] **Step 4: Run, expect pass**

```bash
pytest tests/test_client.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flights_mcp/amadeus/client.py tests/test_client.py
git commit -m "feat: Amadeus search client with error mapping and substitutable transport"
```

---

## Task 12: `search_flights` tool function

**Files:**
- Create: `src/flights_mcp/tools/search_flights.py`
- Test: `tests/test_search_flights.py`

This task wires together: input validation → cache check → Amadeus call → cache fill → logging → error envelope. It does NOT register with FastMCP yet (that's Task 13). The function is a plain async callable returning the tool's success-or-error dict.

- [ ] **Step 1: Write failing test `tests/test_search_flights.py`**

```python
import httpx
import pytest

from flights_mcp.amadeus.client import AmadeusClient
from flights_mcp.cache import TTLCache
from flights_mcp.tools.search_flights import search_flights


def _make_amadeus(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return AmadeusClient(http=http, base_url="https://test.api.amadeus.com",
                         client_id="id", client_secret="sec")


def _token_or(json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 1800})
        return httpx.Response(200, json=json_response)
    return handler


async def test_returns_success_envelope(synthetic_round_trip):
    amadeus = _make_amadeus(_token_or(synthetic_round_trip))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        amadeus=amadeus, cache=cache, env="test",
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", adults=1,
    )
    assert "error" not in result
    assert len(result["results"]) == 2
    assert result["results"][0]["offer_id"] == "1"


async def test_invalid_input_returns_error_envelope(synthetic_round_trip):
    amadeus = _make_amadeus(_token_or(synthetic_round_trip))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        amadeus=amadeus, cache=cache, env="test",
        origin="hel",  # lowercase — invalid
        destination="IAD", departure_date="2026-05-18",
    )
    assert "error" in result
    assert result["error"]["code"] == "invalid_input"


async def test_no_results_message_varies_by_env(empty_results):
    amadeus = _make_amadeus(_token_or(empty_results))
    cache = TTLCache(ttl_seconds=300)

    test_result = await search_flights(
        amadeus=amadeus, cache=cache, env="test",
        origin="ZZZ", destination="QQQ", departure_date="2026-05-18",
    )
    assert test_result["error"]["code"] == "no_results"
    assert "test environment" in test_result["error"]["message"].lower()

    prod_result = await search_flights(
        amadeus=amadeus, cache=cache, env="production",
        origin="ZZZ", destination="QQQ", departure_date="2026-05-18",
    )
    assert prod_result["error"]["code"] == "no_results"
    assert "test environment" not in prod_result["error"]["message"].lower()


async def test_second_identical_call_is_cache_hit(synthetic_round_trip):
    call_count = {"search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 1800})
        call_count["search"] += 1
        return httpx.Response(200, json=synthetic_round_trip)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    amadeus = AmadeusClient(http=http, base_url="https://test.api.amadeus.com",
                            client_id="id", client_secret="sec")
    cache = TTLCache(ttl_seconds=300)

    kwargs = dict(
        amadeus=amadeus, cache=cache, env="test",
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", adults=1,
    )
    await search_flights(**kwargs)
    await search_flights(**kwargs)
    assert call_count["search"] == 1
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_search_flights.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `src/flights_mcp/tools/search_flights.py`**

```python
"""The `search_flights` tool function.

Wires validation, caching, Amadeus calls, error translation, and logging.
The MCP-facing description string lives here too — it is what Claude reads.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import ValidationError

from flights_mcp.amadeus.client import AmadeusClient
from flights_mcp.cache import TTLCache, canonical_key
from flights_mcp.errors import ErrorCode, ToolError, error_response
from flights_mcp.logging_config import log_event
from flights_mcp.models import SearchFlightsInput, SearchFlightsResult

TOOL_NAME = "search_flights"

TOOL_DESCRIPTION = """\
Search live flight offers for a given route and date range using the Amadeus GDS feed.

Returns a ranked list of flight options with prices, airlines, segment details, and fare information. Does not book flights, only searches.

Times in the response are local to the departure or arrival airport, with the airport's IATA code attached so the timezone can be derived. Do not perform timezone math on these times without first converting them.

Origin and destination can be either airport IATA codes (IAD, DCA, BWI) or city IATA codes (WAS, LON, NYC). City codes return offers across all airports in that city; Amadeus handles the multi-airport expansion server-side.

Results from identical searches are cached for up to 5 minutes. Prices may move within minutes, so a returned price may be up to 5 minutes old. If the user is about to act on a specific offer, re-run the search before committing to a number.

Several fields are nullable because Amadeus does not always populate them. Most importantly, a null `baggage_allowance` means "the airline did not return this information," not "no checked bag is included." Do not state that a fare excludes checked bags based on a null value. The same applies to `last_ticketing_date` and `seats_available`."""

_logger = logging.getLogger("flights_mcp")


def _no_results_message(env: str, origin: str, destination: str, departure_date: str) -> str:
    base = f"No flights found for {origin} to {destination} on {departure_date}."
    if env == "test":
        return (base + " Note: the Amadeus test environment only covers a subset of routes — "
                "if you suspect this route should have service, retry in production.")
    return base + " Try adjusting dates or airports."


async def search_flights(
    *,
    amadeus: AmadeusClient,
    cache: TTLCache,
    env: str,
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
    raw_input = dict(
        origin=origin, destination=destination, departure_date=departure_date,
        return_date=return_date, adults=adults, children=children, infants=infants,
        cabin_class=cabin_class, currency=currency, non_stop_only=non_stop_only,
        max_results=max_results,
    )
    # 1. Input validation.
    try:
        params = SearchFlightsInput.model_validate(raw_input)
    except ValidationError as e:
        first_error = e.errors()[0]
        field_path = ".".join(str(p) for p in first_error.get("loc", []))
        msg = f"Invalid input on '{field_path}': {first_error.get('msg')}"
        log_event(_logger, "tool.invalid_input", tool=TOOL_NAME, input=raw_input,
                  error=first_error.get("msg"))
        return error_response(ErrorCode.INVALID_INPUT, msg, retryable=False)

    # 2. Cache.
    key = canonical_key(params.model_dump())
    cached = cache.get(key)
    if cached is not None:
        log_event(_logger, "tool.cache_hit", tool=TOOL_NAME, input=params.model_dump())
        return cached

    # 3. Amadeus call.
    started = time.monotonic()
    try:
        offers = await amadeus.search(params)
    except ToolError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if e.code is ErrorCode.NO_RESULTS:
            msg = _no_results_message(env, params.origin, params.destination, params.departure_date)
            log_event(_logger, "tool.no_results", tool=TOOL_NAME,
                      input=params.model_dump(), elapsed_ms=elapsed_ms)
            return error_response(ErrorCode.NO_RESULTS, msg, retryable=False)
        log_event(_logger, "tool.amadeus_error", tool=TOOL_NAME,
                  code=e.code.value, elapsed_ms=elapsed_ms)
        return error_response(e.code, e.message, retryable=e.retryable)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    result = SearchFlightsResult(results=offers).model_dump(mode="json")
    cache.set(key, result)
    log_event(_logger, "tool.success", tool=TOOL_NAME, input=params.model_dump(),
              count=len(offers), elapsed_ms=elapsed_ms, cache_hit=False)
    return result
```

- [ ] **Step 4: Run, expect pass**

```bash
pytest tests/test_search_flights.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/flights_mcp/tools/search_flights.py tests/test_search_flights.py
git commit -m "feat: search_flights tool function with validation, cache, and error envelope"
```

---

## Task 13: FastMCP server registration

**Files:**
- Create: `src/flights_mcp/server.py`

This task has no isolated unit test — the FastMCP wiring is verified end-to-end via MCP Inspector in Task 15. Don't fabricate a fake test; this is genuinely integration territory.

- [ ] **Step 1: Write `src/flights_mcp/server.py`**

```python
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
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
AMADEUS_CLIENT_ID=fake AMADEUS_CLIENT_SECRET=fake AMADEUS_ENV=test \
  python -c "from flights_mcp.server import mcp; print(mcp.name)"
```

Expected: `flights-mcp`.

- [ ] **Step 3: Verify CLI entry point starts (then immediately killed)**

```bash
AMADEUS_CLIENT_ID=fake AMADEUS_CLIENT_SECRET=fake AMADEUS_ENV=test \
  timeout 2 python -m flights_mcp.server || echo "exit-as-expected"
```

Expected: process starts on stdio (will time out / hang waiting for stdin — that's correct for stdio MCP).

- [ ] **Step 4: Commit**

```bash
git add src/flights_mcp/server.py
git commit -m "feat: FastMCP server registration with stdio transport"
```

---

## Task 14: End-to-end integration test

**Files:**
- Modify: `tests/test_search_flights.py`

Adds one end-to-end test that exercises the full pipeline (input validation → cache → Amadeus → normalize → output dict) against a fixture, without going through FastMCP itself (which is verified manually via Inspector). This is the closest we get to "the tool works" without live credentials.

- [ ] **Step 1: Append failing test**

```python
async def test_full_round_trip_matches_documented_shape(synthetic_round_trip):
    amadeus = _make_amadeus(_token_or(synthetic_round_trip))
    cache = TTLCache(ttl_seconds=300)

    result = await search_flights(
        amadeus=amadeus, cache=cache, env="test",
        origin="HEL", destination="IAD",
        departure_date="2026-05-18", return_date="2026-05-29", adults=1,
    )
    assert "results" in result and isinstance(result["results"], list)
    offer = result["results"][0]
    expected_keys = {
        "offer_id", "total_price", "currency", "price_per_adult",
        "airlines", "validating_airline", "outbound", "inbound",
        "seats_available", "last_ticketing_date", "fare_basis", "baggage_allowance",
    }
    assert expected_keys.issubset(offer.keys())

    outbound = offer["outbound"]
    assert {"duration", "stops", "segments"}.issubset(outbound.keys())
    seg = outbound["segments"][0]
    assert {
        "airline", "flight_number", "departure_airport", "departure_time_local",
        "arrival_airport", "arrival_time_local", "cabin", "booking_class",
    }.issubset(seg.keys())
    # Time format contract: no offset, ISO datetime.
    assert "+" not in seg["departure_time_local"]
    assert "Z" not in seg["departure_time_local"]
    assert "T" in seg["departure_time_local"]
```

- [ ] **Step 2: Run, expect pass (this is a regression-shape test, not a feature test)**

```bash
pytest tests/test_search_flights.py -v
```

Expected: 5 passed.

- [ ] **Step 3: Run the full test suite**

```bash
pytest -v
```

Expected: All tests across `tests/` pass. Count should be: errors (3) + models (15) + normalize (2) + cache (5) + token (5) + client (6) + search_flights (5) = 41 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_search_flights.py
git commit -m "test: full pipeline shape regression test against synthetic fixture"
```

---

## Task 15: README and MCP Inspector verification

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# Flight Search MCP

Local-first MCP server wrapping the Amadeus Flight Offers Search API.

Exposes one tool, `search_flights`, that returns a ranked list of flight offers
for a route and date range. Phase 1 runs on stdio for development; Phase 2
will add HTTP transport for remote access.

## Quickstart

### 1. Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

Copy `.env.example` to `.env` and fill in your Amadeus Self-Service credentials.

```bash
cp .env.example .env
# Edit .env
```

| Variable | Required | Notes |
|---|---|---|
| `AMADEUS_CLIENT_ID` | yes | From the Amadeus Self-Service workspace. |
| `AMADEUS_CLIENT_SECRET` | yes | From the same workspace. |
| `AMADEUS_ENV` | yes | `test` or `production`. |
| `LOG_FILE_PATH` | no | Default `~/.flights-mcp/logs/flight-search.log`. Must be absolute. |
| `LOG_LEVEL` | no | Default `INFO`. |
| `CACHE_TTL_SECONDS` | no | Default `300`. |

### 3. Run tests

```bash
pytest
```

### 4. Start the server

```bash
set -a; source .env; set +a
python -m flights_mcp.server
```

The server speaks the MCP protocol over stdio.

### 5. Verify with MCP Inspector

```bash
npx @modelcontextprotocol/inspector python -m flights_mcp.server
```

Inspector will show the `search_flights` tool. Call it with:

```json
{
  "origin": "HEL",
  "destination": "IAD",
  "departure_date": "2026-05-18",
  "return_date": "2026-05-29",
  "adults": 1
}
```

Expect a `results` array. If you see `{"error": {"code": "no_results", ...}}`
in the test environment, the route may not be in Amadeus's cached subset — try
JFK, LAX, LHR, CDG, or any pair from amadeus4dev/data-collection.

## Architecture

See [SPEC.md](./SPEC.md) for the full functional spec.

```
search_flights() (MCP tool)
    │
    ├── SearchFlightsInput (Pydantic validation)
    ├── TTLCache (canonical-key, 5-min TTL)
    └── AmadeusClient
            ├── TokenCache (OAuth, async-lock-protected refresh)
            ├── GET /v2/shopping/flight-offers
            └── normalize_offers() → list[FlightOffer]
```

## Phase 1 scope

In:
- Single tool, `search_flights`
- stdio transport
- Test-env Amadeus integration
- Structured error contract
- Local-time-with-IATA timestamp contract
- Response caching, token caching with refresh lock

Out (deferred):
- HTTP transport, Cloudflare Tunnel (Phase 2)
- Auth (Phase 2)
- `airport_search`, `flight_price_confirm`, `fare_calendar` (Phase 3–4)
- Booking — Self-Service cannot issue tickets, ever

## Project layout

```
src/flights_mcp/
├── server.py              FastMCP entry point
├── logging_config.py      JSON-line file logger
├── errors.py              Error codes and envelope
├── models.py              Pydantic I/O models
├── cache.py               TTL response cache
├── tools/search_flights.py
└── amadeus/
    ├── client.py          HTTP layer + error mapping
    ├── normalize.py       Raw → clean
    └── token.py           OAuth + async refresh lock
```
```

- [ ] **Step 2: Manual MCP Inspector verification (requires real Amadeus credentials)**

This is the live, end-of-Phase-1 acceptance check. Requires Phase 0.1 complete.

```bash
set -a; source .env; set +a
npx @modelcontextprotocol/inspector python -m flights_mcp.server
```

In Inspector:
1. Confirm the `search_flights` tool appears with the full description.
2. Call it with `origin=HEL, destination=IAD, departure_date=2026-05-18, return_date=2026-05-29, adults=1`. Verify a `results` array is returned.
3. Call it again with the same parameters. Inspect `~/.flights-mcp/logs/flight-search.log` (or your `LOG_FILE_PATH`) — second invocation should log `tool.cache_hit`.
4. Call with `origin="hel"` (lowercase). Verify `{"error": {"code": "invalid_input", ...}}`.
5. Call with `origin="INV", destination="KUO", departure_date="2026-05-18"`. Verify `{"error": {"code": "no_results", ...}}` with the test-env message.

If any step fails, fix and re-run before declaring Phase 1 done.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README with quickstart, configuration, and Inspector verification"
```

---

## Phase 1 Done Checklist

Cross-check against SPEC.md acceptance criteria:

- [ ] (#1) `python -m flights_mcp.server` starts cleanly on stdio.
- [ ] (#2) MCP Inspector shows `search_flights` with full description.
- [ ] (#3) HEL→IAD call returns ≥1 offer. *(blocks on Phase 0.1; verifiable against real creds only)*
- [ ] (#4) Offer shape matches docs. *(verified by Task 14 against synthetic, and Task 15 step 2 against real data)*
- [ ] (#5) Malformed input → `invalid_input`. *(Tasks 4 + 12)*
- [ ] (#6) Unconnected pair → `no_results`. *(Tasks 11 + 12)*
- [ ] (#7) Identical calls within 5 min → cache hit. *(Tasks 9 + 12 unit; Task 15 manual)*
- [ ] (#8) Token refresh lock implemented (Task 10), marked "implemented, not yet verified" until Phase 2.
- [ ] (#9) README documents run + config + call. *(Task 15)*

---

## Risk-driven verification notes

- **HEL-IAD not cached.** The plan tolerates this: development against `synthetic_round_trip.json` proceeds regardless. Task 15 step 2 is where reality hits — if HEL-IAD is empty, swap to a known-good route from amadeus4dev/data-collection for the live verification, but keep the synthetic fixture as the test suite's ground truth.
- **Schema drift.** All raw-Amadeus tests parse fixtures with `extra="ignore"`. If Amadeus adds fields, tests stay green. If Amadeus removes a field we depend on, the normalize tests will fail with a clear ValidationError — that's the signal to refresh fixtures.
- **FastMCP version churn.** `pyproject.toml` pins `fastmcp>=2.5,<3.0`. If the Task 13 import fails on a newer FastMCP, narrow the pin to the exact minor version that worked.
- **Token refresh lock not exercised in Phase 1.** Acknowledged in checklist item #8. Re-verify in Phase 2 when HTTP transport allows concurrent requests.
