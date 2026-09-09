"""Command-line entry point. Every subcommand added by a story is registered here."""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Annotated, cast, get_args
from zoneinfo import ZoneInfo

import duckdb
import typer

from flight_detective import __version__, db
from flight_detective.analytics import queries
from flight_detective.calendar_engine.tags import tags_for_range
from flight_detective.config import load_settings
from flight_detective.export import schema as schema_export
from flight_detective.export import site as site_export
from flight_detective.ingest import DEFAULT_HORIZONS, run_ingest
from flight_detective.models import Destination, Origin
from flight_detective.news.gdelt import MAX_DAYS, GdeltClient
from flight_detective.news.ingest import DEFAULT_DAYS, run_news_ingest
from flight_detective.providers.base import FareProvider, ProviderError
from flight_detective.providers.fake import FakeFareProvider
from flight_detective.providers.serpapi import SerpApiFareProvider

PARIS = ZoneInfo("Europe/Paris")

# `fd ingest` exit codes. 1 means nothing was gathered and 3 means some cells failed and
# the rest were stored; deploy/run-pipeline.sh tolerates 3 and only 3, because the PRD
# budgets < 2 % missing cells and a day with a few holes is still worth exporting.
EXIT_INGEST_FAILED = 1
EXIT_INGEST_PARTIAL = 3

# `fd news-ingest` fails only when *every* taxonomy query died. News is context around the
# fares rather than the dataset, and deploy/run-pipeline.sh chains it with `&&`: one flaky
# free-API query must not cost the day's export, while GDELT being wholly unreachable is
# worth stopping for, since nothing new would be exported anyway.
EXIT_NEWS_FAILED = 1

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

# The scope airports, straight off the Literal types, so a new one is added in one place.
ORIGINS: tuple[str, ...] = get_args(Origin)
DESTINATIONS: tuple[str, ...] = get_args(Destination)

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


@app.command("news-ingest")
def news_ingest(
    days: Annotated[
        int,
        typer.Option("--days", help=f"How many days of GDELT coverage to pull (1-{MAX_DAYS})."),
    ] = DEFAULT_DAYS,
    path: DbPathOption = None,
) -> None:
    """Pull disruption news through the GDELT taxonomy, collapse duplicate coverage of one
    incident into one row, and store it. Idempotent: rows land on their dedupe key."""
    if not 1 <= days <= MAX_DAYS:
        raise typer.BadParameter(
            f"GDELT's DOC 2.0 archive covers the last {MAX_DAYS} days", param_hint="--days"
        )
    with db.connect(_db_path(path)) as conn:
        db.init_schema(conn)
        result = run_news_ingest(conn, GdeltClient(), days=days)
    run = result.run
    typer.echo(
        f"news for the last {days} days: {run.queries} queries, "
        f"{run.rows_kept} events, {run.rows_dropped} duplicates collapsed"
    )
    if run.error is not None:
        failed = run.error.count("\n") + 1
        typer.echo(f"{failed} of {run.queries} taxonomy queries failed:", err=True)
        for line in run.error.splitlines():
            typer.echo(f"  {line}", err=True)
        if failed == run.queries:
            raise typer.Exit(code=EXIT_NEWS_FAILED)


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


class Question(StrEnum):
    """The five PRD F5 questions, as `fd report` names them."""

    LOWEST_FARE = "lowest-fare"
    ARBITRAGE = "arbitrage"
    LEAD_TIME = "lead-time"
    WEDDING_PREMIUM = "wedding-premium"
    EFFICIENCY = "efficiency"


# Which scope options each question actually reads. One it does not read is rejected
# rather than ignored: `report arbitrage --destination LHE` would otherwise print a table
# with SKT and ISB columns and an unfiltered verdict, i.e. answer a different question
# than the one asked. Arbitrage compares destinations and takes whichever carrier is
# cheapest into each; the efficiency index groups by carrier, so filtering to one is a
# table of one row the caller can read off the full one. `--break-even` defaults to None
# rather than to `DEFAULT_BREAK_EVEN_EUR` precisely so it can be checked here too: with a
# real default there is no telling "asked for" from "left alone".
QUESTION_OPTIONS: dict[Question, frozenset[str]] = {
    Question.LOWEST_FARE: frozenset({"--origin", "--destination", "--carrier"}),
    Question.ARBITRAGE: frozenset({"--origin", "--break-even"}),
    Question.LEAD_TIME: frozenset({"--origin", "--destination", "--carrier"}),
    Question.WEDDING_PREMIUM: frozenset({"--origin", "--destination", "--carrier"}),
    Question.EFFICIENCY: frozenset({"--origin", "--destination"}),
}


