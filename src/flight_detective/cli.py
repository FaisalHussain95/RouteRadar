"""Command-line entry point. Every subcommand added by a story is registered here."""

from pathlib import Path
from typing import Annotated

import typer

from flight_detective import __version__, db
from flight_detective.config import load_settings

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


@db_app.command("init")
def db_init(
    path: Annotated[
        Path | None,
        typer.Option(help="Database file; defaults to FD_DB_PATH or data/flight_detective.duckdb."),
    ] = None,
) -> None:
    """Create the database file and its tables. Idempotent: re-running changes nothing."""
    db_path = path if path is not None else load_settings().db_path
    with db.connect(db_path) as conn:
        db.init_schema(conn)
    typer.echo(f"initialised {db_path} ({', '.join(db.TABLE_NAMES)})")


if __name__ == "__main__":
    app()
