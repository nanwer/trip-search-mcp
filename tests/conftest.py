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
