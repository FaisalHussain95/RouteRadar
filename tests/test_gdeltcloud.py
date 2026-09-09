"""S17: the GDELT Cloud client.

No network anywhere: every request is answered by an `httpx.MockTransport`, either from a
recorded fixture under `tests/fixtures/gdeltcloud/` or from a hand-built body when the
shape being tested (a cursor walk, an error envelope) is one the recorder cannot produce
on demand.

The recorded bodies are the point of most of this file. They are what the API actually
returned on 2026-09-09, so the assertions below are about real data — nullable article
titles, a semantic pool of 100 near-misses, `applied_filters` blocks carrying keys nobody
sent — rather than about what the docs imply.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from flight_detective.config import Settings
from flight_detective.news.gdeltcloud import (
    AVIATION_TERMS,
    CARRIERS,
    MIN_SEARCH_SCORE,
    PAGE_LIMIT,
    RELEVANCE_KEYWORDS,
    SEMANTIC_QUERIES,
    GdeltCloudAuthError,
    GdeltCloudClient,
    GdeltCloudError,
    GdeltCloudKeyMissing,
    GdeltCloudQuotaError,
    GdeltCloudRateLimited,
    GdeltCloudResponseInvalid,
    GdeltCloudTransportError,
    Story,
    check_applied_filters,
    headline,
    is_relevant,
    keep_search_hit,
    parse_page,
    source_url,
    titles,
)

FIXTURES = Path(__file__).parent / "fixtures" / "gdeltcloud"

CARRIER_SLUGS = [f"carrier-{c.name.lower().replace(' ', '-')}" for c in CARRIERS]
SEMANTIC_SLUGS = [q.slug for q in SEMANTIC_QUERIES]
ALL_SLUGS = CARRIER_SLUGS + SEMANTIC_SLUGS


def body(slug: str) -> dict[str, Any]:
    loaded = json.loads((FIXTURES / f"{slug}.json").read_text())
    assert isinstance(loaded, dict)
    return loaded


def story(**overrides: Any) -> Story:
    """A minimal valid Story, so a test can vary the one field it is about."""
    base: dict[str, Any] = {
        "id": "abc123",
        "title": "PIA suspends its Paris service",
        "story_date": "2026-09-08",
        "metrics": {"significance": 0.0753, "article_count": 1},
        "top_articles": [{"url": "https://example.test/a", "title": "", "domain": "example.test"}],
    }
    return Story.model_validate(base | overrides)


def client_for(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any
) -> GdeltCloudClient:
    return GdeltCloudClient(
        "test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
        **kwargs,
    )


# --- The recorded fixtures ------------------------------------------------------------


def test_every_query_has_a_recorded_fixture() -> None:
    """One recording per daily call: six carriers and three semantic groups. A query added
    without a fixture would otherwise be tested by nothing at all."""
    assert sorted(p.stem for p in FIXTURES.glob("*.json") if not p.stem.startswith("resolve-")) == (
        sorted(ALL_SLUGS)
    )


def test_every_carrier_has_a_recorded_resolution() -> None:
    """`CARRIERS` commits an opaque id per airline; the `/search` body it was chosen from
    is committed beside it so the choice can be re-argued without spending a query unit."""
    for carrier in CARRIERS:
        recorded = body(f"resolve-{carrier.name.lower().replace(' ', '-')}")
        assert recorded["query"] == carrier.resolved_from
        assert carrier.entity_id in {row["entity_id"] for row in recorded["data"]}


def test_every_carrier_records_why_that_candidate() -> None:
    """The note is the whole value of committing the id rather than resolving at run time."""
    for carrier in CARRIERS:
        assert carrier.note.strip()
        assert carrier.resolved_from.strip()


@pytest.mark.parametrize("slug", ALL_SLUGS)
def test_recorded_bodies_parse(slug: str) -> None:
    """Every recording goes through `parse_page` intact. This is the regression test for
    nullable strings: three rows of `airspace-disruption` carry an article with
    `title: null` and `domain: null`, which rejected the whole page before `Text` coerced
    them, failing 100 stories over one incomplete article."""
    stories, _cursor = parse_page(body(slug))
    assert len(stories) == len(body(slug)["data"])


def test_a_recorded_article_really_does_have_a_null_title() -> None:
    """Pins the case above to the data, so re-recording a week without it cannot quietly
    turn `test_recorded_bodies_parse` into a test of nothing."""
    articles = [a for row in body("airspace-disruption")["data"] for a in row["top_articles"]]
    assert any(a["title"] is None for a in articles)


@pytest.mark.parametrize("slug", ALL_SLUGS)
def test_recorded_bodies_pass_the_applied_filters_guard(slug: str) -> None:
    """The recordings are what a passing echo looks like, including the extra keys the
    server adds unasked (`entity_match`, `entity_family`, `linkage`)."""
    recorded = body(slug)
    applied = recorded["applied_filters"]
    sent = {"days": "7", "sort": "recent", "languages": "en,fr"}
    for key in ("search", "entity"):
        if applied.get(key):
            sent[key] = applied[key]
    check_applied_filters(recorded, sent)


# --- The semantic-query trap ----------------------------------------------------------


@pytest.mark.parametrize("query", SEMANTIC_QUERIES, ids=lambda q: q.slug)
def test_semantic_queries_carry_no_conjunction(query: Any) -> None:
    """`search` is embedded, not parsed: a string containing `and`/`or` is truncated to its
    first term, silently, with HTTP 200 and an empty `ignored`. Measured on 2026-09-09 —
    "Hajj and Umrah pilgrimage flight quotas and Saudi transit visa rules" came back having
    searched "Hajj" alone. One concept per query is the rule that avoids it."""
    words = query.search.casefold().split()
    assert "and" not in words and "or" not in words, query.search


@pytest.mark.parametrize("slug", SEMANTIC_SLUGS)
def test_recorded_semantic_queries_were_echoed_verbatim(slug: str) -> None:
    """Proof the phrasings above are not merely conjunction-free but actually survived the
    server's term splitter, and that it did not add a `note` about dropped terms."""
    recorded = body(slug)
    sent = next(q.search for q in SEMANTIC_QUERIES if q.slug == slug)
    assert recorded["applied_filters"]["search"] == sent
    assert recorded.get("note") is None


