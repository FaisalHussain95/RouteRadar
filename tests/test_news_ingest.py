"""S17: `fd news-ingest` end to end.

Still no network: the client is built on an `httpx.MockTransport` that answers each of the
nine daily queries from its own recorded fixture, so the pipeline sees exactly what the
client would have parsed off GDELT Cloud.
"""

from collections.abc import Callable
from typing import Any

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from flight_detective import db
from flight_detective.cli import app
from flight_detective.news.gdeltcloud import (
    CARRIERS,
    SEMANTIC_QUERIES,
    GdeltCloudClient,
    Story,
)
from flight_detective.news.ingest import (
    SEVERITY_BANDS,
    Severity,
    impact_note,
    run_news_ingest,
    severity_for,
)

runner = CliRunner()

Body = Callable[[str], dict[str, Any]]

BY_ENTITY = {c.entity_id: f"carrier-{c.name.lower().replace(' ', '-')}" for c in CARRIERS}
BY_SEARCH = {q.search: q.slug for q in SEMANTIC_QUERIES}
ALL_SLUGS = [*BY_ENTITY.values(), *BY_SEARCH.values()]


def slug_for(request: httpx.Request) -> str:
    params = request.url.params
    if "entity" in params:
        return BY_ENTITY[params["entity"]]
    return BY_SEARCH[params["search"]]


def echo(request: httpx.Request) -> dict[str, Any]:
    """`applied_filters` as the server returns it, so the client's echo guard passes."""
    applied: dict[str, Any] = {"ignored": {}}
    for key, value in request.url.params.items():
        if key in ("limit", "cursor"):
            continue
        applied[key] = value.split(",") if key == "languages" else value
    return applied


def cloud_client(
    gdelt_body: Body,
    *,
    fail: set[str] | None = None,
    status: int = 429,
    error: dict[str, Any] | None = None,
    units: dict[str, Any] | None = None,
) -> GdeltCloudClient:
    """A client answering each query from its fixture. `fail` names slugs that should
    behave as a dead query instead — by default a rate-limit that outlives its retry."""
    failing = fail or set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/meta/query-units"):
            return httpx.Response(
                200,
                json=units
                or {
                    "usage": {
                        "plan_display_name": "Explore",
                        "remaining": 1010,
                        "allowance": {"effective": 1030},
                    }
                },
            )
        slug = slug_for(request)
        if slug in failing:
            return httpx.Response(status, json=error or {"error": {"code": "RATE_LIMITED"}})
        # The recorded body's own `applied_filters` is replaced by an echo of *this*
        # request: the fixtures were recorded with `days=7`, and a test asking for another
        # window must not fail the guard over the recording's stale echo.
        return httpx.Response(200, json=gdelt_body(slug) | {"applied_filters": echo(request)})

    return GdeltCloudClient(
        "test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


@pytest.fixture
def conn() -> Any:
    connection = duckdb.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


def story(**overrides: Any) -> Story:
    base: dict[str, Any] = {
        "id": "s1",
        "title": "PIA suspends its Paris service",
        "story_date": "2026-09-08",
        "metrics": {"significance": 0.0753, "article_count": 1},
        "top_articles": [{"url": "https://ex.test/a", "title": "", "domain": "ex.test"}],
    }
    return Story.model_validate(base | overrides)


# --- Severity -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("significance", "expected"),
    [(0.60, 3), (0.25, 3), (0.24, 2), (0.15, 2), (0.14, 1), (0.0753, 1)],
)
def test_severity_comes_from_significance(significance: float, expected: int) -> None:
    """The bands are the 90th and 75th percentiles of the recorded rows; the boundaries are
    inclusive, so a story exactly on a threshold gets the higher band."""
    assert severity_for(
        story(metrics={"significance": significance, "article_count": 1})
    ).level == (expected)


@pytest.mark.parametrize(("articles", "expected"), [(20, 3), (9, 3), (8, 2), (3, 2), (2, 1)])
def test_article_count_stands_in_when_significance_is_unmeasured(
    articles: int, expected: int
) -> None:
    """A `null` metric means unmeasured, never zero. Falling back to cluster size keeps a
    big story from being filed as a single report because one number was missing."""
    scored = severity_for(story(metrics={"significance": None, "article_count": articles}))
    assert scored.level == expected
    assert scored.significance is None


def test_the_bands_are_ordered_highest_first() -> None:
    """`severity_for` returns on the first band that matches, so an out-of-order table
    would silently score every big story as a 2."""
    levels = [level for level, _, _ in SEVERITY_BANDS]
    assert levels == sorted(levels, reverse=True)
    floors = [floor for _, floor, _ in SEVERITY_BANDS]
    assert floors == sorted(floors, reverse=True)


