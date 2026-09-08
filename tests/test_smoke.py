from typer.testing import CliRunner

from flight_detective import __version__
from flight_detective.cli import app


def test_version_command_prints_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__
