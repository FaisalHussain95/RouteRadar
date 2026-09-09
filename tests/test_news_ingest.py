"""S10: `fd news-ingest` end to end.

Still no network: `GdeltClient` is built on an `httpx.MockTransport` that answers each
taxonomy query from its own fixture, so the pipeline sees exactly what the client would
have parsed off GDELT.
"""

from collections.abc import Callable
from datetime import date
from typing import Any

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from flight_detective import db
from flight_detective.cli import app
from flight_detective.news import gdelt
from flight_detective.news.dedupe import Candidate, Incident, group_incidents
from flight_detective.news.gdelt import TAXONOMY, Category, GdeltClient, TaxonomyQuery
from flight_detective.news.ingest import (
    REVERSAL_CUES,
    SEVERITY_SIGNALS,
    Severity,
    impact_note,
    run_news_ingest,
    severity_for,
    to_news_event,
)

runner = CliRunner()

Body = Callable[[str], dict[str, Any]]


def taxonomy_client(gdelt_body: Body, *, fail: set[str] | None = None) -> GdeltClient:
    """A client answering each taxonomy query from its own fixture. `fail` names slugs
    that should behave as a dead query (HTTP 429) instead."""
    failing = fail or set()
    by_query = {entry.query: entry.slug for entry in TAXONOMY}

    def handler(request: httpx.Request) -> httpx.Response:
        slug = by_query[request.url.params["query"]]
        if slug in failing:
            return httpx.Response(429, json={})
        return httpx.Response(200, json=gdelt_body(slug))

    return GdeltClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None
    )


@pytest.fixture
def conn() -> Any:
    connection = duckdb.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


# --- severity ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        ("EASA bans PIA from European airspace", Severity(3, "bans")),
        ("Pakistan closes airspace to overflights", Severity(3, "closes airspace")),
        ("CDG strike grounds hundreds of flights", Severity(3, "strike")),
        ("Doha transit delay stretches connection times", Severity(2, "delay")),
        ("Saudi Arabia cuts the Umrah quota for 2027", Severity(2, "cuts")),
        ("PIA adds a third weekly Paris frequency", Severity(1)),
    ],
)
def test_severity_comes_from_the_documented_keywords(headline: str, expected: Severity) -> None:
    assert severity_for(headline) == expected


def test_the_highest_matching_band_wins() -> None:
    # "delay" is a 2 and "strike" is a 3; the headline carries both.
    assert severity_for("Strike delays hundreds of flights").level == 3


def test_a_keyword_does_not_fire_inside_a_longer_word() -> None:
    # "cut" is a severity-2 keyword; "executive" must not trip it.
    assert severity_for("Executive shuffle at the airline") == Severity(1)


@pytest.mark.parametrize(
    ("headline", "keyword", "cue"),
    [
        ("EASA lifts its ban on PIA flights to Europe", "ban", "lifts"),
        ("Pakistan airspace closure lifted after two weeks", "airspace closure", "lifted"),
        ("CDG strike called off after pay deal", "strike", "called off"),
        ("Flights to Lahore resume as the PIA ban is dropped", "ban", "resume"),
    ],
)
def test_a_reversed_restriction_is_context_not_a_shock(
    headline: str, keyword: str, cue: str
) -> None:
    # The scale measures capacity *lost*; a ban being lifted is a fare mover, not a shock,
    # and a severity-3 marker on it would mislead the explanation layer.
    assert severity_for(headline) == Severity(1, keyword, cue)


@pytest.mark.parametrize(
    "headline",
    [
        # Words that read as a reversal but are not. The demotion drops straight from 3
        # to 1, so a cue that can appear in a live capacity loss is worse than no cue:
        # "end"/"ends" and "revoked" were in the list and were removed over exactly these.
        "Airspace closure extended to the end of October",
        "Air France strike ends its third day with more cancellations",
        "PIA suspends Paris flights as the summer season ends",
        "PIA suspends flights after its licence was revoked",
    ],
)
def test_an_ambiguous_word_does_not_demote_a_live_capacity_loss(headline: str) -> None:
    scored = severity_for(headline)
    assert scored.level == 3
    assert scored.reversed_by is None


def test_a_reversal_cue_does_not_demote_a_band_two_match() -> None:
    # "delay" is band 2 and mild enough already; reading direction into it would over-fit.
    assert severity_for("Airline ends the delays on its Paris route") == Severity(2, "delays")


def test_every_severity_band_is_in_range() -> None:
    assert [level for level, _ in SEVERITY_SIGNALS] == [3, 2]


def test_reversal_cues_are_lowercase_so_they_match_a_normalised_title() -> None:
    assert all(cue == cue.lower() for cue in REVERSAL_CUES)


# --- rows -------------------------------------------------------------------------------


def sole_incident(articles: list[gdelt.Article], category: Category = "regulatory") -> Incident:
    """Group `articles` under one category and assert they were all one story."""
    incidents = group_incidents([Candidate(category=category, article=a) for a in articles])
    assert len(incidents) == 1
    return incidents[0]


