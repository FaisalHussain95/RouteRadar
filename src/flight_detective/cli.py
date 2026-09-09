"""Command-line entry point. Every subcommand added by a story is registered here."""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from flight_detective import __version__, db
from flight_detective.calendar_engine.tags import tags_for_range
from flight_detective.config import load_settings
from flight_detective.ingest import DEFAULT_HORIZONS, run_ingest
from flight_detective.providers.base import FareProvider, ProviderError
from flight_detective.providers.fake import FakeFareProvider
from flight_detective.providers.serpapi import SerpApiFareProvider

PARIS = ZoneInfo("Europe/Paris")

# `fd ingest` exit codes. 1 means nothing was gathered and 3 means some cells failed and
# the rest were stored; deploy/run-pipeline.sh tolerates 3 and only 3, because the PRD
# budgets < 2 % missing cells and a day with a few holes is still worth exporting.
EXIT_INGEST_FAILED = 1
EXIT_INGEST_PARTIAL = 3

# A year covers the longest ingest horizon (180 days) and the dashboard's 11-month series
# with room to spare, and re-tagging a year is milliseconds.
DEFAULT_TAG_SPAN = timedelta(days=365)

# `--provider` names. The constructor is called only when the command runs, so a provider
# that needs a key fails then, with its own message, rather than at import time.
PROVIDERS: dict[str, Callable[[], FareProvider]] = {
    "fake": FakeFareProvider,
    "serpapi": lambda: SerpApiFareProvider.from_settings(load_settings()),
}

DbPathOption = Annotated[
    Path | None,
    typer.Option(
        "--path", help="Database file; defaults to FD_DB_PATH or data/flight_detective.duckdb."
    ),
]

app = typer.Typer(help="Flight Detective: fare tracking and explanation, CDG/ORY to ISB/LHE/SKT.")
db_app = typer.Typer(help="Database maintenance.")
app.add_typer(db_app, name="db")


@app.callback()
def main() -> None:
    """Root callback: keeps subcommands as subcommands even while there is only one."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


def _db_path(path: Path | None) -> Path:
    return path if path is not None else load_settings().db_path


@db_app.command("init")
def db_init(path: DbPathOption = None) -> None:
    """Create the database file and its tables. Idempotent: re-running changes nothing."""
    db_path = _db_path(path)
    with db.connect(db_path) as conn:
        db.init_schema(conn)
    typer.echo(f"initialised {db_path} ({', '.join(db.TABLE_NAMES)})")


@app.command("tag-dates")
def tag_dates(
    from_: Annotated[
        datetime | None,
        typer.Option(
            "--from", formats=["%Y-%m-%d"], help="First date to tag; defaults to today (Paris)."
        ),
    ] = None,
    to: Annotated[
        datetime | None,
        typer.Option(
            "--to",
            formats=["%Y-%m-%d"],
            help="Last date to tag, inclusive; defaults to --from + 365 days.",
        ),
    ] = None,
    path: DbPathOption = None,
) -> None:
    """Recompute `calendar_tag` for a date range. Idempotent: the range ends up holding
    exactly the tags the calendar engine produces today, and nothing outside it moves."""
    # Typer parses dates as datetimes; only the day matters here.
    start = from_.date() if from_ is not None else datetime.now(PARIS).date()
    end = to.date() if to is not None else start + DEFAULT_TAG_SPAN
    if end < start:
        raise typer.BadParameter(f"--to {end} is before --from {start}", param_hint="--to")
    try:
        tags = list(tags_for_range(start, end))
    except OverflowError as exc:
        # hijri-converter's Umm al-Qura tables stop in 2077 and it raises rather than guess.
        raise typer.BadParameter(f"the Hijri tables end in 2077: {exc}", param_hint="--to") from exc
    with db.connect(_db_path(path)) as conn:
        db.init_schema(conn)
        written = db.replace_calendar_tags(conn, start, end, tags)
    dates = len({t.date for t in tags})
    typer.echo(f"tagged {start}..{end}: {dates} dates, {written} rows")


def _parse_horizons(value: str) -> list[int]:
    """`14,30,60` -> [14, 30, 60]; every entry a distinct positive day count."""
    horizons: list[int] = []
    for part in value.split(","):
        try:
            horizon = int(part.strip())
        except ValueError:
            raise typer.BadParameter(f"{part!r} is not a whole number") from None
        if horizon <= 0:
            raise typer.BadParameter(f"{horizon} is not a positive number of days")
        if horizon in horizons:
            raise typer.BadParameter(f"{horizon} is listed twice")
        horizons.append(horizon)
    return horizons


def _make_provider(name: str) -> FareProvider:
    factory = PROVIDERS.get(name)
    if factory is None:
        raise typer.BadParameter(f"{name!r}; choose from {', '.join(PROVIDERS)}")
    return factory()


@app.command()
def ingest(
    provider: Annotated[
        str, typer.Option("--provider", help=f"Fare provider: {', '.join(PROVIDERS)}.")
    ] = "fake",
    horizons: Annotated[
        str,
        typer.Option(
            "--horizons",
            help="Comma-separated days before departure to query.",
            show_default=True,
        ),
    ] = ",".join(str(h) for h in DEFAULT_HORIZONS),
    observed_on: Annotated[
        datetime | None,
        typer.Option(
            "--observed-on",
            formats=["%Y-%m-%d"],
            help="Observation date the horizons count from; defaults to today (Paris). "
            "Set it to replay a day against fixtures.",
        ),
    ] = None,
    path: DbPathOption = None,
) -> None:
    """Query every route at every horizon, store the in-scope fares, and log the run.
    Idempotent for a given observation date. The queries that answered are stored
    whatever the others did; the exit code says how much of the grid that was: 3 if some
    queries failed, 1 if all of them did."""
    try:
        horizon_list = _parse_horizons(horizons)
    except typer.BadParameter as exc:
        raise typer.BadParameter(str(exc), param_hint="--horizons") from None
    try:
        fare_provider = _make_provider(provider)
    except typer.BadParameter as exc:
        raise typer.BadParameter(str(exc), param_hint="--provider") from None
    except ProviderError as exc:
        # A provider that cannot even be constructed (no API key) is a configuration
        # error, not a failed query: one line on stderr, no traceback, nothing written.
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    day = observed_on.date() if observed_on is not None else datetime.now(PARIS).date()
    with db.connect(_db_path(path)) as conn:
        db.init_schema(conn)
        run = run_ingest(conn, fare_provider, observed_on=day, horizons=horizon_list)
    typer.echo(
        f"ingested {day} via {run.provider}: {run.queries} queries, "
        f"{run.rows_kept} kept, {run.rows_dropped} dropped"
    )
    if run.error is not None:
        failed = run.error.count("\n") + 1
        typer.echo(f"{failed} of {run.queries} queries failed:", err=True)
        for line in run.error.splitlines():
            typer.echo(f"  {line}", err=True)
        partial = failed < run.queries
        raise typer.Exit(code=EXIT_INGEST_PARTIAL if partial else EXIT_INGEST_FAILED)


if __name__ == "__main__":
    app()
