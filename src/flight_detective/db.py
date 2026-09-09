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
from datetime import date
from pathlib import Path

import duckdb

from flight_detective.models import CalendarTag, FareObservation

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

_TAG_COLUMNS = ("date", "tag", "multiplier_low", "multiplier_high")

_TAG_UPSERT = f"""
    INSERT INTO calendar_tag ({", ".join(_TAG_COLUMNS)})
    VALUES ({", ".join("?" for _ in _TAG_COLUMNS)})
    ON CONFLICT DO UPDATE SET
        multiplier_low = excluded.multiplier_low,
        multiplier_high = excluded.multiplier_high
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


def upsert_calendar_tags(conn: duckdb.DuckDBPyConnection, tags: Iterable[CalendarTag]) -> int:
    """Insert-or-update tags by `(date, tag)`. Returns the number of rows written."""
    rows = [(t.date, t.tag, t.multiplier_low, t.multiplier_high) for t in tags]
    if not rows:
        return 0
    conn.executemany(_TAG_UPSERT, rows)
    return len(rows)


def replace_calendar_tags(
    conn: duckdb.DuckDBPyConnection, start: date, end: date, tags: Iterable[CalendarTag]
) -> int:
    """Make `calendar_tag` for `start..end` (inclusive) exactly `tags`, in one transaction.

    Upserting alone would leave a row behind for a window that was renamed or retired,
    and `calendar_tag` is documented as *recomputed*, so the range is cleared first.
    Rows outside the range are untouched. Returns the number of rows written."""
    conn.begin()
    try:
        conn.execute("DELETE FROM calendar_tag WHERE date BETWEEN ? AND ?", [start, end])
        written = upsert_calendar_tags(conn, tags)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return written


def fetch_calendar_tags(
    conn: duckdb.DuckDBPyConnection, start: date, end: date
) -> list[CalendarTag]:
    """Tags for `start..end` inclusive, in date then tag order."""
    result = conn.execute(
        f"SELECT {', '.join(_TAG_COLUMNS)} FROM calendar_tag "
        "WHERE date BETWEEN ? AND ? ORDER BY date, tag",
        [start, end],
    ).fetchall()
    return [CalendarTag.model_validate(dict(zip(_TAG_COLUMNS, row, strict=True))) for row in result]