def test_a_row_carries_category_severity_and_source_url(gdelt_body: Body) -> None:
    incident = sole_incident(gdelt.parse_articles(gdelt_body("regulatory-easa-pia"))[:2])
    event = to_news_event(incident)
    assert event.category == "regulatory"
    # "EASA lifts its ban …" is a restriction being undone, so band 3 is demoted to 1.
    assert event.severity == 1
    assert event.source_url.startswith("https://www.reuters.com/")
    assert event.event_date == date(2026, 9, 1)
    assert event.headline == incident.canonical.title
    assert event.dedupe_key.startswith("2026-09-01:")


def test_impact_note_records_the_outlets_and_the_severity_keyword(gdelt_body: Body) -> None:
    incident = sole_incident(gdelt.parse_articles(gdelt_body("regulatory-easa-pia"))[:2])
    note = impact_note(incident, severity_for(incident.canonical.title))
    assert note == (
        "2 articles (reuters.com, dawn.com); severity keyword 'ban', reversed by 'lifts'"
    )


def test_impact_note_says_so_when_nothing_matched(gdelt_body: Body) -> None:
    incident = sole_incident(gdelt.parse_articles(gdelt_body("regulatory-traffic-rights")))
    assert impact_note(incident, Severity(1)) == (
        "1 article (flightglobal.com); no severity keyword"
    )


# --- pipeline ---------------------------------------------------------------------------


def test_a_run_stores_one_row_per_incident_and_logs_what_it_did(
    conn: duckdb.DuckDBPyConnection, gdelt_body: Body
) -> None:
    result = run_news_ingest(conn, taxonomy_client(gdelt_body), days=7)
    stored = db.fetch_news_events(conn)
    # Eight usable articles across the four fixtures collapse into six incidents: the two
    # EASA/PIA reports are one story, and so are the two CDG strike reports.
    assert len(stored) == 6
    assert result.run.queries == len(TAXONOMY)
    assert result.run.rows_kept == 6
    assert result.run.rows_dropped == 2
    assert result.run.error is None
    assert result.run.provider == "gdelt"


def test_every_stored_row_has_a_category_a_severity_and_a_source(
    conn: duckdb.DuckDBPyConnection, gdelt_body: Body
) -> None:
    run_news_ingest(conn, taxonomy_client(gdelt_body), days=7)
    for event in db.fetch_news_events(conn):
        assert event.category in {"regulatory", "airspace_disruption", "pilgrimage_visas"}
        assert 1 <= event.severity <= 3
        assert event.source_url.startswith("https://")
        assert event.impact_note


def test_events_come_back_newest_day_first(
    conn: duckdb.DuckDBPyConnection, gdelt_body: Body
) -> None:
    run_news_ingest(conn, taxonomy_client(gdelt_body), days=7)
    dates = [event.event_date for event in db.fetch_news_events(conn)]
    assert dates == sorted(dates, reverse=True)


def test_fetch_can_be_bounded_at_either_end(
    conn: duckdb.DuckDBPyConnection, gdelt_body: Body
) -> None:
    run_news_ingest(conn, taxonomy_client(gdelt_body), days=7)
    assert len(db.fetch_news_events(conn, start=date(2026, 9, 1))) == 4
    assert len(db.fetch_news_events(conn, end=date(2026, 8, 31))) == 2
    assert len(db.fetch_news_events(conn, date(2026, 9, 1), date(2026, 9, 1))) == 2


def test_re_ingesting_the_same_window_is_idempotent(
    conn: duckdb.DuckDBPyConnection, gdelt_body: Body
) -> None:
    first = run_news_ingest(conn, taxonomy_client(gdelt_body), days=7)
    before = db.fetch_news_events(conn)
    second = run_news_ingest(conn, taxonomy_client(gdelt_body), days=7)
    after = db.fetch_news_events(conn)
    assert before == after
    assert first.run.rows_kept == second.run.rows_kept
    # The run log, unlike the events, gains a row: it is a log.
    assert len(db.fetch_ingest_runs(conn)) == 2


def test_one_dead_query_costs_that_query_and_nothing_else(
    conn: duckdb.DuckDBPyConnection, gdelt_body: Body
) -> None:
    result = run_news_ingest(
        conn, taxonomy_client(gdelt_body, fail={"airspace-disruption"}), days=7
    )
    assert result.run.error is not None
    assert result.run.error.startswith("airspace-disruption: ")
    assert "\n" not in result.run.error
    # The CDG/Doha incidents are gone; the other three fixtures still landed.
    assert result.run.rows_kept == 4
    assert len(db.fetch_news_events(conn)) == 4


