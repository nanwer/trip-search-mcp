import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def serpapi_one_way() -> dict:
    return _load("serpapi_one_way_success.json")


@pytest.fixture
def serpapi_round_trip_outbound() -> dict:
    return _load("serpapi_round_trip_outbound.json")


@pytest.fixture
def serpapi_round_trip_return() -> dict:
    return _load("serpapi_round_trip_return.json")


@pytest.fixture
def serpapi_empty_results() -> dict:
    return _load("serpapi_empty_results.json")


@pytest.fixture
def serpapi_auth_failed_body() -> dict:
    return _load("serpapi_auth_failed.json")
