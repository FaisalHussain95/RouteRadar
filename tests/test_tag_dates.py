"""`fd tag-dates` materialises `calendar_tag`. Idempotency is the whole point: the daily
pipeline re-runs it, and a re-run must leave exactly the same rows behind."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest
from typer.testing import CliRunner

from flight_detective import db
from flight_detective.cli import app
from flight_detective.models import CalendarTag

runner = CliRunner()


def rows(path: Path) -> list[tuple[date, str, Decimal, Decimal]]:
    with duckdb.connect(str(path)) as conn:
        return conn.execute("SELECT * FROM calendar_tag ORDER BY date, tag").fetchall()


def test_tag_dates_materialises_rows(isolated_db_path: Path) -> None:
    result = runner.invoke(app, ["tag-dates", "--from", "2026-03-15", "--to", "2026-03-23"])
    assert result.exit_code == 0, result.output
    got = rows(isolated_db_path)
    assert [(r[0], r[1]) for r in got] == [
        (date(2026, 3, 16) + timedelta(days=i), "eid_ul_fitr") for i in range(7)
    ]
    assert all(isinstance(r[2], Decimal) and isinstance(r[3], Decimal) for r in got)
    assert "7 rows" in result.output


def test_tag_dates_twice_is_idempotent(isolated_db_path: Path) -> None:
    args = ["tag-dates", "--from", "2026-12-01", "--to", "2027-01-31"]
    assert runner.invoke(app, args).exit_code == 0
    first = rows(isolated_db_path)
    assert runner.invoke(app, args).exit_code == 0
    assert rows(isolated_db_path) == first
    assert len(first) > 0


def test_tag_dates_replaces_stale_rows_in_range_only(isolated_db_path: Path) -> None:
    stale_in = CalendarTag(
        date=date(2026, 3, 20), tag="retired_window", multiplier_low="1.00", multiplier_high="1.10"
    )
    stale_out = CalendarTag(
        date=date(2026, 4, 1), tag="retired_window", multiplier_low="1.00", multiplier_high="1.10"
    )
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        db.upsert_calendar_tags(conn, [stale_in, stale_out])
    result = runner.invoke(app, ["tag-dates", "--from", "2026-03-15", "--to", "2026-03-23"])
    assert result.exit_code == 0, result.output
    tags_by_date = {(r[0], r[1]) for r in rows(isolated_db_path)}
    assert (date(2026, 3, 20), "retired_window") not in tags_by_date
    assert (date(2026, 4, 1), "retired_window") in tags_by_date


def test_tag_dates_updates_multipliers_on_conflict(isolated_db_path: Path) -> None:
    old = CalendarTag(
        date=date(2026, 3, 20), tag="eid_ul_fitr", multiplier_low="9.00", multiplier_high="9.50"
    )
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        db.upsert_calendar_tags(conn, [old])
        db.upsert_calendar_tags(conn, [old.model_copy(update={"multiplier_low": Decimal("1.20")})])
        fetched = db.fetch_calendar_tags(conn, date(2026, 3, 20), date(2026, 3, 20))
    assert fetched == [old.model_copy(update={"multiplier_low": Decimal("1.20")})]


def test_tag_dates_defaults_to_a_year_from_today(isolated_db_path: Path) -> None:
    result = runner.invoke(app, ["tag-dates"])
    assert result.exit_code == 0, result.output
    today = datetime.now(ZoneInfo("Europe/Paris")).date()
    got = rows(isolated_db_path)
    assert got, "a year always contains at least one window"
    assert min(r[0] for r in got) >= today
    assert max(r[0] for r in got) <= today + timedelta(days=365)
    assert "wedding_rush" in {r[1] for r in got}


def test_tag_dates_rejects_inverted_range(isolated_db_path: Path) -> None:
    result = runner.invoke(app, ["tag-dates", "--from", "2026-03-23", "--to", "2026-03-15"])
    assert result.exit_code != 0
    assert "--to" in result.output


def test_tag_dates_rejects_bad_date(isolated_db_path: Path) -> None:
    result = runner.invoke(app, ["tag-dates", "--from", "next tuesday", "--to", "2026-03-15"])
    assert result.exit_code != 0


def test_tag_dates_rejects_dates_beyond_the_hijri_tables(isolated_db_path: Path) -> None:
    # hijri-converter's Umm al-Qura tables end in 2077; that must be a usage error,
    # not a traceback.
    result = runner.invoke(app, ["tag-dates", "--from", "2077-12-01", "--to", "2078-01-01"])
    assert result.exit_code != 0
    assert "2077" in result.output


def test_tag_dates_works_on_a_fresh_database(isolated_db_path: Path) -> None:
    assert not isolated_db_path.exists()
    result = runner.invoke(app, ["tag-dates", "--from", "2026-05-18", "--to", "2026-05-18"])
    assert result.exit_code == 0, result.output
    assert rows(isolated_db_path) == [
        (date(2026, 5, 18), "hajj_eid_ul_adha", Decimal("1.10"), Decimal("1.30"))
    ]


@pytest.mark.parametrize("explicit_path", [True, False])
def test_tag_dates_honours_path_option(
    isolated_db_path: Path, tmp_path: Path, explicit_path: bool
) -> None:
    other = tmp_path / "other.duckdb"
    args = ["tag-dates", "--from", "2026-05-18", "--to", "2026-05-18"]
    if explicit_path:
        args += ["--path", str(other)]
    assert runner.invoke(app, args).exit_code == 0
    assert other.exists() == explicit_path
    assert isolated_db_path.exists() != explicit_path
