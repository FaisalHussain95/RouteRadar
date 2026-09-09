"""The dashboard JSON contract: what `fd export-site` writes and what the schema says.

The expected numbers below are worked out by hand from `tests/fixtures/analytics/fares.json`
(the same 21 rows S11 and S12 use), not recomputed from it: a test that re-derives its
expectation from the fixture only proves the export agrees with itself.

`generated_at` is always passed explicitly. The export window is anchored on it, so a
test that let it default to "now" would change shape every day.
"""

import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import jsonschema
import pytest
from typer.testing import CliRunner

from flight_detective import db
from flight_detective.calendar_engine.gregorian import WINDOWS
from flight_detective.calendar_engine.hijri import HIJRI_WINDOWS
from flight_detective.cli import DEFAULT_TAG_SPAN, app
from flight_detective.export import schema as schema_module
from flight_detective.export import site
from flight_detective.models import NewsEvent

runner = CliRunner()

GENERATED_AT = datetime.fromisoformat("2026-09-09T06:35:00+02:00")

SPECS = Path(__file__).resolve().parents[1] / "specs"

# What `news/ingest.py` writes into `impact_note`; the export passes it through.
NOTE = "4 articles (aviationherald.example); severity keyword 'closes airspace'"


@pytest.fixture
def empty_conn(isolated_db_path: Path) -> Any:
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        yield conn


@pytest.fixture(scope="module")
def json_schema() -> dict[str, Any]:
    return schema_module.dashboard_json_schema()