def test_a_rewritten_search_is_an_error_not_an_empty_week() -> None:
    """The failure the rule above prevents, asserted through the guard that catches it if
    the rule is ever broken: the server answers 200, `ignored` is empty, and only the echo
    reveals that a different question was answered."""
    recorded = body("airspace-disruption")
    recorded["applied_filters"] = recorded["applied_filters"] | {"search": "airspace"}
    with pytest.raises(GdeltCloudResponseInvalid, match="was applied as"):
        check_applied_filters(recorded, {"search": "airspace closure"})


def test_a_dropped_filter_is_an_error() -> None:
    with pytest.raises(GdeltCloudResponseInvalid, match="not applied"):
        check_applied_filters({"applied_filters": {"days": "7"}}, {"entity": "e_1"})


def test_an_ignored_filter_is_an_error() -> None:
    with pytest.raises(GdeltCloudResponseInvalid, match="ignored filters"):
        check_applied_filters(
            {"applied_filters": {"days": "7", "ignored": {"entty": "typo"}}}, {"days": "7"}
        )


def test_a_comma_list_echoed_as_an_array_matches() -> None:
    """`languages=en,fr` comes back as `["en", "fr"]`; comparing wire forms would fail."""
    check_applied_filters(
        {"applied_filters": {"languages": ["fr", "en"], "ignored": {}}}, {"languages": "en,fr"}
    )


def test_a_body_without_applied_filters_is_an_error() -> None:
    with pytest.raises(GdeltCloudResponseInvalid, match="no applied_filters"):
        check_applied_filters({"data": []}, {"days": "7"})


# --- Paging ---------------------------------------------------------------------------


def test_the_cursor_is_the_truncation_signal_not_the_row_count() -> None:
    """A full page with `next_cursor: null` is the last page. Inferring "there is more"
    from `len(data) == limit` would spend a unit on an empty follow-up every time."""
    stories, cursor = parse_page(
        {
            "data": [story(id=str(i)).model_dump(mode="json") for i in range(PAGE_LIMIT)],
            "pagination": {"next_cursor": None},
        }
    )
    assert len(stories) == PAGE_LIMIT
    assert cursor is None