def test_severity_carries_the_measurement_that_set_it() -> None:
    scored = severity_for(story(metrics={"significance": 0.31, "article_count": 12}))
    assert scored == Severity(3, 0.31, 12)


def test_impact_note_names_the_evidence() -> None:
    """A surprising severity has to be traceable without re-running anything."""
    note = impact_note(story(), severity_for(story()), "pia")
    assert "1 article" in note
    assert "ex.test" in note
    assert "significance 0.0753" in note
    assert "kept on 'pia'" in note


def test_impact_note_says_so_when_significance_was_unmeasured() -> None:
    subject = story(metrics={"significance": None, "article_count": 5})
    assert "significance unmeasured" in impact_note(subject, severity_for(subject), "pia")


# --- The pipeline ---------------------------------------------------------------------


def test_a_run_keeps_only_what_the_guard_accepts(conn: Any, gdelt_body: Body) -> None:
    """300 recorded rows in, a couple of dozen aviation stories out, and the run row says
    how many were dropped rather than leaving the difference unexplained."""
    result = run_news_ingest(conn, cloud_client(gdelt_body))
    run = result.run
    assert run.queries == len(ALL_SLUGS)
    assert run.rows_kept == len(result.events)
    assert 0 < run.rows_kept < 40
    assert run.rows_dropped > 200
    assert run.error is None
    assert run.provider == "gdeltcloud"


def test_rows_land_in_the_database(conn: Any, gdelt_body: Body) -> None:
    result = run_news_ingest(conn, cloud_client(gdelt_body))
    stored = db.fetch_news_events(conn)
    assert {e.dedupe_key for e in stored} == {e.dedupe_key for e in result.events}


def test_the_dedupe_key_is_the_story_id(conn: Any, gdelt_body: Body) -> None:
    """A Story is already a cluster, so its id is the identity — no title similarity."""
    result = run_news_ingest(conn, cloud_client(gdelt_body))
    assert all(event.dedupe_key for event in result.events)
    assert len({e.dedupe_key for e in result.events}) == len(result.events)


def test_re_ingesting_the_same_window_is_idempotent(conn: Any, gdelt_body: Body) -> None:
    """The acceptance criterion: the rows land on the story id, so a second run over the
    same week rewrites the same rows instead of doubling them."""
    first = run_news_ingest(conn, cloud_client(gdelt_body))
    before = db.fetch_news_events(conn)
    second = run_news_ingest(conn, cloud_client(gdelt_body))
    after = db.fetch_news_events(conn)
    assert len(after) == len(before)
    assert {e.dedupe_key for e in after} == {e.dedupe_key for e in before}
    assert second.run.rows_kept == first.run.rows_kept


# Stories the recorded week returned from more than one query, and the arms that returned
# each. These are what make the cross-query dedupe and the carrier-first ordering testable
# on real data rather than on a constructed case.
SHARED_STORIES = {
    "47975009fced": ["carrier-pia", "pilgrimage-visas"],
    "ec8823ff77ec": ["carrier-qatar-airways", "carrier-emirates", "carrier-turkish-airlines"],
    "ae8b845533c7": ["carrier-emirates", "pilgrimage-visas", "cdg-strikes"],
    "ab18a9183422": ["airspace-disruption", "cdg-strikes"],
}


def test_a_story_returned_by_several_queries_becomes_one_row(conn: Any, gdelt_body: Body) -> None:
    """The recorded week has four stories that two or three queries each swept up, and all
    four survive the guard, so each is a real chance to emit a duplicate row.

    The counts are asserted **exactly**, not as bounds. `events` is a dict keyed on the
    story id, so "no duplicates" is true by construction and proves nothing; and a bound
    like `rows_dropped >= 6` is satisfied 50× over by a number that is really 299. What is
    worth pinning is the arithmetic: 309 rows fetched across the nine queries, 10 surviving,
    and every one of the other 299 — guard rejections *and* the six duplicate sightings —
    accounted for in `rows_dropped` rather than silently uncounted."""
    result = run_news_ingest(conn, cloud_client(gdelt_body))
    ids = [e.dedupe_key for e in result.events]
    for story_id in SHARED_STORIES:
        assert story_id in ids, story_id
    assert result.run.rows_kept == 10
    assert result.run.rows_dropped == 299
    assert result.run.rows_kept + result.run.rows_dropped == 309


