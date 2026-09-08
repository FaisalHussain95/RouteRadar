import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
from typer.testing import CliRunner

from flight_detective import db
from flight_detective.cli import app
from flight_detective.models import FareObservation, Itinerary, Route

TABLES = {"fare_observation", "calendar_tag", "news_event", "ingest_run"}


def make_observation(price: str = "612", **overrides: object) -> FareObservation:
    base: dict[str, object] = {
        "route": Route(origin="CDG", destination="ISB"),
        "departure_date": date(2026, 12, 20),
        "carrier": "PK",
        "flight_numbers": ["PK750"],
        "stops": 0,
        "layover_minutes": 0,
        "duration_minutes": 480,
        "cabin": "economy",
        "price_eur": Decimal(price),
        "provider": "fake",
        "raw_ref": "fixture:1",
    }
    base.update(overrides)
    itinerary = Itinerary.model_validate(base)
    return FareObservation.from_itinerary(itinerary, observed_on=date(2026, 9, 9))


def table_names(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()}


def test_db_init_cli_creates_file_and_tables(isolated_db_path: Path) -> None:
    assert not isolated_db_path.exists()
    result = CliRunner().invoke(app, ["db", "init"])
    assert result.exit_code == 0, result.output
    assert str(isolated_db_path) in result.output
    assert isolated_db_path.exists()
    with duckdb.connect(str(isolated_db_path)) as conn:
        assert table_names(conn) == TABLES


def test_db_init_twice_is_a_noop(isolated_db_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["db", "init"]).exit_code == 0
    # Put a row in, re-init, and make sure the tables were not recreated.
    with db.connect(isolated_db_path) as conn:
        db.upsert_fare_observations(conn, [make_observation()])
    second = runner.invoke(app, ["db", "init"])
    assert second.exit_code == 0, second.output
    with duckdb.connect(str(isolated_db_path)) as conn:
        assert table_names(conn) == TABLES
        assert conn.execute("SELECT count(*) FROM fare_observation").fetchone() == (1,)


def test_db_init_uses_explicit_path_option(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "other.duckdb"
    result = CliRunner().invoke(app, ["db", "init", "--path", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()


def test_upsert_same_observation_twice_keeps_one_row_with_second_price(
    isolated_db_path: Path,
) -> None:
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        db.upsert_fare_observations(conn, [make_observation("612")])
        db.upsert_fare_observations(conn, [make_observation("598", raw_ref="fixture:2")])
        rows = db.fetch_fare_observations(conn)
    assert len(rows) == 1
    assert rows[0].price_eur == Decimal("598")
    assert isinstance(rows[0].price_eur, Decimal)
    assert rows[0].raw_ref == "fixture:2"


def test_upsert_round_trips_every_column(isolated_db_path: Path) -> None:
    original = make_observation(
        "731.50",
        route=Route(origin="ORY", destination="SKT"),
        carrier="EK",
        flight_numbers=["EK76", "FZ333"],
        stops=1,
        layover_minutes=150,
        duration_minutes=840,
    )
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        db.upsert_fare_observations(conn, [original])
        (restored,) = db.fetch_fare_observations(conn)
    assert restored == original


def test_distinct_keys_are_distinct_rows(isolated_db_path: Path) -> None:
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        db.upsert_fare_observations(
            conn,
            [
                make_observation("612"),
                make_observation("640", carrier="QR", flight_numbers=["QR40", "QR620"]),
                make_observation("650", departure_date=date(2026, 12, 21)),
            ],
        )
        assert len(db.fetch_fare_observations(conn)) == 3


def test_upsert_empty_batch_is_fine(isolated_db_path: Path) -> None:
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        assert db.upsert_fare_observations(conn, []) == 0


def test_table_names_match_schema() -> None:
    # TABLE_NAMES is what `fd db init` echoes; keep it honest against the DDL it claims.
    declared = {
        m.group(1) for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)", "".join(db.SCHEMA))
    }
    assert declared == set(db.TABLE_NAMES) == TABLES


def test_price_column_is_decimal_in_duckdb(isolated_db_path: Path) -> None:
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        (col_type,) = conn.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'fare_observation' AND column_name = 'price_eur'"
        ).fetchone()  # type: ignore[misc]
    assert col_type.startswith("DECIMAL")