def test_a_run_where_everything_failed_is_logged_with_zero_rows(
    conn: duckdb.DuckDBPyConnection, gdelt_body: Body
) -> None:
    failing = {entry.slug for entry in TAXONOMY}
    result = run_news_ingest(conn, taxonomy_client(gdelt_body, fail=failing), days=7)
    assert result.run.rows_kept == 0
    assert result.run.error is not None
    assert len(result.run.error.splitlines()) == len(TAXONOMY)
    assert db.fetch_news_events(conn) == []


def test_a_non_gdelt_exception_is_a_bug_and_writes_nothing(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    class Broken(GdeltClient):
        def search(self, query: str, days: int) -> list[gdelt.Article]:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_news_ingest(conn, Broken(sleep=lambda _s: None), days=7)
    assert db.fetch_news_events(conn) == []
    assert db.fetch_ingest_runs(conn) == []


def test_queries_are_paced_apart_but_not_before_the_first(
    conn: duckdb.DuckDBPyConnection, gdelt_body: Body
) -> None:
    pauses: list[float] = []
    run_news_ingest(conn, taxonomy_client(gdelt_body), days=7, sleep=pauses.append)
    assert pauses == [gdelt.REQUEST_INTERVAL_SECONDS] * (len(TAXONOMY) - 1)


def test_an_empty_taxonomy_writes_a_run_and_no_events(
    conn: duckdb.DuckDBPyConnection, gdelt_body: Body
) -> None:
    result = run_news_ingest(conn, taxonomy_client(gdelt_body), days=7, taxonomy=())
    assert result.run.queries == 0
    assert result.run.error is None
    assert db.fetch_news_events(conn) == []


def test_a_custom_taxonomy_is_honoured(conn: duckdb.DuckDBPyConnection, gdelt_body: Body) -> None:
    only = [entry for entry in TAXONOMY if entry.slug == "pilgrimage-visas"]
    result = run_news_ingest(conn, taxonomy_client(gdelt_body), days=7, taxonomy=only)
    assert result.run.queries == 1
    assert [e.category for e in db.fetch_news_events(conn)] == ["pilgrimage_visas"]
    assert isinstance(only[0], TaxonomyQuery)


# --- CLI --------------------------------------------------------------------------------


def install_client(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body, *, fail: set[str] | None = None
) -> None:
    """Make `fd news-ingest` build the mock-transport client instead of a real one."""
    monkeypatch.setattr(
        "flight_detective.cli.GdeltClient", lambda: taxonomy_client(gdelt_body, fail=fail)
    )


def test_cli_stores_events_and_reports_the_counts(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body, isolated_db_path: Any
) -> None:
    install_client(monkeypatch, gdelt_body)
    result = runner.invoke(app, ["news-ingest", "--days", "7"])
    assert result.exit_code == 0, result.output
    assert "6 events, 2 duplicates collapsed" in result.output
    with db.connect(isolated_db_path) as conn:
        assert len(db.fetch_news_events(conn)) == 6


def test_cli_defaults_to_a_week(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body, isolated_db_path: Any
) -> None:
    install_client(monkeypatch, gdelt_body)
    result = runner.invoke(app, ["news-ingest"])
    assert result.exit_code == 0, result.output
    assert "last 7 days" in result.output


def test_cli_writes_to_the_path_it_is_given(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body, tmp_path: Any
) -> None:
    install_client(monkeypatch, gdelt_body)
    elsewhere = tmp_path / "elsewhere.duckdb"
    assert runner.invoke(app, ["news-ingest", "--path", str(elsewhere)]).exit_code == 0
    with db.connect(elsewhere) as conn:
        assert len(db.fetch_news_events(conn)) == 6


@pytest.mark.parametrize("days", ["0", "91", "-3"])
def test_cli_rejects_a_window_outside_gdelts_archive(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body, days: str
) -> None:
    install_client(monkeypatch, gdelt_body)
    result = runner.invoke(app, ["news-ingest", "--days", days])
    assert result.exit_code != 0
    assert "90 days" in result.output


def test_cli_survives_a_partial_failure(monkeypatch: pytest.MonkeyPatch, gdelt_body: Body) -> None:
    install_client(monkeypatch, gdelt_body, fail={"airspace-disruption"})
    result = runner.invoke(app, ["news-ingest"])
    # News is context, not the dataset: the export must not be blocked by one dead query.
    assert result.exit_code == 0, result.output
    assert "1 of 4 taxonomy queries failed" in result.output
    assert "airspace-disruption" in result.output


def test_cli_fails_when_every_query_failed(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body
) -> None:
    install_client(monkeypatch, gdelt_body, fail={entry.slug for entry in TAXONOMY})
    result = runner.invoke(app, ["news-ingest"])
    assert result.exit_code == 1
    assert "4 of 4 taxonomy queries failed" in result.output


def test_the_pipeline_script_step_exists() -> None:
    # deploy/run-pipeline.sh chains `fd news-ingest`; a renamed command breaks the timer.
    assert "news-ingest" in {command.name for command in app.registered_commands}