def test_a_carrier_arm_names_a_story_the_semantic_arms_also_found(
    conn: Any, gdelt_body: Body
) -> None:
    """The load-bearing half of "first arm wins": carriers run first, so a story about an
    airline is filed under that airline's category rather than under whichever semantic
    pool also swept it up. Flipping the job order is what this catches.

    `47975009fced` is the PIA restructuring story, returned by both `carrier-pia` and the
    `pilgrimage-visas` pool. The carrier arm's default category is `regulatory`; the
    semantic arm would have filed it as `pilgrimage_visas`."""
    result = run_news_ingest(conn, cloud_client(gdelt_body))
    by_id = {e.dedupe_key: e for e in result.events}
    assert by_id["47975009fced"].category == "regulatory"
    assert "kept on 'pia'" in (by_id["47975009fced"].impact_note or "")


def one_story_client(subject: Story) -> GdeltCloudClient:
    """A client whose every query returns exactly `subject`, for the category tests below."""
    page = {"data": [subject.model_dump(mode="json")], "pagination": {"next_cursor": None}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page | {"applied_filters": echo(request)})

    return GdeltCloudClient(
        "k", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None
    )


def test_a_keyword_moves_a_story_out_of_its_query_category(conn: Any) -> None:
    """The other half of the category rule: a matched `RELEVANCE_KEYWORDS` entry carrying a
    category overrides the arm's default, so a *carrier* query's airspace story is filed as
    an airspace story rather than as `regulatory`.

    Built rather than taken from the fixtures on purpose. In the recorded week no story
    reaches an arm whose default disagrees with its keyword — the airspace stories all
    arrive through arms that already default to `airspace_disruption` — so a fixture-based
    version of this passes even with `keyword.category or category` reduced to `category`.
    Only a carrier arm returning an airspace story exercises the override at all."""
    client = one_story_client(story(id="x1", title="Airspace closed over Pakistan, PIA reroutes"))
    result = run_news_ingest(conn, client, carriers=CARRIERS[:1], semantic=())
    assert [e.category for e in result.events] == ["airspace_disruption"]
    assert "kept on 'airspace'" in (result.events[0].impact_note or "")


def test_a_carrier_story_without_a_topic_keyword_stays_regulatory(conn: Any) -> None:
    """The control for the test above: same arm, a story whose keyword carries no category,
    so the arm's default stands. Without this the override could be unconditional and the
    previous test would still pass."""
    client = one_story_client(story(id="x2", title="PIA seeks new leadership"))
    result = run_news_ingest(conn, client, carriers=CARRIERS[:1], semantic=())
    assert [e.category for e in result.events] == ["regulatory"]


def test_the_recorded_week_really_does_contain_those_shared_stories(gdelt_body: Body) -> None:
    """Pins `SHARED_STORIES` to the fixtures, so a re-recording without overlap turns the
    two tests above into tests of nothing instead of leaving them quietly passing."""
    for story_id, arms in SHARED_STORIES.items():
        found = [
            slug for slug in ALL_SLUGS if any(r["id"] == story_id for r in gdelt_body(slug)["data"])
        ]
        assert found == arms, story_id


def test_events_map_the_story_fields(conn: Any, gdelt_body: Body) -> None:
    result = run_news_ingest(conn, cloud_client(gdelt_body))
    for event in result.events:
        assert event.headline.strip()
        assert event.source_url.startswith("http")
        assert 1 <= event.severity <= 3
        assert event.impact_note


def test_one_dead_query_costs_only_that_query(conn: Any, gdelt_body: Body) -> None:
    """The bargain `run_ingest` makes for a fare cell: everything that answered is stored,
    and the failure is named in `ingest_run.error`."""
    client = cloud_client(gdelt_body, fail={"airspace-disruption"})
    result = run_news_ingest(conn, client)
    assert result.run.error is not None
    assert "airspace-disruption" in result.run.error
    assert result.run.error.count("\n") == 0
    assert result.run.rows_kept > 0


def test_every_query_failing_still_writes_a_run_row(conn: Any, gdelt_body: Body) -> None:
    """A run that gathered nothing is an `ingest_run` row with `rows_kept = 0`, which is
    exactly what that table is for."""
    result = run_news_ingest(conn, cloud_client(gdelt_body, fail=set(ALL_SLUGS)))
    assert result.run.rows_kept == 0
    assert result.run.error is not None
    assert result.run.error.count("\n") == len(ALL_SLUGS) - 1