def _euros(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    """A fixed-width table on stdout. Hand-rolled rather than pulled from rich, so the
    output is byte-stable and a test can assert on it."""
    if not rows:
        typer.echo("no rows")
        return
    widths = [max(len(header), *(len(row[i]) for row in rows)) for i, header in enumerate(headers)]

    def line(cells: Sequence[str]) -> str:
        # First column left, the rest right: everything after the label is a number.
        return "  ".join(
            cell.ljust(width) if i == 0 else cell.rjust(width)
            for i, (cell, width) in enumerate(zip(cells, widths, strict=True))
        )

    typer.echo(line(headers))
    typer.echo("  ".join("-" * width for width in widths))
    for row in rows:
        typer.echo(line(row))


def _choice(value: str | None, allowed: tuple[str, ...], hint: str) -> str | None:
    if value is not None and value not in allowed:
        raise typer.BadParameter(f"{value!r}; choose from {', '.join(allowed)}", param_hint=hint)
    return value


def _break_even(value: str) -> Decimal:
    """Parsed from a string, never a float: `Money` rejects floats for a reason.

    `Decimal` happily builds `NaN` and `Infinity`, and a `NaN` only fails much later, on
    the comparison inside `airport_arbitrage`, as a traceback rather than a usage error.
    A negative transfer cost parses too and makes every spread a win, so the verdict
    column stops meaning anything. Both are rejected here, where the message can name the
    option."""
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise typer.BadParameter(f"{value!r} is not an amount", param_hint="--break-even") from None
    if not amount.is_finite():
        raise typer.BadParameter(f"{value!r} is not a finite amount", param_hint="--break-even")
    if amount < 0:
        raise typer.BadParameter(
            "a ground transfer cannot cost less than 0", param_hint="--break-even"
        )
    return amount


def _report_lowest_fare(conn: duckdb.DuckDBPyConnection, **scope: object) -> None:
    points = queries.lowest_fare_curve(
        conn,
        origin=cast(Origin | None, scope["origin"]),
        destination=cast(Destination | None, scope["destination"]),
        carrier=cast(str | None, scope["carrier"]),
    )
    _print_table(
        ("carrier", "month", "lowest_eur", "observations"),
        [
            (
                p.carrier,
                p.departure_month.strftime("%Y-%m"),
                _euros(p.price_eur),
                str(p.observations),
            )
            for p in points
        ],
    )


def _report_arbitrage(
    conn: duckdb.DuckDBPyConnection, break_even_eur: Decimal, **scope: object
) -> None:
    rows = queries.airport_arbitrage(
        conn, break_even_eur=break_even_eur, origin=cast(Origin | None, scope["origin"])
    )
    _print_table(
        ("departure", "lhe_eur", "skt_eur", "isb_eur", "spread_eur", "break_even_eur", "verdict"),
        [
            (
                r.departure_date.isoformat(),
                _euros(r.lhe_eur),
                _euros(r.skt_eur),
                _euros(r.isb_eur),
                _euros(r.spread_eur),
                _euros(r.break_even_eur),
                r.verdict,
            )
            for r in rows
        ],
    )


def _report_lead_time(conn: duckdb.DuckDBPyConnection, **scope: object) -> None:
    points = queries.lead_time_curve(
        conn,
        origin=cast(Origin | None, scope["origin"]),
        destination=cast(Destination | None, scope["destination"]),
        carrier=cast(str | None, scope["carrier"]),
    )
    _print_table(
        ("band", "horizon_days", "min_eur", "avg_eur", "observations"),
        [
            (
                p.band,
                str(p.horizon_days),
                _euros(p.min_price_eur),
                _euros(p.avg_price_eur),
                str(p.observations),
            )
            for p in points
        ],
    )


def _report_wedding_premium(conn: duckdb.DuckDBPyConnection, **scope: object) -> None:
    premium = queries.wedding_premium(
        conn,
        origin=cast(Origin | None, scope["origin"]),
        destination=cast(Destination | None, scope["destination"]),
        carrier=cast(str | None, scope["carrier"]),
    )
    if premium is None:
        # One empty side means the premium is unknown, not zero. Which side is empty is a
        # question for `report lowest-fare` with the same filters, not for this table.
        typer.echo("no rows")
        return
    _print_table(
        (
            "tag",
            "wedding_eur",
            "baseline_eur",
            "multiplier",
            "premium_pct",
            "wedding_n",
            "baseline_n",
        ),
        [
            (
                premium.tag,
                _euros(premium.wedding_avg_eur),
                _euros(premium.baseline_avg_eur),
                _euros(premium.multiplier),
                _euros(premium.premium_pct),
                str(premium.wedding_observations),
                str(premium.baseline_observations),
            )
        ],
    )


def _report_efficiency(conn: duckdb.DuckDBPyConnection, **scope: object) -> None:
    rows = queries.carrier_efficiency(
        conn,
        origin=cast(Origin | None, scope["origin"]),
        destination=cast(Destination | None, scope["destination"]),
    )
    _print_table(
        ("carrier", "avg_price_eur", "avg_hours", "eur_per_hour", "observations"),
        [
            (
                r.carrier,
                _euros(r.avg_price_eur),
                _euros(r.avg_duration_hours),
                _euros(r.eur_per_hour),
                str(r.observations),
            )
            for r in rows
        ],
    )


@app.command()
def report(
    question: Annotated[Question, typer.Argument(help="Which PRD F5 question to answer.")],
    origin: Annotated[
        str | None, typer.Option("--origin", help=f"Limit to one origin: {', '.join(ORIGINS)}.")
    ] = None,
    destination: Annotated[
        str | None,
        typer.Option("--destination", help=f"Limit to one destination: {', '.join(DESTINATIONS)}."),
    ] = None,
    carrier: Annotated[
        str | None, typer.Option("--carrier", help="Limit to one marketing carrier (IATA code).")
    ] = None,
    break_even: Annotated[
        str | None,
        typer.Option(
            "--break-even",
            help="Ground transfer cost in EUR, for arbitrage "
            f"(default {queries.DEFAULT_BREAK_EVEN_EUR}).",
        ),
    ] = None,
    path: DbPathOption = None,
) -> None:
    """Answer one of the five F5 questions from the stored observations, as a table.

    An option a question does not use is a usage error, not a no-op: see QUESTION_OPTIONS.
    An unanswerable question prints `no rows` rather than an empty table or a zero, and
    every question tolerates an empty database."""
    for hint, value in (
        ("--origin", origin),
        ("--destination", destination),
        ("--carrier", carrier),
        ("--break-even", break_even),
    ):
        if value is not None and hint not in QUESTION_OPTIONS[question]:
            raise typer.BadParameter(f"{hint} does not apply to {question}", param_hint=hint)
    scope = {
        "origin": _choice(origin, ORIGINS, "--origin"),
        "destination": _choice(destination, DESTINATIONS, "--destination"),
        "carrier": carrier,
    }
    break_even_eur = (
        queries.DEFAULT_BREAK_EVEN_EUR if break_even is None else _break_even(break_even)
    )
    with db.connect(_db_path(path)) as conn:
        # A report on a database that predates a table should say "no rows", not crash.
        db.init_schema(conn)
        if question is Question.LOWEST_FARE:
            _report_lowest_fare(conn, **scope)
        elif question is Question.ARBITRAGE:
            _report_arbitrage(conn, break_even_eur, **scope)
        elif question is Question.LEAD_TIME:
            _report_lead_time(conn, **scope)
        elif question is Question.WEDDING_PREMIUM:
            _report_wedding_premium(conn, **scope)
        else:
            _report_efficiency(conn, **scope)


@app.command("export-site")
def export_site(
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Where to write the dashboard JSON; defaults to FD_SITE_EXPORT_PATH or "
            "data/site/dashboard.json.",
        ),
    ] = None,
    path: DbPathOption = None,
) -> None:
    """Write `dashboard.json`, everything the static site reads, atomically.

    Runs on any database, including one that has never been ingested into: the site has to
    build before the first pipeline run, and an empty file with populated calendar bands is
    what lets it. `generated_at` is now (Paris)."""
    destination = out if out is not None else load_settings().site_export_path
    with db.connect(_db_path(path)) as conn:
        # Same reason `fd report` does it: a database that predates a table is not an error.
        db.init_schema(conn)
        data = site_export.build_dashboard(conn, generated_at=datetime.now(PARIS))
    written = site_export.write_dashboard(data, destination)
    typer.echo(
        f"wrote {written}: {len(data.series)} series, {len(data.bands)} bands, "
        f"{len(data.events)} events (observed_on {data.observed_on or 'never'})"
    )


@app.command("export-schema")
def export_schema(
    out: Annotated[
        Path,
        typer.Option("--out", help="Where to write the JSON Schema for dashboard.json."),
    ] = schema_export.DEFAULT_SCHEMA_PATH,
) -> None:
    """Regenerate the committed JSON Schema from the export model. Run it whenever the
    model changes: the site's TypeScript types are generated from the committed file."""
    typer.echo(f"wrote {schema_export.write_schema(out)}")


if __name__ == "__main__":
    app()
