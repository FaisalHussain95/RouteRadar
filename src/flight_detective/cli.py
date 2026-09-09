"""Command-line entry point. Every subcommand added by a story is registered here."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from flight_detective import __version__, db
from flight_detective.calendar_engine.tags import tags_for_range
from flight_detective.config import load_settings

PARIS = ZoneInfo("Europe/Paris")

# A year covers the longest ingest horizon (180 days) and the dashboard's 11-month series
# with room to spare, and re-tagging a year is milliseconds.
DEFAULT_TAG_SPAN = timedelta(days=365)

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


if __name__ == "__main__":
    app()
