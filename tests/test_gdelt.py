"""S10: the GDELT DOC 2.0 client.

No network: every exchange goes through `httpx.MockTransport`, answering from the
fixtures in `tests/fixtures/gdelt/`. Those fixtures are hand-written to the documented
`artlist` shape rather than recorded, because what a live taxonomy query returns depends
on the week it is run; `record_fixtures` is how a session replaces them with real ones.
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import GDELT_FIXTURES
from conftest import load_gdelt_body as load_fixture

from flight_detective.news.gdelt import (
    ENDPOINT,
    MAX_DAYS,
    TAXONOMY,
    Article,
    GdeltClient,
    GdeltError,
    GdeltRateLimited,
    GdeltResponseInvalid,
    GdeltTransportError,
    _main,
    fixture_path,
    parse_articles,
    query_params,
    record_fixtures,
    seen_at,
    seen_date,
)

Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler, **kwargs: Any) -> GdeltClient:
    """A client whose HTTP goes to `handler` and whose sleeps are instant."""
    kwargs.setdefault("sleep", lambda _seconds: None)
    return GdeltClient(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def answering(body: Any, status: int = 200) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


# --- taxonomy ---------------------------------------------------------------------------


def test_taxonomy_covers_the_three_brainstorm_groups() -> None:
    assert {entry.category for entry in TAXONOMY} == {
        "regulatory",
        "airspace_disruption",
        "pilgrimage_visas",
    }


def test_taxonomy_slugs_are_unique_so_fixtures_do_not_overwrite_each_other() -> None:
    slugs = [entry.slug for entry in TAXONOMY]
    assert len(set(slugs)) == len(slugs)


def test_every_taxonomy_query_has_a_fixture() -> None:
    for entry in TAXONOMY:
        assert fixture_path(GDELT_FIXTURES, entry).is_file()


def test_taxonomy_keeps_the_brainstorm_phrases_verbatim() -> None:
    queries = " ".join(entry.query for entry in TAXONOMY)
    for phrase in (
        "EASA",
        "PIA",
        "bilateral air service",
        "traffic rights Pakistan",
        "civil aviation authority",
        "Middle East airspace",
        "Gulf Air route disruption",
        "CDG strike",
        "Doha transit delay",
        "Umrah quota",
        "Hajj flight schedule",
        "Saudi transit visa",
    ):
        assert f'"{phrase}"' in queries


# --- request ----------------------------------------------------------------------------


def test_query_params_ask_for_a_relative_artlist_window() -> None:
    params = query_params('"CDG strike"', 7)
    assert params == {
        "query": '"CDG strike"',
        "mode": "artlist",
        "format": "json",
        "timespan": "7d",
        "maxrecords": "250",
        "sort": "datedesc",
    }


@pytest.mark.parametrize("days", [0, -1, MAX_DAYS + 1])
def test_query_params_rejects_a_window_gdelt_does_not_index(days: int) -> None:
    with pytest.raises(ValueError, match="days must be between"):
        query_params('"CDG strike"', days)


def test_search_sends_the_query_to_the_doc_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=load_fixture("airspace-disruption"))

    client_for(handler).search('"CDG strike"', 7)
    assert str(seen[0].url).startswith(ENDPOINT)
    assert seen[0].url.params["query"] == '"CDG strike"'
    assert seen[0].url.params["timespan"] == "7d"


# --- parsing ----------------------------------------------------------------------------


def test_parses_the_recorded_article_list() -> None:
    articles = parse_articles(load_fixture("regulatory-easa-pia"))
    assert [a.domain for a in articles] == ["reuters.com", "dawn.com", "aviacionline.com"]
    assert articles[0].title == "EASA lifts its ban on PIA flights to Europe - Reuters"
    assert articles[0].url.startswith("https://www.reuters.com/")


def test_an_empty_body_is_no_matches_not_an_error() -> None:
    assert parse_articles(load_fixture("empty")) == []


def test_articles_without_a_headline_are_dropped_not_fatal() -> None:
    articles = parse_articles(load_fixture("pilgrimage-visas"))
    assert [a.domain for a in articles] == ["arab.news"]


def test_unknown_fields_are_ignored() -> None:
    body = {
        "articles": [
            {
                "url": "https://example.test/a",
                "title": "Something",
                "seendate": "20260901T000000Z",
                "domain": "example.test",
                "somethingnew": 1,
            }
        ]
    }
    assert parse_articles(body)[0].url == "https://example.test/a"


def test_a_response_that_is_not_an_article_list_is_invalid() -> None:
    with pytest.raises(GdeltResponseInvalid, match="not an article list"):
        parse_articles({"articles": "nope"})


def test_a_malformed_seendate_is_invalid() -> None:
    with pytest.raises(GdeltResponseInvalid, match="seendate"):
        parse_articles({"articles": [{"url": "u", "title": "t", "seendate": "2026-09-01 12:00"}]})


def test_seen_at_is_utc_and_seen_date_is_the_paris_day() -> None:
    # 22:30 UTC on 1 September is already 2 September in Paris (CEST, UTC+2).
    article = Article(url="u", title="t", seendate="20260901T223000Z")
    assert seen_at(article) == datetime(2026, 9, 1, 22, 30, tzinfo=UTC)
    assert seen_date(article) == date(2026, 9, 2)


# --- failures ---------------------------------------------------------------------------


def test_rate_limiting_is_surfaced_not_swallowed() -> None:
    with pytest.raises(GdeltRateLimited):
        client_for(answering({}, status=429)).search('"CDG strike"', 7)


def test_a_4xx_is_a_gdelt_error() -> None:
    with pytest.raises(GdeltError, match="HTTP 400"):
        client_for(answering({}, status=400)).search('"CDG strike"', 7)


def test_plain_text_from_gdelt_is_reported_with_its_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Your query was too short or too long.")

    with pytest.raises(GdeltResponseInvalid, match="too short or too long"):
        client_for(handler).search('"a"', 7)


def test_a_non_object_body_is_invalid() -> None:
    with pytest.raises(GdeltResponseInvalid, match="not a JSON object"):
        client_for(answering([1, 2, 3])).search('"CDG strike"', 7)


def test_a_transport_error_is_retried_once_then_raised() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("no route to host")

    with pytest.raises(GdeltTransportError, match="could not reach GDELT"):
        client_for(handler).search('"CDG strike"', 7)
    assert len(attempts) == 2


def test_a_5xx_is_retried_once_and_succeeds_on_the_retry() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=load_fixture("regulatory-traffic-rights"))

    articles = client_for(handler).search('"bilateral air service"', 7)
    assert len(attempts) == 2
    assert [a.domain for a in articles] == ["flightglobal.com"]


def test_a_persistent_5xx_becomes_a_gdelt_error() -> None:
    with pytest.raises(GdeltError, match="server error"):
        client_for(answering({}, status=503)).search('"CDG strike"', 7)


# --- fixture recording ------------------------------------------------------------------


def test_record_fixtures_writes_one_file_per_query_and_paces_them(tmp_path: Path) -> None:
    pauses: list[float] = []
    client = client_for(answering(load_fixture("empty")), sleep=pauses.append)
    written = record_fixtures(client, 7, tmp_path)
    assert [p.name for p in written] == [f"{entry.slug}.json" for entry in TAXONOMY]
    assert json.loads(written[0].read_text()) == {}
    # A courtesy pause between requests, but not before the first one.
    assert len(pauses) == len(TAXONOMY) - 1


def test_recorder_main_reports_a_failure_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(*args: Any, **kwargs: Any) -> None:
        raise GdeltError("GDELT is rate-limiting this caller")

    monkeypatch.setattr("flight_detective.news.gdelt.record_fixtures", explode)
    assert _main(["7"]) == 1
    assert "rate-limiting" in capsys.readouterr().err


def test_recorder_main_writes_where_it_is_told(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "flight_detective.news.gdelt.GdeltClient",
        lambda: client_for(answering(load_fixture("empty"))),
    )
    assert _main(["7", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        f"{entry.slug}.json" for entry in TAXONOMY
    )
    assert str(tmp_path) in capsys.readouterr().out


def test_recorder_main_rejects_extra_arguments() -> None:
    assert _main(["7", "somewhere", "extra"]) == 2