def _exported(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """The dashboard as it lands in the file: parsed back out of the rendered JSON."""
    payload = json.loads(site.render(site.build_dashboard(conn, generated_at=GENERATED_AT)))
    assert isinstance(payload, dict)
    return payload


def _series(payload: dict[str, Any], destination: str, horizon: int, carrier: str) -> Any:
    for entry in payload["series"]:
        if (entry["destination"], entry["horizon_days"], entry["carrier"]) == (
            destination,
            horizon,
            carrier,
        ):
            return entry
    return None


def _walk(node: Any, path: str = "") -> Any:
    """Every (json path, value) pair in the document, so a rule can be asserted globally."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")
    else:
        yield path, node


# -- the contract ---------------------------------------------------------------------------
def test_export_on_the_seeded_db_validates_against_the_schema(
    analytics_conn: duckdb.DuckDBPyConnection, json_schema: dict[str, Any]
) -> None:
    jsonschema.validate(_exported(analytics_conn), json_schema)


def test_export_round_trips_through_json(analytics_conn: duckdb.DuckDBPyConnection) -> None:
    data = site.build_dashboard(analytics_conn, generated_at=GENERATED_AT)
    assert site.DashboardData.model_validate_json(site.render(data)) == data


def test_the_committed_schema_matches_the_model() -> None:
    """`specs/dashboard-data.schema.json` is generated; a stale one is a broken contract."""
    committed = (SPECS / "dashboard-data.schema.json").read_text()
    assert committed == schema_module.render_schema()


def test_schema_version_is_present_and_matches_the_model(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    assert _exported(analytics_conn)["schema_version"] == site.SCHEMA_VERSION


def test_every_price_is_a_whole_number_of_euros(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    payload = _exported(analytics_conn)
    euros = [(p, v) for p, v in _walk(payload) if p.endswith("_eur")]
    assert euros, "no price fields found; the walk is broken, not the export"
    for path, value in euros:
        assert value is None or isinstance(value, int), f"{path} = {value!r} is not an integer"
        assert not isinstance(value, bool), path


def test_every_date_is_an_iso_day(analytics_conn: duckdb.DuckDBPyConnection) -> None:
    payload = _exported(analytics_conn)
    days = [
        (path, value)
        for path, value in _walk(payload)
        if value is not None
        and (path.endswith(("_date", ".from", ".to")) or path.endswith(".date"))
    ]
    assert days
    for path, value in days:
        assert date.fromisoformat(value).isoformat() == value, path


def test_generated_at_keeps_its_offset(analytics_conn: duckdb.DuckDBPyConnection) -> None:
    payload = _exported(analytics_conn)
    assert datetime.fromisoformat(payload["generated_at"]) == GENERATED_AT


# -- the empty database ---------------------------------------------------------------------
def test_empty_db_exports_a_valid_file_with_empty_series(
    empty_conn: duckdb.DuckDBPyConnection, json_schema: dict[str, Any], tmp_path: Path
) -> None:
    """Before the first ingest the site still has to build, so the file has to exist and
    validate: empty series, no observation day, and the nullable regions null."""
    data = site.build_dashboard(empty_conn, generated_at=GENERATED_AT)
    out = site.write_dashboard(data, tmp_path / "dashboard.json")
    payload = json.loads(out.read_text())
    jsonschema.validate(payload, json_schema)
    assert payload["series"] == []
    assert payload["events"] == []
    assert payload["efficiency"] == []
    assert payload["arbitrage"] is None
    assert payload["seasonal_gauge"] is None
    assert payload["observed_on"] is None
    assert datetime.fromisoformat(payload["generated_at"]) == GENERATED_AT


def test_empty_db_still_carries_the_chrome_the_filter_bar_needs(
    empty_conn: duckdb.DuckDBPyConnection,
) -> None:
    payload = _exported(empty_conn)
    assert [c["code"] for c in payload["carriers"]] == ["PK", "QR", "EK", "GF", "TK", "SV"]
    assert [d["code"] for d in payload["destinations"]] == ["ISB", "LHE", "SKT"]
    assert payload["horizons"] == [14, 30, 60, 90, 120, 180]


def test_bands_are_populated_before_the_first_ingest(
    empty_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Bands come from the calendar engine, not from fares, so the page draws them on an
    empty chart (S13's note on this story)."""
    bands = _exported(empty_conn)["bands"]
    wedding = [b for b in bands if b["tag"] == "wedding_rush"]
    assert wedding == [
        {
            "tag": "wedding_rush",
            "label": "Desi wedding season",
            "short_label": "Wedding peak",
            "kind": "wedding",
            "from": "2026-11-15",
            "to": "2027-01-15",
            "multiplier_low": 1.4,
            "multiplier_high": 1.8,
        }
    ]


def test_bands_are_clipped_to_the_export_window(empty_conn: duckdb.DuckDBPyConnection) -> None:
    payload = _exported(empty_conn)
    start = (GENERATED_AT.date() - site.HISTORY).isoformat()
    end = (GENERATED_AT.date() + site.FORWARD).isoformat()
    assert payload["bands"], "the window covers more than a year; some window must fall in it"
    for band in payload["bands"]:
        assert start <= band["from"] <= band["to"] <= end


def test_the_forward_window_is_the_range_fd_tag_dates_tags() -> None:
    """A band past the tagged range would name a window `calendar_tag` has no row for,
    so the explainer and the chart would disagree."""
    assert site.FORWARD == DEFAULT_TAG_SPAN


# -- series ---------------------------------------------------------------------------------
def test_series_are_one_curve_per_destination_horizon_and_carrier(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    payload = _exported(analytics_conn)
    keys = [(s["destination"], s["horizon_days"], s["carrier"]) for s in payload["series"]]
    assert len(keys) == len(set(keys))
    assert keys == sorted(keys)
    assert ("ISB", 14, "QR") in keys
    assert ("SKT", 30, "GF") in keys


def test_a_series_holds_the_lowest_price_seen_per_departure_date(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    """CDG and ORY are one origin to the page, and the same departure is quoted on more
    than one observation day: 700 on 2026-09-09 and 720 on 2026-09-02, so 700 wins."""
    entry = _series(_exported(analytics_conn), "ISB", 14, "QR")
    assert entry["points"] == [{"departure_date": "2026-09-23", "price_eur": 700}]


def test_lead_times_are_bucketed_to_the_nearest_horizon(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    """2026-10-11 is 32 days out from 2026-09-09, which is nearer 30 than 60."""
    entry = _series(_exported(analytics_conn), "ISB", 30, "QR")
    assert entry["points"] == [
        {"departure_date": "2026-10-09", "price_eur": 660},
        {"departure_date": "2026-10-11", "price_eur": 700},
    ]


def test_a_carrier_the_palette_has_no_colour_for_is_left_out_of_every_region(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Six carriers is the palette's ceiling (S13) and the page cannot draw a line it has
    no colour for, so an unknown code is dropped. The scope filters do not filter by
    carrier, so a real ingest *will* return other codes — and dropping them from the chart
    while leaving them in the modules would put a spread on the arbitrage card that no line
    on the chart accounts for. The Air France fares below undercut both LHE and SKT and
    would move every region if any of them still counted them."""
    before = _exported(analytics_conn)
    analytics_conn.execute(
        "INSERT INTO fare_observation VALUES "
        "('2026-09-09', '2026-09-23', 'CDG', 'ISB', 'AF', 'AF1', 1, 90, 800, 120.00, 'x', 'a'),"
        "('2026-09-09', '2026-09-23', 'CDG', 'LHE', 'AF', 'AF2', 1, 90, 800, 120.00, 'x', 'b'),"
        "('2026-09-09', '2026-09-23', 'CDG', 'SKT', 'AF', 'AF3', 1, 90, 800, 130.00, 'x', 'c'),"
        "('2026-09-09', '2026-12-08', 'CDG', 'ISB', 'AF', 'AF4', 1, 90, 800, 140.00, 'x', 'd')"
    )
    after = _exported(analytics_conn)
    assert after == before


# -- the three modules ----------------------------------------------------------------------
def test_arbitrage_is_the_nearest_departure_on_the_latest_observation_day(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    assert _exported(analytics_conn)["arbitrage"] == {
        "departure_date": "2026-09-23",
        "lhe_eur": 520,
        "skt_eur": 610,
        "isb_eur": 600,
        "spread_eur": -90,
        "ground_transfer_eur": 34,
        "ground_time": "4h 10m",
        "verdict": "lhe",
    }


def test_arbitrage_is_null_when_one_airport_was_never_quoted(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    analytics_conn.execute("DELETE FROM fare_observation WHERE destination = 'SKT'")
    assert _exported(analytics_conn)["arbitrage"] is None


def test_efficiency_is_cheapest_hour_first(analytics_conn: duckdb.DuckDBPyConnection) -> None:
    assert _exported(analytics_conn)["efficiency"] == [
        {"carrier": "GF", "price_eur": 619, "transit_minutes": 900},
        {"carrier": "QR", "price_eur": 808, "transit_minutes": 840},
        {"carrier": "TK", "price_eur": 870, "transit_minutes": 890},
        {"carrier": "PK", "price_eur": 687, "transit_minutes": 540},
        {"carrier": "EK", "price_eur": 1020, "transit_minutes": 780},
    ]


def test_the_seasonal_gauge_is_the_wedding_premium(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    """8 wedding-window rows averaging 970 against 4 February/March rows averaging 575."""
    gauge = _exported(analytics_conn)["seasonal_gauge"]
    assert gauge["current_avg_eur"] == 970
    assert gauge["baseline_avg_eur"] == 575
    assert gauge["pct"] == 69
    assert "February" in gauge["method"]


def test_the_seasonal_gauge_is_null_without_a_baseline(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    """A premium against nothing is unknown, not 0 % — the card must not print +0%."""
    analytics_conn.execute("DELETE FROM fare_observation WHERE month(departure_date) IN (2, 3)")
    assert _exported(analytics_conn)["seasonal_gauge"] is None


# -- events ---------------------------------------------------------------------------------
def _news(conn: duckdb.DuckDBPyConnection) -> None:
    db.upsert_news_events(
        conn,
        [
            NewsEvent(
                event_date=date(2026, 9, 4),
                category="airspace-disruption",
                severity=3,
                headline="Pakistan closes airspace to Indian carriers",
                source_url="https://www.aviationherald.example/news/1",
                dedupe_key="a",
                impact_note=NOTE,
            ),
            NewsEvent(
                event_date=date(2026, 9, 7),
                category="carrier-capacity",
                severity=1,
                headline="PIA reviews its European schedule",
                source_url="https://dawn.example/story/2",
                dedupe_key="b",
            ),
        ],
    )


def test_events_carry_the_feed_and_drawer_fields(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    _news(analytics_conn)
    events = _exported(analytics_conn)["events"]
    assert events == [
        {
            "date": "2026-09-04",
            "severity": "high",
            "headline": "Pakistan closes airspace to Indian carriers",
            "source": "aviationherald.example",
            "source_url": "https://www.aviationherald.example/news/1",
            "impact_text": NOTE,
            "body": None,
        },
        {
            "date": "2026-09-07",
            "severity": "low",
            "headline": "PIA reviews its European schedule",
            "source": "dawn.example",
            "source_url": "https://dawn.example/story/2",
            "impact_text": None,
            "body": None,
        },
    ]


def test_events_outside_the_window_are_dropped(
    analytics_conn: duckdb.DuckDBPyConnection,
) -> None:
    """The chart's x-axis is the export window; a pin outside it has nowhere to sit."""
    db.upsert_news_events(
        analytics_conn,
        [
            NewsEvent(
                event_date=GENERATED_AT.date() - site.HISTORY - timedelta(days=1),
                category="airspace-disruption",
                severity=2,
                headline="Too old to pin",
                source_url="https://old.example/1",
                dedupe_key="old",
            )
        ],
    )
    assert _exported(analytics_conn)["events"] == []


# -- carriers -------------------------------------------------------------------------------
def test_carrier_colours_are_the_design_system_tokens() -> None:
    """The export is the only place the palette leaves the spec, so it is checked against
    it rather than trusted: `tests/test_design_system.py` guards the tokens themselves."""
    import re

    block = (SPECS / "ux" / "design-system.md").read_text()
    block = block.split("## Tokens", 1)[1].split("\n## ", 1)[0]
    tokens = dict(re.findall(r"(--[a-zA-Z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))
    for carrier in site.CARRIERS:
        assert carrier.color == tokens[f"--carrier-{carrier.code}"], carrier.code


def test_the_direct_carrier_has_no_hub() -> None:
    by_code = {c.code: c for c in site.CARRIERS}
    assert by_code["PK"].direct is True
    assert by_code["PK"].hub is None
    assert by_code["QR"].direct is False
    assert by_code["QR"].hub == "DOH"


# -- the write ------------------------------------------------------------------------------
def test_the_file_is_published_by_rename_and_leaves_no_temp_behind(
    empty_conn: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader opening the path mid-write must see the previous file or the new one, never
    a half-written one. The temp file has to be complete before the rename, and gone after."""
    out = tmp_path / "site" / "dashboard.json"
    out.parent.mkdir()
    out.write_text('{"schema_version": 0}')
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: Any, dst: Any) -> None:
        seen.append((str(src), str(dst)))
        # Mid-write the destination still holds the previous export, in full.
        assert json.loads(Path(dst).read_text()) == {"schema_version": 0}
        assert json.loads(Path(src).read_text())["schema_version"] == site.SCHEMA_VERSION
        real_replace(src, dst)

    monkeypatch.setattr(site.os, "replace", spy)
    site.write_dashboard(site.build_dashboard(empty_conn, generated_at=GENERATED_AT), out)
    assert len(seen) == 1 and seen[0][1] == str(out)
    assert sorted(p.name for p in out.parent.iterdir()) == ["dashboard.json"]


def test_a_failed_write_leaves_no_temp_file_and_no_damaged_output(
    empty_conn: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "site" / "dashboard.json"
    out.parent.mkdir()
    out.write_text("previous")

    def boom(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(site.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        site.write_dashboard(site.build_dashboard(empty_conn, generated_at=GENERATED_AT), out)
    assert out.read_text() == "previous"
    assert sorted(p.name for p in out.parent.iterdir()) == ["dashboard.json"]


def test_a_failed_write_of_the_temp_file_cleans_up_after_itself(
    empty_conn: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The disk filling up mid-write is the case the temp file exists for; a truncated
    `.tmp` left beside a good export would be read as a real one by the next person."""
    out = tmp_path / "site" / "dashboard.json"
    out.parent.mkdir()
    out.write_text("previous")
    real_write_text = Path.write_text

    def boom(self: Path, *args: Any, **kwargs: Any) -> int:
        if self.name.endswith(".tmp"):
            real_write_text(self, "half a fi")
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError, match="disk full"):
        site.write_dashboard(site.build_dashboard(empty_conn, generated_at=GENERATED_AT), out)
    monkeypatch.undo()
    assert out.read_text() == "previous"
    assert sorted(p.name for p in out.parent.iterdir()) == ["dashboard.json"]


def test_write_creates_the_directory(empty_conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    out = tmp_path / "deep" / "site" / "dashboard.json"
    site.write_dashboard(site.build_dashboard(empty_conn, generated_at=GENERATED_AT), out)
    assert json.loads(out.read_text())["schema_version"] == site.SCHEMA_VERSION


# -- the CLI --------------------------------------------------------------------------------
def test_fd_export_site_writes_where_out_says(analytics_db_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "site" / "dashboard.json"
    result = runner.invoke(app, ["export-site", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert str(out) in result.output
    payload = json.loads(out.read_text())
    assert payload["observed_on"] == "2026-09-09"
    assert payload["series"]


def test_fd_export_site_defaults_to_the_configured_site_path(
    analytics_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`deploy/run-pipeline.sh` calls `fd export-site` with no arguments, so the default is
    the path that actually matters: a wrong one and S16's push finds nothing to commit."""
    out = tmp_path / "configured" / "dashboard.json"
    monkeypatch.setenv("FD_SITE_EXPORT_PATH", str(out))
    result = runner.invoke(app, ["export-site"])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["series"]


def test_fd_export_site_works_on_a_database_that_was_never_initialised(tmp_path: Path) -> None:
    """The site has to build before the first pipeline run, so a missing table is not an
    error here any more than it is for `fd report`."""
    out = tmp_path / "dashboard.json"
    result = runner.invoke(
        app, ["export-site", "--out", str(out), "--path", str(tmp_path / "x.db")]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["series"] == []


def test_fd_export_schema_writes_the_committed_schema(tmp_path: Path) -> None:
    out = tmp_path / "schema.json"
    result = runner.invoke(app, ["export-schema", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text() == (SPECS / "dashboard-data.schema.json").read_text()


# -- the schema itself ----------------------------------------------------------------------
def test_the_schema_allows_the_regions_an_incomplete_database_cannot_fill(
    json_schema: dict[str, Any],
) -> None:
    """S13's note: the site's generated types must carry these nulls rather than discover
    them at runtime."""
    skeleton = {
        "schema_version": site.SCHEMA_VERSION,
        "generated_at": GENERATED_AT.isoformat(),
        "observed_on": None,
        "carriers": [],
        "destinations": [],
        "horizons": [],
        "series": [],
        "bands": [],
        "events": [],
        "arbitrage": None,
        "efficiency": [],
        "seasonal_gauge": None,
    }
    jsonschema.validate(skeleton, json_schema)


def test_the_schema_rejects_a_float_price(json_schema: dict[str, Any]) -> None:
    bad = {
        "departure_date": "2026-09-23",
        "lhe_eur": 520.5,
        "skt_eur": 610,
        "isb_eur": None,
        "spread_eur": -90,
        "ground_transfer_eur": 34,
        "ground_time": "4h 10m",
        "verdict": "lhe",
    }
    arbitrage = json_schema["$defs"]["Arbitrage"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, {**arbitrage, "$defs": json_schema["$defs"]})


def test_break_even_reaches_the_export(analytics_conn: duckdb.DuckDBPyConnection) -> None:
    """The ground transfer is a hand-set input; the verdict has to move with it."""
    data = site.build_dashboard(
        analytics_conn, generated_at=GENERATED_AT, break_even_eur=Decimal("200")
    )
    assert data.arbitrage is not None
    assert data.arbitrage.ground_transfer_eur == 200
    assert data.arbitrage.verdict == "either"


def test_every_window_has_a_short_label() -> None:
    """`short_label` is the uppercase chip the hover card shows; a band without one would
    render blank rather than fail."""
    for window in (*WINDOWS, *HIJRI_WINDOWS):
        assert window.short_label
        assert len(window.short_label) <= 18, window.tag


def test_the_site_axis_uses_the_same_window_as_the_export() -> None:
    """The chart's x axis is the export window, and `web/src/lib/select.ts` hand-copies its
    two constants — there is no way to import a Python `timedelta` into TypeScript.

    Nothing else couples them. `HISTORY` follows GDELT's archive length (`news/gdelt.py`'s
    `MAX_DAYS`), so it moves if GDELT's does, and the site would then draw an axis narrower
    than the data it is given: `.plot svg` is `overflow: visible`, so the points would render
    outside the plot rather than disappearing, which is exactly the kind of failure nobody
    reports. This is the assertion that turns that into a red test."""
    source = Path(__file__).resolve().parents[1].joinpath("web/src/lib/select.ts").read_text()
    assert f"HISTORY_DAYS = {site.HISTORY.days};" in source
    assert f"FORWARD_DAYS = {site.FORWARD.days};" in source
