"""S07: `fd ingest` with the fake provider.

The shipped fixtures only cover departures on 2026-12-20, so the CLI tests pin the
observation date to 2026-12-06 with a 14-day horizon, which lands every route's query on
that date. Tests that need several horizons build their own fixtures in `tmp_path` and
call `run_ingest` directly with a fake pointed at them.
"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from flight_detective import cli, db
from flight_detective.cli import app
from flight_detective.ingest import DEFAULT_HORIZONS, ROUTES, run_ingest
from flight_detective.models import Itinerary, Route
from flight_detective.providers.base import ProviderError
from flight_detective.providers.fake import FakeFareProvider, fixture_path

runner = CliRunner()

OBSERVED = date(2026, 12, 6)
FIXTURE_DEPARTURE = date(2026, 12, 20)  # OBSERVED + 14 days: the one date the fixtures cover
BASE_ARGS = ["ingest", "--provider", "fake", "--observed-on", OBSERVED.isoformat()]

# What the shipped 2026-12-20 fixtures hold, counted by hand against the scope rules:
# CDG-ISB 4 kept / 3 dropped, CDG-LHE 4 / 3, CDG-SKT 3 / 3, the three ORY files empty.
FIXTURE_KEPT = 11
FIXTURE_DROPPED = 9


def fare_count(path: Path) -> int:
    with duckdb.connect(str(path)) as conn:
        row = conn.execute("SELECT count(*) FROM fare_observation").fetchone()
    assert row is not None
    return int(row[0])


def ingest_runs(path: Path) -> list[tuple[datetime, str, int, int, int, str | None]]:
    with duckdb.connect(str(path)) as conn:
        return conn.execute(
            "SELECT run_at, provider, queries, rows_kept, rows_dropped, error "
            "FROM ingest_run ORDER BY run_at"
        ).fetchall()


def economy(carrier: str, flights: list[str], price: int, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "carrier": carrier,
        "flight_numbers": flights,
        "stops": len(flights) - 1,
        "layover_minutes": 0 if len(flights) == 1 else 120,
        "duration_minutes": 480 if len(flights) == 1 else 700,
        "cabin": "economy",
        "price_eur": price,
        "raw_ref": f"{carrier.lower()}-{price}",
    }
    record.update(overrides)
    return record


def write_fixture(
    directory: Path, route: Route, departure: date, itineraries: list[dict[str, object]]
) -> None:
    body = {
        "route": {"origin": route.origin, "destination": route.destination},
        "departure_date": departure.isoformat(),
        "itineraries": itineraries,
    }
    fixture_path(directory, route, departure).write_text(json.dumps(body))


@pytest.fixture
def grid_fixtures(tmp_path: Path) -> Path:
    """A full route × horizon grid for OBSERVED with horizons 14 and 30: every CDG query
    answers one PIA direct plus one out-of-scope 2-stop, every ORY query is empty."""
    directory = tmp_path / "fares"
    directory.mkdir()
    for horizon in (14, 30):
        departure = OBSERVED + timedelta(days=horizon)
        for route in ROUTES:
            if route.origin == "ORY":
                write_fixture(directory, route, departure, [])
                continue
            write_fixture(
                directory,
                route,
                departure,
                [
                    economy("PK", ["PK750"], 500 + horizon),
                    economy("SV", ["SV128", "SV1024", "SV730"], 450, stops=2),
                ],
            )
    return directory


# --- Acceptance: running twice for the same day leaves row counts unchanged ------------


def test_ingest_twice_for_the_same_day_leaves_fare_rows_unchanged(isolated_db_path: Path) -> None:
    first = runner.invoke(app, BASE_ARGS + ["--horizons", "14"])
    assert first.exit_code == 0, first.output
    assert fare_count(isolated_db_path) == FIXTURE_KEPT
    second = runner.invoke(app, BASE_ARGS + ["--horizons", "14"])
    assert second.exit_code == 0, second.output
    assert fare_count(isolated_db_path) == FIXTURE_KEPT
    # `ingest_run` is a log, one row per run, so it is the one table that does grow.
    assert len(ingest_runs(isolated_db_path)) == 2


def test_ingest_twice_via_run_ingest_across_horizons(
    isolated_db_path: Path, grid_fixtures: Path
) -> None:
    provider = FakeFareProvider(grid_fixtures)
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        run_ingest(conn, provider, observed_on=OBSERVED, horizons=[14, 30])
        after_first = db.fetch_fare_observations(conn)
        run_ingest(conn, provider, observed_on=OBSERVED, horizons=[14, 30])
        after_second = db.fetch_fare_observations(conn)
    assert after_first == after_second
    # 3 CDG routes × 2 horizons, one PIA direct each.
    assert len(after_first) == 6
    assert {o.departure_date for o in after_first} == {
        OBSERVED + timedelta(days=14),
        OBSERVED + timedelta(days=30),
    }


def test_rerun_refreshes_price_for_the_same_itinerary(
    isolated_db_path: Path, grid_fixtures: Path
) -> None:
    provider = FakeFareProvider(grid_fixtures)
    cdg_isb = Route(origin="CDG", destination="ISB")
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        run_ingest(conn, provider, observed_on=OBSERVED, horizons=[14])
        write_fixture(grid_fixtures, cdg_isb, FIXTURE_DEPARTURE, [economy("PK", ["PK750"], 999)])
        run_ingest(conn, provider, observed_on=OBSERVED, horizons=[14])
        rows = [o for o in db.fetch_fare_observations(conn) if o.destination == "ISB"]
    assert [o.price_eur for o in rows] == [Decimal("999")]


# --- Acceptance: rows_dropped reflects the out-of-scope fixtures -----------------------


def test_ingest_run_counts_kept_and_dropped(isolated_db_path: Path) -> None:
    result = runner.invoke(app, BASE_ARGS + ["--horizons", "14"])
    assert result.exit_code == 0, result.output
    runs = ingest_runs(isolated_db_path)
    assert len(runs) == 1
    run_at, provider, queries, kept, dropped, error = runs[0]
    assert provider == "fake"
    assert queries == len(ROUTES) == 6
    assert (kept, dropped) == (FIXTURE_KEPT, FIXTURE_DROPPED)
    assert error is None
    assert run_at.tzinfo is not None
    assert abs(datetime.now(UTC) - run_at) < timedelta(minutes=5)
    assert f"{FIXTURE_KEPT} kept" in result.output
    assert f"{FIXTURE_DROPPED} dropped" in result.output


def test_only_in_scope_itineraries_are_stored(isolated_db_path: Path) -> None:
    assert runner.invoke(app, BASE_ARGS + ["--horizons", "14"]).exit_code == 0
    with db.connect(isolated_db_path) as conn:
        rows = db.fetch_fare_observations(conn)
    assert all(o.observed_on == OBSERVED for o in rows)
    assert all(o.departure_date == FIXTURE_DEPARTURE for o in rows)
    assert all(
        o.stops <= 1 and o.layover_minutes <= 420 and o.duration_minutes <= 900 for o in rows
    )
    assert all(o.provider == "fake" for o in rows)
    assert not any(o.origin == "ORY" for o in rows)
    # The business-class Qatar record shares QR40+QR614 with the economy one. Filtering
    # happens before the upsert, so the economy price is what survives.
    qr_isb = [o for o in rows if o.destination == "ISB" and o.carrier == "QR"]
    assert [o.price_eur for o in qr_isb] == [Decimal("612")]


def test_run_ingest_returns_the_same_counts_it_stores(isolated_db_path: Path) -> None:
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        run = run_ingest(conn, FakeFareProvider(), observed_on=OBSERVED, horizons=[14])
        stored = db.fetch_ingest_runs(conn)
    assert stored == [run]
    assert (run.queries, run.rows_kept, run.rows_dropped, run.error) == (6, 11, 9, None)


# --- Acceptance: a provider exception is recorded and the command exits non-zero -------


def test_provider_error_is_recorded_and_exit_is_non_zero(isolated_db_path: Path) -> None:
    # Horizon 30 lands on 2027-01-05, which has no fixture: six FixtureMissing errors.
    result = runner.invoke(app, BASE_ARGS + ["--horizons", "14,30"])
    assert result.exit_code == 1, result.output
    runs = ingest_runs(isolated_db_path)
    assert len(runs) == 1
    _, _, queries, kept, dropped, error = runs[0]
    assert queries == 12
    assert error is not None
    assert "no fare fixture" in error
    assert "2027-01-05" in error
    assert error.count("no fare fixture") == 6
    # The queries that did answer are still stored: one bad cell must not lose the day.
    assert (kept, dropped) == (FIXTURE_KEPT, FIXTURE_DROPPED)
    assert fare_count(isolated_db_path) == FIXTURE_KEPT
    assert "2027-01-05" in result.output


class ExplodingProvider:
    name = "exploding"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def search(self, route: Route, departure_date: date) -> list[Itinerary]:
        raise self.exc


def test_every_query_failing_still_writes_one_run_row(isolated_db_path: Path) -> None:
    provider = ExplodingProvider(ProviderError("quota exhausted"))
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        run = run_ingest(conn, provider, observed_on=OBSERVED, horizons=[14])
        stored = db.fetch_ingest_runs(conn)
    assert stored == [run]
    assert run.provider == "exploding"
    assert (run.rows_kept, run.rows_dropped) == (0, 0)
    assert run.error is not None
    assert run.error.count("quota exhausted") == 6


def test_non_provider_exceptions_propagate(isolated_db_path: Path) -> None:
    provider = ExplodingProvider(RuntimeError("a bug, not a provider failure"))
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        with pytest.raises(RuntimeError):
            run_ingest(conn, provider, observed_on=OBSERVED, horizons=[14])
        assert db.fetch_ingest_runs(conn) == []


# --- Command surface ------------------------------------------------------------------


def test_default_horizons_are_the_prd_set() -> None:
    assert DEFAULT_HORIZONS == (14, 30, 60, 90, 120, 180)


def test_routes_cover_every_origin_destination_pair() -> None:
    assert {(r.origin, r.destination) for r in ROUTES} == {
        (o, d) for o in ("CDG", "ORY") for d in ("ISB", "LHE", "SKT")
    }


def test_ingest_works_on_a_fresh_database(isolated_db_path: Path) -> None:
    assert not isolated_db_path.exists()
    result = runner.invoke(app, BASE_ARGS + ["--horizons", "14"])
    assert result.exit_code == 0, result.output
    assert fare_count(isolated_db_path) == FIXTURE_KEPT


def test_ingest_honours_path_option(isolated_db_path: Path, tmp_path: Path) -> None:
    other = tmp_path / "other.duckdb"
    result = runner.invoke(app, BASE_ARGS + ["--horizons", "14", "--path", str(other)])
    assert result.exit_code == 0, result.output
    assert fare_count(other) == FIXTURE_KEPT
    assert not isolated_db_path.exists()


def test_ingest_defaults_to_today_and_all_horizons(
    isolated_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The command reads the real clock, so the fake is pointed at an empty fixtures
    # directory: every one of the 6 routes × 6 default horizons then fails the same
    # way whatever today is, and the defaults are visible in the counts.
    empty = tmp_path / "no-fixtures"
    empty.mkdir()
    monkeypatch.setitem(cli.PROVIDERS, "fake", lambda: FakeFareProvider(empty))
    result = runner.invoke(app, ["ingest", "--provider", "fake"])
    assert result.exit_code == 1
    runs = ingest_runs(isolated_db_path)
    assert len(runs) == 1
    assert runs[0][2] == 36
    assert runs[0][5] is not None and runs[0][5].count("no fare fixture") == 36


@pytest.mark.parametrize("horizons", ["0", "-14", "14,abc", "14,14", ""])
def test_ingest_rejects_bad_horizons(isolated_db_path: Path, horizons: str) -> None:
    result = runner.invoke(app, BASE_ARGS + ["--horizons", horizons])
    assert result.exit_code == 2, result.output
    assert "--horizons" in result.output
    assert not isolated_db_path.exists()


def test_ingest_rejects_unknown_provider(isolated_db_path: Path) -> None:
    result = runner.invoke(app, ["ingest", "--provider", "nope"])
    assert result.exit_code == 2
    assert "--provider" in result.output
    assert "fake" in result.output


def test_ingest_rejects_bad_observed_on(isolated_db_path: Path) -> None:
    result = runner.invoke(app, ["ingest", "--provider", "fake", "--observed-on", "yesterday"])
    assert result.exit_code == 2


def test_duplicate_keys_within_one_query_keep_the_lowest_price(
    isolated_db_path: Path, tmp_path: Path
) -> None:
    # Two economy fares on the same flights (different fare classes) collide on the
    # primary key. The PRD wants the lowest practical fare, so that is the one kept.
    directory = tmp_path / "fares"
    directory.mkdir()
    route = Route(origin="CDG", destination="ISB")
    write_fixture(
        directory,
        route,
        FIXTURE_DEPARTURE,
        [
            economy("PK", ["PK750"], 640),
            economy("PK", ["PK750"], 540),
            economy("PK", ["PK750"], 590),
        ],
    )
    for other in ROUTES:
        if other != route:
            write_fixture(directory, other, FIXTURE_DEPARTURE, [])
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        run = run_ingest(conn, FakeFareProvider(directory), observed_on=OBSERVED, horizons=[14])
        rows = db.fetch_fare_observations(conn)
    assert [o.price_eur for o in rows] == [Decimal("540")]
    assert (run.rows_kept, run.rows_dropped) == (1, 0)


def test_multi_line_provider_messages_are_flattened_to_one_line_each(
    isolated_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A FixtureInvalid wraps a pydantic ValidationError, which spans several lines.
    # `ingest_run.error` is one failure per line and the CLI counts failures from it.
    directory = tmp_path / "fares"
    directory.mkdir()
    for route in ROUTES:
        write_fixture(directory, route, FIXTURE_DEPARTURE, [])
    broken = Route(origin="CDG", destination="ISB")
    fixture_path(directory, broken, FIXTURE_DEPARTURE).write_text(
        json.dumps({"route": {"origin": "CDG", "destination": "ISB"}, "itineraries": "nope"})
    )
    monkeypatch.setitem(cli.PROVIDERS, "fake", lambda: FakeFareProvider(directory))
    result = runner.invoke(app, BASE_ARGS + ["--horizons", "14"])
    assert result.exit_code == 1, result.output
    runs = ingest_runs(isolated_db_path)
    error = runs[0][5]
    assert error is not None
    assert "\n" not in error
    assert error.startswith("CDG->ISB 2026-12-20: fare fixture ")
    assert "not valid" in error
    assert "1 of 6 queries failed" in result.output
