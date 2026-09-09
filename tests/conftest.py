"""Shared fixtures. The autouse env fixture is what guarantees "tests never use data/":
every test process sees FD_DB_PATH pointing into pytest's tmp_path, so even a test that
forgets to pass a path cannot open the real database."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from flight_detective import db
from flight_detective.calendar_engine.tags import tags_for_range
from flight_detective.models import FareObservation

GDELT_FIXTURES = Path(__file__).parent / "fixtures" / "gdeltcloud"
ANALYTICS_FARES = Path(__file__).parent / "fixtures" / "analytics" / "fares.json"


@pytest.fixture(autouse=True)
def isolated_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setenv("FD_DB_PATH", str(db_path))
    # Also keep a stray .env in the repo root from leaking into tests.
    monkeypatch.setenv("FD_DOTENV", str(tmp_path / "no-such.env"))
    return db_path


def load_gdelt_body(slug: str) -> dict[str, Any]:
    """One recorded GDELT Cloud response body, by query slug.

    Slugs are `carrier-<name>` for the six entity queries and the `SemanticQuery.slug` for
    the three search queries — the names `news/gdeltcloud.py`'s recorder writes."""
    body = json.loads((GDELT_FIXTURES / f"{slug}.json").read_text())
    assert isinstance(body, dict)
    return body


@pytest.fixture
def gdelt_body() -> Callable[[str], dict[str, Any]]:
    """`gdelt_body("airspace-disruption")` -> that fixture's JSON. Shared by the client and
    pipeline tests, which answer from the same nine recorded files."""
    return load_gdelt_body


def seed_analytics_db(conn: duckdb.DuckDBPyConnection) -> list[FareObservation]:
    """Fill an empty database from `fixtures/analytics/fares.json` and return what went in.

    The fixture is 21 hand-written `fare_observation` rows over two observation days,
    chosen so each F5 question has a hand-checkable answer: two carriers per departure
    date to make a lowest-fare curve, LHE/SKT pairs whose spread lands either side of the
    ground-transfer break-even, lead times both on and off the horizon grid, wedding-window
    and February/March departures, and five carriers with different durations.

    Calendar tags come from the real engine rather than the fixture, so the seasonal bands
    are the ones the pipeline would have written and a window moving in `calendar_engine`
    shows up here as a failing analytics test rather than as agreeing fiction."""
    db.init_schema(conn)
    observations = [
        FareObservation.model_validate(record) for record in json.loads(ANALYTICS_FARES.read_text())
    ]
    db.upsert_fare_observations(conn, observations)
    departures = [obs.departure_date for obs in observations]
    db.upsert_calendar_tags(conn, tags_for_range(min(departures), max(departures)))
    return observations


@pytest.fixture
def analytics_db_path(isolated_db_path: Path) -> Path:
    """The seeded database at FD_DB_PATH, i.e. the one `fd report` opens with no --path."""
    with db.connect(isolated_db_path) as conn:
        seed_analytics_db(conn)
    return isolated_db_path


@pytest.fixture
def analytics_conn(analytics_db_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A connection to the seeded database, for calling the query functions directly."""
    with db.connect(analytics_db_path) as conn:
        yield conn
