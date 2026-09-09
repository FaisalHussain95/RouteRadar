"""S11: `fd report <question>`, over the same seeded fixture as `tests/test_analytics.py`.

These tests are about the command, not the arithmetic: that the five question names are
wired to the five query functions, that the options reach them, and that a database with
nothing in it prints `no rows` instead of an empty table or a traceback.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from flight_detective.cli import app

runner = CliRunner()

QUESTIONS = ("lowest-fare", "arbitrage", "lead-time", "wedding-premium", "efficiency")


@pytest.mark.parametrize("question", QUESTIONS)
def test_every_question_answers_from_the_seeded_database(
    question: str, analytics_db_path: Path
) -> None:
    result = runner.invoke(app, ["report", question])
    assert result.exit_code == 0, result.output
    assert "no rows" not in result.output
    # Header, rule, then at least one row.
    assert len(result.output.splitlines()) >= 3


@pytest.mark.parametrize("question", QUESTIONS)
def test_every_question_survives_an_empty_database(question: str, isolated_db_path: Path) -> None:
    result = runner.invoke(app, ["report", question])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no rows"


def test_the_lowest_fare_table_is_carrier_by_month(analytics_db_path: Path) -> None:
    result = runner.invoke(app, ["report", "lowest-fare", "--origin", "ORY"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["carrier", "month", "lowest_eur", "observations"]
    assert lines[2].split() == ["TK", "2026-12", "870.00", "1"]


def test_the_arbitrage_table_carries_the_break_even_and_the_verdict(
    analytics_db_path: Path,
) -> None:
    result = runner.invoke(app, ["report", "arbitrage"])
    assert result.exit_code == 0, result.output
    rows = [line.split() for line in result.output.splitlines()[2:]]
    assert [row[0] for row in rows] == ["2026-09-23", "2026-10-09", "2026-12-08"]
    assert [row[-1] for row in rows] == ["lhe", "either", "skt"]
    assert {row[-2] for row in rows} == {"34.00"}


def test_break_even_is_an_option(analytics_db_path: Path) -> None:
    result = runner.invoke(app, ["report", "arbitrage", "--break-even", "150"])
    assert result.exit_code == 0, result.output
    rows = [line.split() for line in result.output.splitlines()[2:]]
    assert [row[-1] for row in rows] == ["either", "either", "either"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("cheap", "not an amount"),
        # Decimal builds these happily; NaN only blows up later, on the spread comparison,
        # and a negative transfer makes every spread a "win" so the verdict means nothing.
        ("nan", "not a finite amount"),
        ("Infinity", "not a finite amount"),
        ("-5", "cannot cost less than 0"),
    ],
)
def test_break_even_rejects_an_amount_it_cannot_compare_against(
    value: str, message: str, analytics_db_path: Path
) -> None:
    result = runner.invoke(app, ["report", "arbitrage", "--break-even", value])
    assert result.exit_code != 0
    assert message in result.output


@pytest.mark.parametrize(
    ("question", "option", "value"),
    [
        # Arbitrage compares destinations and reads whichever carrier is cheapest into
        # each, so both of these would silently answer a different question.
        ("arbitrage", "--destination", "LHE"),
        ("arbitrage", "--carrier", "QR"),
        # The efficiency index groups by carrier; filtering to one is the full table's row.
        ("efficiency", "--carrier", "QR"),
        # Only arbitrage prices a ground transfer.
        ("lowest-fare", "--break-even", "999"),
    ],
)
def test_an_option_a_question_does_not_read_is_an_error_not_a_no_op(
    question: str, option: str, value: str, analytics_db_path: Path
) -> None:
    result = runner.invoke(app, ["report", question, option, value])
    assert result.exit_code != 0
    assert "does not apply" in result.output


def test_origin_applies_to_every_question(analytics_db_path: Path) -> None:
    for question in QUESTIONS:
        result = runner.invoke(app, ["report", question, "--origin", "CDG"])
        assert result.exit_code == 0, result.output


def test_the_wedding_premium_is_one_row(analytics_db_path: Path) -> None:
    result = runner.invoke(app, ["report", "wedding-premium"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 3
    assert lines[2].split() == ["wedding_rush", "970.00", "575.00", "1.69", "68.70", "8", "4"]


def test_a_premium_with_an_empty_side_is_no_rows_not_a_zero(analytics_db_path: Path) -> None:
    # EK appears only inside the wedding window, so it has no February/March baseline.
    result = runner.invoke(app, ["report", "wedding-premium", "--carrier", "EK"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no rows"


def test_the_efficiency_table_leads_with_the_cheapest_hour(analytics_db_path: Path) -> None:
    result = runner.invoke(app, ["report", "efficiency"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == [
        "carrier",
        "avg_price_eur",
        "avg_hours",
        "eur_per_hour",
        "observations",
    ]
    assert lines[2].split() == ["GF", "618.57", "15.00", "41.24", "7"]


def test_filters_reach_the_query(analytics_db_path: Path) -> None:
    result = runner.invoke(app, ["report", "efficiency", "--destination", "SKT"])
    assert result.exit_code == 0, result.output
    assert [line.split()[0] for line in result.output.splitlines()[2:]] == ["GF"]


@pytest.mark.parametrize(("option", "value"), [("--origin", "LHR"), ("--destination", "KHI")])
def test_an_airport_outside_the_scope_is_rejected(
    option: str, value: str, analytics_db_path: Path
) -> None:
    result = runner.invoke(app, ["report", "lowest-fare", option, value])
    assert result.exit_code != 0
    assert "choose from" in result.output


def test_an_unknown_question_is_rejected(analytics_db_path: Path) -> None:
    result = runner.invoke(app, ["report", "why-is-it-expensive"])
    assert result.exit_code != 0