def test_a_short_page_can_still_carry_a_cursor() -> None:
    _stories, cursor = parse_page({"data": [], "pagination": {"next_cursor": "100"}})
    assert cursor == "100"


def test_a_walk_follows_the_cursor_to_the_end() -> None:
    pages = [
        {"data": [story(id="a").model_dump(mode="json")], "pagination": {"next_cursor": "1"}},
        {"data": [story(id="b").model_dump(mode="json")], "pagination": {"next_cursor": None}},
    ]
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("cursor"))
        page = pages[len(seen) - 1]
        return httpx.Response(200, json=page | {"applied_filters": _echo(request)})

    walk = client_for(handler).stories_for_carrier(CARRIERS[0], 7)
    assert [s.id for s in walk.stories] == ["a", "b"]
    assert seen == [None, "1"]
    assert walk.truncated is False


def test_a_walk_stops_at_the_row_cap_and_says_so() -> None:
    """`MAX_ROWS` is a budget guard, not a silent truncation: a query that runs away is
    reported so it can be narrowed rather than paid for every day."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    story(id=f"{request.url.params.get('cursor')}-{i}").model_dump(mode="json")
                    for i in range(2)
                ],
                "pagination": {"next_cursor": "more"},
                "applied_filters": _echo(request),
            },
        )

    walk = client_for(handler, max_rows=4).stories_for_carrier(CARRIERS[0], 7)
    assert walk.truncated is True
    assert len(walk.stories) == 4


def test_every_page_of_a_walk_is_checked_for_the_filter_echo() -> None:
    """A cursor page that quietly widened the query is the same bug as a first page that
    did, so the echo is asserted on each one rather than only on the first."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        applied = _echo(request)
        if calls["n"] == 2:
            applied = applied | {"entity": "someone-else"}
        return httpx.Response(
            200,
            json={
                "data": [],
                "pagination": {"next_cursor": "1" if calls["n"] == 1 else None},
                "applied_filters": applied,
            },
        )

    with pytest.raises(GdeltCloudResponseInvalid, match="was applied as"):
        client_for(handler).stories_for_carrier(CARRIERS[0], 7)


def test_a_non_string_cursor_is_an_error() -> None:
    with pytest.raises(GdeltCloudResponseInvalid, match="next_cursor"):
        parse_page({"data": [], "pagination": {"next_cursor": 100}})


def test_a_body_without_pagination_is_an_error() -> None:
    with pytest.raises(GdeltCloudResponseInvalid, match="no pagination"):
        parse_page({"data": []})


# --- The relevance guard --------------------------------------------------------------


def test_a_place_alone_does_not_make_a_story_relevant() -> None:
    """The measurement behind the two-axis guard: a country name appears in every kind of
    story filed from that country, so on its own it selects a place, not a topic."""
    assert is_relevant(story(title="Pakistan tenders for wheat imports")) is None
    assert is_relevant(story(title="Eiffel Tower strike over restrictions in Paris")) is None


def test_a_place_plus_an_aviation_word_is_relevant() -> None:
    kept = is_relevant(story(title="Pakistan halts all flights from Islamabad"))
    assert kept is not None and kept.word == "pakistan"


def test_a_subject_alone_is_relevant() -> None:
    """A scope carrier or a flying topic needs no second word: a story naming PIA is about
    PIA whatever else it says."""
    kept = is_relevant(story(title="PIA seeks new leadership and staff"))
    assert kept is not None and kept.word == "pia"


def test_a_subject_match_wins_over_a_place_match() -> None:
    """`impact_note` prints the reason a row was kept, so the most specific one must win."""
    kept = is_relevant(story(title="Airspace closed over Pakistan"))
    assert kept is not None and kept.word == "airspace"


def test_an_irrelevant_story_is_dropped() -> None:
    assert is_relevant(story(title="Local council debates parking charges")) is None


