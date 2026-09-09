"""The daily fare ingest (PRD F1): every route × horizon through one FareProvider, scope
filtering, upsert, and one `ingest_run` row recording what happened.

Queries are collected first and written afterwards in a single transaction, so a crash
mid-run leaves no half-day behind and a re-run for the same `observed_on` lands on the
same primary keys (architecture § Idempotent writes).

A `ProviderError` on one query does not abort the run. The PRD's success metric is
"< 2 % missing (route, horizon) cells", which only makes sense if one bad cell costs one
cell rather than the day: the other queries are stored, every failure message goes into
`ingest_run.error`, and the CLI exits non-zero so the failure is still visible. Anything
that is not a `ProviderError` is a bug and propagates before anything is written.

Filtering runs *before* the upsert, and the kept rows are deduplicated by primary key
keeping the lowest price, because `fare_observation` has no cabin column: a business
fare on the same flight numbers as an economy one would otherwise collide with it and
whichever the provider listed last would win.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import get_args
from zoneinfo import ZoneInfo

import duckdb

from flight_detective import db
from flight_detective.models import (
    Destination,
    FareObservation,
    IngestRun,
    Itinerary,
    Origin,
    Route,
)
from flight_detective.providers.base import FareProvider, ProviderError
from flight_detective.providers.filters import apply_scope

PARIS = ZoneInfo("Europe/Paris")

# PRD F1: departure dates queried each day, in days before departure.
DEFAULT_HORIZONS: tuple[int, ...] = (14, 30, 60, 90, 120, 180)

# Every origin/destination pair the PRD scopes, in a fixed order so `ingest_run.error`
# and the raw_refs read the same from one run to the next.
ROUTES: tuple[Route, ...] = tuple(
    Route(origin=origin, destination=destination)
    for origin in get_args(Origin)
    for destination in get_args(Destination)
)


def _lowest_per_key(observations: list[FareObservation]) -> list[FareObservation]:
    best: dict[tuple[date, date, str, str, str, str], FareObservation] = {}
    for obs in observations:
        current = best.get(obs.key)
        if current is None or obs.price_eur < current.price_eur:
            best[obs.key] = obs
    return list(best.values())


def run_ingest(
    conn: duckdb.DuckDBPyConnection,
    provider: FareProvider,
    *,
    observed_on: date,
    horizons: Sequence[int],
) -> IngestRun:
    """Query every route at every horizon, store what is in scope, and return the
    `IngestRun` that was written. `run.error` is None when every query answered."""
    run_at = datetime.now(PARIS)
    kept: list[Itinerary] = []
    dropped = 0
    errors: list[str] = []
    queries = 0
    for horizon in horizons:
        departure = observed_on + timedelta(days=horizon)
        for route in ROUTES:
            queries += 1
            try:
                found = provider.search(route, departure)
            except ProviderError as exc:
                # One failure per line in `ingest_run.error`; a pydantic ValidationError
                # inside a FixtureInvalid is multi-line, so whitespace is flattened.
                message = " ".join(str(exc).split())
                errors.append(
                    f"{route.origin}->{route.destination} {departure.isoformat()}: {message}"
                )
                continue
            in_scope, out_of_scope = apply_scope(found)
            kept.extend(in_scope)
            dropped += out_of_scope

    observations = _lowest_per_key(
        [FareObservation.from_itinerary(it, observed_on=observed_on) for it in kept]
    )
    run = IngestRun(
        run_at=run_at,
        provider=provider.name,
        queries=queries,
        rows_kept=len(observations),
        rows_dropped=dropped,
        error="\n".join(errors) or None,
    )
    conn.begin()
    try:
        db.upsert_fare_observations(conn, observations)
        db.insert_ingest_run(conn, run)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return run
