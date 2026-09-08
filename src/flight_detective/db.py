"""DuckDB access: connection, schema, and the writes the pipeline needs.

Schema "migrations" are a list of idempotent `CREATE ... IF NOT EXISTS` statements run in
order on every connect-and-init. There is no version table yet: with one process and one
file, adding a column is a new statement in the list (`ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`) rather than a numbered migration, and that stays true until a change needs data
rewritten.

`flight_numbers` is stored '+'-joined rather than as a DuckDB LIST because it is part of
the primary key, and DuckDB cannot index a list column.
"""

from collections.abc import Iterable
from pathlib import Path

import duckdb

from flight_detective.models import FareObservation

SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS fare_observation (
        observed_on      DATE          NOT NULL,
        departure_date   DATE          NOT NULL,
        origin           VARCHAR       NOT NULL,
        destination      VARCHAR       NOT NULL,
        carrier          VARCHAR       NOT NULL,
        flight_numbers   VARCHAR       NOT NULL,
        stops            INTEGER       NOT NULL,
        layover_minutes  INTEGER       NOT NULL,
        duration_minutes INTEGER       NOT NULL,
        price_eur        DECIMAL(10,2) NOT NULL,
        provider         VARCHAR       NOT NULL,
        raw_ref          VARCHAR       NOT NULL,
        PRIMARY KEY (observed_on, departure_date, origin, destination, carrier, flight_numbers)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS calendar_tag (
        date            DATE         NOT NULL,
        tag             VARCHAR      NOT NULL,
        multiplier_low  DECIMAL(4,2) NOT NULL,
        multiplier_high DECIMAL(4,2) NOT NULL,
        PRIMARY KEY (date, tag)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS news_event (
        event_date  DATE    NOT NULL,
        category    VARCHAR NOT NULL,
        severity    TINYINT NOT NULL CHECK (severity BETWEEN 1 AND 3),
        headline    VARCHAR NOT NULL,
        source_url  VARCHAR NOT NULL,
        dedupe_key  VARCHAR NOT NULL PRIMARY KEY,
        impact_note VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingest_run (
        run_at       TIMESTAMPTZ NOT NULL,
        provider     VARCHAR     NOT NULL,
        queries      INTEGER     NOT NULL,
        rows_kept    INTEGER     NOT NULL,
        rows_dropped INTEGER     NOT NULL,
        error        VARCHAR,
        PRIMARY KEY (run_at, provider)
    )
    """,
)

TABLE_NAMES: tuple[str, ...] = ("fare_observation", "calendar_tag", "news_event", "ingest_run")

_FARE_COLUMNS = (
    "observed_on",
    "departure_date",
    "origin",
    "destination",
    "carrier",
    "flight_numbers",
    "stops",
    "layover_minutes",
    "duration_minutes",
    "price_eur",
    "provider",
    "raw_ref",
)

# Everything not in the primary key is refreshed on conflict, so a re-run for the same day
# replaces yesterday's view of that itinerary instead of duplicating it.
_FARE_UPSERT = f"""
    INSERT INTO fare_observation ({", ".join(_FARE_COLUMNS)})
    VALUES ({", ".join("?" for _ in _FARE_COLUMNS)})
    ON CONFLICT DO UPDATE SET
        stops = excluded.stops,
        layover_minutes = excluded.layover_minutes,
        duration_minutes = excluded.duration_minutes,
        price_eur = excluded.price_eur,
        provider = excluded.provider,
        raw_ref = excluded.raw_ref
"""


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the database file, making the parent directory first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create every table that does not exist yet. Safe to run on every start."""
    for statement in SCHEMA:
        conn.execute(statement)


def upsert_fare_observations(
    conn: duckdb.DuckDBPyConnection, observations: Iterable[FareObservation]
) -> int:
    """Insert-or-update observations by primary key. Returns the number of rows written."""
    rows = [
        (
            obs.observed_on,
            obs.departure_date,
            obs.origin,
            obs.destination,
            obs.carrier,
            obs.flight_numbers_key,
            obs.stops,
            obs.layover_minutes,
            obs.duration_minutes,
            obs.price_eur,
            obs.provider,
            obs.raw_ref,
        )
        for obs in observations
    ]
    if not rows:
        return 0
    conn.executemany(_FARE_UPSERT, rows)
    return len(rows)


def fetch_fare_observations(conn: duckdb.DuckDBPyConnection) -> list[FareObservation]:
    """Every stored observation, in primary-key order. Mostly for tests and `fd report`."""
    result = conn.execute(
        f"SELECT {', '.join(_FARE_COLUMNS)} FROM fare_observation "
        "ORDER BY observed_on, departure_date, origin, destination, carrier, flight_numbers"
    ).fetchall()
    return [
        FareObservation.model_validate(
            {
                **dict(zip(_FARE_COLUMNS, row, strict=True)),
                "flight_numbers": row[_FARE_COLUMNS.index("flight_numbers")].split("+"),
            }
        )
        for row in result
    ]
