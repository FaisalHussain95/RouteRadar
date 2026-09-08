"""Command-line entry point. Every subcommand added by a story is registered here."""

import typer

from flight_detective import __version__

app = typer.Typer(help="Flight Detective: fare tracking and explanation, CDG/ORY to ISB/LHE/SKT.")


@app.callback()
def main() -> None:
    """Root callback: keeps subcommands as subcommands even while there is only one."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
