# -----------------------------------------------------------------------------#
# SHARED FIXTURES
# -----------------------------------------------------------------------------#
"""Fixtures for the whole suite. The dataset is synthetic — see test_fixture.py."""

import copy
import json
from pathlib import Path

import pytest

from sources.protocols_io.client import _call_api

FIXTURE = Path(__file__).parent / "fixtures" / "protocols_by_id.json"
WALK_FIXTURE = Path(__file__).parent / "fixtures" / "filemanager_walk.json"

# Named for the structure each one carries, not for the protocol it came from.
ARCHETYPES = (
    "baseline",
    "signed_urls",
    "empty_versions_null_steps",
    "empty_versions_with_steps",
    "reserved_doi",
    "version_class_differs",
    "dotted_steps",
)


# -----------------------------------------------------------------------------#
# RATE LIMIT
# -----------------------------------------------------------------------------#
def _rate_limiter():
    """The RateLimitDecorator guarding _call_api, reached through its closure."""

    def find(function, depth: int = 0):
        for cell in getattr(function, "__closure__", None) or []:
            candidate = cell.cell_contents
            if type(candidate).__name__ == "RateLimitDecorator":
                return candidate
            if callable(candidate) and depth < 4:
                found = find(candidate, depth + 1)
                if found is not None:
                    return found
        return None

    return find(_call_api)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Give every test a full call budget.

    _call_api allows 100 calls/minute and `sleep_and_retry` blocks when that runs
    out — a suite that walks a few hundred mocked pages would otherwise stall for
    a real minute. Resetting is not a behaviour change: the throttle is tested
    directly in test_request.py rather than incidentally everywhere else.
    """
    limiter = _rate_limiter()
    if limiter is not None:
        limiter.num_calls = 0
        limiter.last_reset = limiter.clock()
    yield


@pytest.fixture(scope="session")
def by_id_records() -> dict[str, dict]:
    """The synthetic by-ID dataset, read once per session."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def record(by_id_records):
    """A deep copy of one archetype, so a mutating test cannot leak into another."""

    def _record(archetype: str) -> dict:
        return copy.deepcopy(by_id_records[archetype])

    return _record


@pytest.fixture(scope="session")
def walk_records() -> dict:
    """The synthetic File Manager tree: top folders, folder pages, items."""
    return json.loads(WALK_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def db_path(tmp_path) -> str:
    """A throwaway sqlite file path unique to each test."""
    return str(tmp_path / "chronos_test.db")


@pytest.fixture
def list_items():
    """The list-endpoint shape: ids only, which is all `fields=id` returns.

    Deliberately not the by-ID fixture — conflating the two shapes is what let the
    old suite drift away from the contract it was meant to be testing.
    """

    def _items(count: int, start: int = 1) -> list[dict]:
        return [{"id": start + n} for n in range(count)]

    return _items