def test_the_guard_reads_article_titles_as_well_as_the_story_title() -> None:
    """A cluster title is a generated summary and can omit the word its sources use."""
    kept = is_relevant(
        story(
            title="Carrier announces schedule change",
            top_articles=[{"url": "https://x.test/1", "title": "PIA cuts its Paris route"}],
        )
    )
    assert kept is not None and kept.word == "pia"


def test_matching_is_word_bounded() -> None:
    """'PIA' inside 'Olympia' and 'vol' inside 'volume' are the false positives a plain
    substring test would produce on a two- or three-letter keyword."""
    assert is_relevant(story(title="Olympia hosts a trade show")) is None
    assert is_relevant(story(title="Pakistan trading volume rises")) is None


def test_matching_is_case_insensitive() -> None:
    assert is_relevant(story(title="pia GROUNDS its fleet")) is not None


def test_a_keyword_can_reclassify_the_story() -> None:
    """A PIA story about a closed airspace is an airspace story, not a regulatory one, and
    that is how a carrier query's rows get out of the default category."""
    kept = is_relevant(story(title="Airspace closed, PIA reroutes"))
    assert kept is not None and kept.category == "airspace_disruption"


def test_every_keyword_documents_why_it_is_on_the_list() -> None:
    for keyword in RELEVANCE_KEYWORDS:
        assert keyword.why.strip(), keyword.word
        assert keyword.word == keyword.word.casefold(), keyword.word
        assert keyword.axis in ("subject", "place")


def test_the_guard_uses_both_axes() -> None:
    """A list that drifted to all-subject would silently become the old one-word guard."""
    axes = {k.axis for k in RELEVANCE_KEYWORDS}
    assert axes == {"subject", "place"}
    assert "carrier" not in AVIATION_TERMS  # an aircraft carrier is a warship


def test_the_guard_cuts_the_recorded_week_to_aviation_news() -> None:
    """The end-to-end measurement, pinned. 300 recorded rows, of which the guard keeps a
    couple of dozen; a change that lets hundreds through has broken the anti-noise
    argument the two-arm design exists for, whatever the unit tests say."""
    kept = []
    for slug in ALL_SLUGS:
        stories, _ = parse_page(body(slug))
        pool = [s for s in stories if keep_search_hit(s)] if slug in SEMANTIC_SLUGS else stories
        kept += [s for s in pool if is_relevant(s)]
    assert 5 <= len(kept) <= 40, len(kept)


# --- Search scoring -------------------------------------------------------------------


def test_a_name_match_is_thresholded_like_any_other() -> None:
    """`match_type=name` is a *lexical* hit, not a verified one, so it gets no exemption.

    Measured on 2026-09-09, when `cdg-strikes` was still being truncated to the token
    "strike": that body was 100 `name` rows scoring 0.25-0.36, the word matched in stories
    about football and politics. The committed fixtures no longer contain a `name` row —
    the corrected one-concept queries all return `semantic` — so this is a behavioural
    test, not a fixture test. The rule stays because `name` is a shape the API returns."""
    assert keep_search_hit(story(match_type="name", search_score=MIN_SEARCH_SCORE - 0.01)) is False
    assert keep_search_hit(story(match_type="name", search_score=MIN_SEARCH_SCORE)) is True


def test_a_missing_score_is_dropped_not_kept() -> None:
    """A null score is unmeasured, and an unmeasured row has cleared no bar."""
    assert keep_search_hit(story(match_type="semantic", search_score=None)) is False


def test_the_threshold_keeps_the_recorded_pools() -> None:
    """The evidence for `MIN_SEARCH_SCORE`, pinned tightly enough to catch drift.

    The three committed pools are 300 scored rows over 0.2943-0.4303, so 0.20 keeps all of
    them and leaves the decision to the keyword guard. The bounds are asserted to three
    decimals rather than loosely, because this threshold is the one number in the module a
    reviewer has to take on trust: a re-recording that moved the range would otherwise slip
    through and silently change what the guard is handed."""
    scores = [
        s.search_score
        for slug in SEMANTIC_SLUGS
        for s in parse_page(body(slug))[0]
        if s.search_score is not None
    ]
    assert len(scores) == 300
    assert min(scores) == pytest.approx(0.2943, abs=5e-5)
    assert max(scores) == pytest.approx(0.4303, abs=5e-5)
    assert min(scores) > MIN_SEARCH_SCORE