def test_quota_exhaustion_stops_the_run_instead_of_failing_nine_times(
    conn: Any, gdelt_body: Body
) -> None:
    """Every remaining query would fail identically, so the loop stops — and names the
    queries it never issued, because a short run with no explanation reads like a quiet
    week."""
    client = cloud_client(
        gdelt_body,
        fail=set(ALL_SLUGS),
        error={"error": {"code": "QUOTA_EXCEEDED", "message": "month gone"}},
    )
    result = run_news_ingest(conn, client)
    assert result.run.queries == 1
    assert result.run.error is not None
    assert "not issued after quota exhausted" in result.run.error
    # The five carriers and three semantic groups behind the first query are all named.
    for slug in ALL_SLUGS[1:]:
        assert slug in result.run.error


def test_the_run_reports_the_remaining_budget(conn: Any, gdelt_body: Body) -> None:
    result = run_news_ingest(conn, cloud_client(gdelt_body))
    assert result.units is not None
    assert result.units.remaining == 1010
    assert result.units.is_low is False


def test_a_failed_budget_read_does_not_stop_the_run(conn: Any, gdelt_body: Body) -> None:
    """`/meta/query-units` is free but it is still a call that can time out, and losing the
    reading is not a reason to skip the day's news."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/meta/query-units"):
            return httpx.Response(500, text="down")
        return httpx.Response(
            200, json=gdelt_body(slug_for(request)) | {"applied_filters": echo(request)}
        )

    client = GdeltCloudClient(
        "k", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None
    )
    result = run_news_ingest(conn, client)
    assert result.units is None
    assert result.run.rows_kept > 0


def test_queries_are_spaced_apart(conn: Any, gdelt_body: Body) -> None:
    slept: list[float] = []
    run_news_ingest(conn, cloud_client(gdelt_body), sleep=slept.append)
    assert len(slept) == len(ALL_SLUGS) - 1


# --- The command ----------------------------------------------------------------------


def test_the_command_reports_kept_dropped_and_the_budget(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body
) -> None:
    monkeypatch.setenv("GDELT_API_KEY", "test-key")
    monkeypatch.setattr(
        "flight_detective.cli.GdeltCloudClient.from_settings",
        lambda _settings, **_kw: cloud_client(gdelt_body),
    )
    result = runner.invoke(app, ["news-ingest", "--days", "7"])
    assert result.exit_code == 0, result.output
    assert "events kept" in result.output
    assert "dropped" in result.output
    assert "query units:" in result.output


def test_the_command_warns_on_a_low_budget(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body
) -> None:
    """The budget is what decides whether tomorrow's run happens at all."""
    monkeypatch.setenv("GDELT_API_KEY", "test-key")
    low = {"usage": {"plan": "Explore", "remaining": 12, "allowance": {"effective": 1030}}}
    monkeypatch.setattr(
        "flight_detective.cli.GdeltCloudClient.from_settings",
        lambda _settings, **_kw: cloud_client(gdelt_body, units=low),
    )
    result = runner.invoke(app, ["news-ingest"])
    assert result.exit_code == 0, result.output
    assert "query units left this month" in result.output


def test_the_command_fails_only_when_every_query_died(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body
) -> None:
    monkeypatch.setenv("GDELT_API_KEY", "test-key")
    monkeypatch.setattr(
        "flight_detective.cli.GdeltCloudClient.from_settings",
        lambda _settings, **_kw: cloud_client(gdelt_body, fail=set(ALL_SLUGS)),
    )
    assert runner.invoke(app, ["news-ingest"]).exit_code == 1


def test_the_command_survives_one_dead_query(
    monkeypatch: pytest.MonkeyPatch, gdelt_body: Body
) -> None:
    monkeypatch.setenv("GDELT_API_KEY", "test-key")
    monkeypatch.setattr(
        "flight_detective.cli.GdeltCloudClient.from_settings",
        lambda _settings, **_kw: cloud_client(gdelt_body, fail={"cdg-strikes"}),
    )
    result = runner.invoke(app, ["news-ingest"])
    assert result.exit_code == 0, result.output


def test_a_missing_key_is_one_actionable_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The acceptance criterion. A setup problem must not surface as nine identical
    per-query failures."""
    monkeypatch.delenv("GDELT_API_KEY", raising=False)
    result = runner.invoke(app, ["news-ingest"])
    assert result.exit_code != 0
    assert "GDELT_API_KEY" in result.output


@pytest.mark.parametrize("days", ["0", "31"])
def test_the_command_refuses_a_window_the_api_caps(
    days: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GDELT_API_KEY", "test-key")
    result = runner.invoke(app, ["news-ingest", "--days", days])
    assert result.exit_code != 0
    assert "30 days" in result.output