def test_the_rejected_threshold_would_have_dropped_every_recorded_row() -> None:
    """Why 0.55 was rejected. It is not that it was slightly tight — it keeps nothing at
    all, which is the failure mode that would have read as "GDELT has no news this week"
    every single day."""
    kept = [
        s
        for slug in SEMANTIC_SLUGS
        for s in parse_page(body(slug))[0]
        if (s.search_score or 0.0) >= 0.55
    ]
    assert kept == []


def test_search_hits_are_filtered_but_entity_hits_are_not() -> None:
    """An entity row has no score to threshold — it matched an id, not a phrase."""
    low = story(match_type="semantic", search_score=0.01).model_dump(mode="json")
    page = {"data": [low], "pagination": {"next_cursor": None}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page | {"applied_filters": _echo(request)})

    assert client_for(handler).stories_for_search(SEMANTIC_QUERIES[0], 7).stories == []
    assert len(client_for(handler).stories_for_carrier(CARRIERS[0], 7).stories) == 1


# --- Field mapping --------------------------------------------------------------------


def test_source_url_prefers_the_top_article() -> None:
    assert source_url(story()) == "https://example.test/a"


def test_source_url_falls_back_to_the_story_page() -> None:
    """A cluster with no usable article link still has to be clickable in the feed."""
    assert source_url(story(top_articles=[])).endswith("/stories/abc123")


def test_headline_falls_back_to_the_best_article_title() -> None:
    assert (
        headline(
            story(title=None, top_articles=[{"url": "https://x.test/1", "title": "PIA grounded"}])
        )
        == "PIA grounded"
    )


def test_titles_skips_empties() -> None:
    assert titles(story(title="a", top_articles=[{"url": "u", "title": "  "}])) == ["a"]


def test_a_null_metric_is_unmeasured_not_zero() -> None:
    parsed = story(metrics={"significance": None, "article_count": 4})
    assert parsed.metrics.significance is None
    assert parsed.metrics.article_count == 4


def test_unknown_fields_are_ignored() -> None:
    """New response fields ship on this API without notice; one must not fail a run."""
    assert story(some_new_field_2027={"a": 1}).id == "abc123"


def test_a_page_that_is_not_a_story_list_is_an_error() -> None:
    with pytest.raises(GdeltCloudResponseInvalid, match="not a story page"):
        parse_page({"data": [{"no_id": True}], "pagination": {"next_cursor": None}})


# --- Errors ---------------------------------------------------------------------------


def test_a_missing_key_is_one_actionable_line() -> None:
    with pytest.raises(GdeltCloudKeyMissing, match="GDELT_API_KEY is not set"):
        GdeltCloudClient.from_settings(
            Settings(
                db_path=Path("x"), serpapi_key=None, gdelt_api_key=None, site_export_path=Path("y")
            )
        )


def test_a_key_is_sent_as_a_bearer_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(
            200, json={"data": [], "pagination": {}, "applied_filters": _echo(request)}
        )

    client_for(handler).stories_for_carrier(CARRIERS[0], 7)
    assert seen == ["Bearer test-key"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_raises_auth_error(status: int) -> None:
    client = client_for(lambda _r: httpx.Response(status, json={"error": {"code": "UNAUTHORIZED"}}))
    with pytest.raises(GdeltCloudAuthError):
        client.query_units()


def test_rate_limited_waits_the_hinted_delay_and_retries_once() -> None:
    """`RATE_LIMITED` is "slow down", so it is worth one wait — and the server says how
    long, which beats guessing."""
    slept: list[float] = []
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                json={"error": {"code": "RATE_LIMITED", "details": {"retry_after": 7}}},
            )
        return httpx.Response(
            200,
            json={"usage": {"plan": "Explore", "remaining": 900, "allowance": {"effective": 1000}}},
        )

    client = GdeltCloudClient(
        "k", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=slept.append
    )
    assert client.query_units().remaining == 900
    assert slept == [7.0]


def test_rate_limited_twice_gives_up() -> None:
    client = client_for(lambda _r: httpx.Response(429, json={"error": {"code": "RATE_LIMITED"}}))
    with pytest.raises(GdeltCloudRateLimited):
        client.query_units()


def test_quota_exceeded_is_a_distinct_error_and_is_not_retried() -> None:
    """The two 429s mean opposite things. Retrying an exhausted month burns wall clock to
    learn nothing, so it must branch on the body's code, never on the status."""
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"code": "QUOTA_EXCEEDED", "message": "gone"}})

    with pytest.raises(GdeltCloudQuotaError):
        client_for(handler).query_units()
    assert calls["n"] == 1


def test_a_server_error_is_retried_once_then_raised() -> None:
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="upstream down")

    with pytest.raises(GdeltCloudError):
        client_for(handler).query_units()
    assert calls["n"] == 2


def test_a_server_error_that_clears_on_the_retry_succeeds() -> None:
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200, json={"usage": {"plan": "Explore", "remaining": 5, "allowance": {"effective": 10}}}
        )

    assert client_for(handler).query_units().remaining == 5


def test_a_transport_failure_is_retried_once_then_raised() -> None:
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("no route to host")

    with pytest.raises(GdeltCloudTransportError):
        client_for(handler).query_units()
    assert calls["n"] == 2


def test_a_client_error_is_not_retried() -> None:
    """4xx is a wrong request; asking again changes nothing."""
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"code": "BAD_REQUEST", "message": "nope"}})

    with pytest.raises(GdeltCloudError, match="HTTP 400"):
        client_for(handler).query_units()
    assert calls["n"] == 1


def test_a_non_json_body_is_an_error_carrying_the_text() -> None:
    client = client_for(lambda _r: httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(GdeltCloudResponseInvalid, match="gateway"):
        client.query_units()


# --- Budget ---------------------------------------------------------------------------


def test_query_units_reads_the_usage_block() -> None:
    client = client_for(
        lambda _r: httpx.Response(
            200,
            json={
                "usage": {
                    "plan_display_name": "Explore",
                    "remaining": 1010,
                    "allowance": {"effective": 1030},
                }
            },
        )
    )
    units = client.query_units()
    assert (units.plan, units.remaining, units.effective) == ("Explore", 1010, 1030)
    assert units.is_low is False


def test_a_low_budget_is_flagged() -> None:
    client = client_for(
        lambda _r: httpx.Response(
            200,
            json={"usage": {"plan": "Explore", "remaining": 20, "allowance": {"effective": 1030}}},
        )
    )
    assert client.query_units().is_low is True


def test_an_unreadable_usage_block_is_an_error() -> None:
    client = client_for(lambda _r: httpx.Response(200, json={"usage": {"plan": "Explore"}}))
    with pytest.raises(GdeltCloudResponseInvalid, match="not readable"):
        client.query_units()


# --- Window -------------------------------------------------------------------------


@pytest.mark.parametrize("days", [0, 31, -1])
def test_an_out_of_range_window_is_refused_before_it_is_paid_for(days: int) -> None:
    """The API caps a story window at 30 inclusive days. Sending 90 would cost a unit to
    be told so, or worse, silently answer for 30."""
    client = client_for(lambda _r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="days must be between"):
        client.stories_for_carrier(CARRIERS[0], days)


def test_the_window_filters_are_sent_on_every_query() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(
            200, json={"data": [], "pagination": {}, "applied_filters": _echo(request)}
        )

    client_for(handler).stories_for_carrier(CARRIERS[0], 7)
    assert seen[0]["days"] == "7"
    assert seen[0]["sort"] == "recent"
    assert seen[0]["languages"] == "en,fr"
    assert seen[0]["limit"] == str(PAGE_LIMIT)
    assert seen[0]["entity"] == CARRIERS[0].entity_id


def _echo(request: httpx.Request) -> dict[str, Any]:
    """`applied_filters` as a well-behaved server would return it for this request."""
    params = dict(request.url.params)
    applied: dict[str, Any] = {"ignored": {}}
    for key, value in params.items():
        if key in ("limit", "cursor"):
            continue
        applied[key] = value.split(",") if key == "languages" else value
    return applied
