"""S08: the SerpApi Google Flights provider.

No network: every HTTP exchange goes through `httpx.MockTransport`, and the mapping tests
read the recorded fixture at `tests/fixtures/serpapi/CDG-ISB-2026-12-20.json`. The
expected values below were read off that file by hand, so re-recording it means
updating them; the fixture-integrity tests at the end are what make a stale re-recording
fail loudly rather than pass on different data.
"""

import json
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from flight_detective import cli, db
from flight_detective.cli import app
from flight_detective.config import Settings
from flight_detective.ingest import ROUTES, run_ingest
from flight_detective.models import Itinerary, Route
from flight_detective.providers import serpapi
from flight_detective.providers.base import FareProvider, ProviderError
from flight_detective.providers.filters import apply_scope
from flight_detective.providers.serpapi import (
    DEFAULT_FIXTURES_DIR,
    ENDPOINT,
    SerpApiAuthError,
    SerpApiError,
    SerpApiFareProvider,
    SerpApiKeyMissing,
    SerpApiQuotaError,
    SerpApiResponseInvalid,
    SerpApiTransportError,
    _main,
    fixture_path,
    parse_response,
    record_fixture,
    strip_archive_urls,
)

runner = CliRunner()

CDG_ISB = Route(origin="CDG", destination="ISB")
DEPARTURE = date(2026, 12, 20)
KEY = "sk-test-0123456789abcdef"
FIXTURE = fixture_path(DEFAULT_FIXTURES_DIR, CDG_ISB, DEPARTURE)
SEARCH_ID = "6aa0a67073bf59e675609d77"

Handler = Callable[[httpx.Request], httpx.Response]


def load_fixture() -> dict[str, Any]:
    body = json.loads(FIXTURE.read_text())
    assert isinstance(body, dict)
    return body


class Recorder:
    """A MockTransport handler that replays a scripted list of responses and remembers
    every request, so tests can assert on retries and on the parameters sent."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.script: Iterator[httpx.Response | Exception] = iter(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = next(self.script)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def provider_with(
    *responses: httpx.Response | Exception, sleeps: list[float] | None = None
) -> tuple[SerpApiFareProvider, Recorder]:
    recorder = Recorder(*responses)
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    provider = SerpApiFareProvider(
        KEY, client=client, sleep=(sleeps.append if sleeps is not None else lambda _: None)
    )
    return provider, recorder


def ok(body: Any) -> httpx.Response:
    return httpx.Response(200, json=body)


def error_body(message: str) -> dict[str, Any]:
    return {"search_metadata": {"id": SEARCH_ID, "status": "Error"}, "error": message}


# --- Protocol -----------------------------------------------------------------------


def test_serpapi_satisfies_the_provider_protocol() -> None:
    provider, _ = provider_with()
    checked: FareProvider = provider
    assert isinstance(checked, FareProvider)
    assert provider.name == "serpapi"


def test_every_serpapi_failure_is_a_provider_error() -> None:
    for exc in (
        SerpApiKeyMissing,
        SerpApiAuthError,
        SerpApiQuotaError,
        SerpApiTransportError,
        SerpApiResponseInvalid,
    ):
        assert issubclass(exc, SerpApiError)
        assert issubclass(exc, ProviderError)


# --- Acceptance: mapping test passes on the recorded fixture; no network in tests -------


def test_fixture_maps_to_four_economy_itineraries() -> None:
    results = parse_response(load_fixture(), CDG_ISB, DEPARTURE)

    assert [(i.carrier, i.flight_numbers) for i in results] == [
        ("PK", ["PK750"]),
        ("J2", ["J274", "J2143"]),
        ("TK", ["TK1824", "TK710"]),
        ("TK", ["TK1834", "TK750"]),
    ]
    assert [i.stops for i in results] == [0, 1, 1, 1]
    assert [i.layover_minutes for i in results] == [0, 150, 120, 130]
    assert [i.duration_minutes for i in results] == [465, 645, 660, 660]
    assert [i.price_eur for i in results] == [Decimal(p) for p in (609, 638, 639, 639)]
    assert all(isinstance(i.price_eur, Decimal) for i in results)
    assert all(i.cabin == "economy" for i in results)
    assert all(i.provider == "serpapi" for i in results)
    assert all(i.route == CDG_ISB and i.departure_date == DEPARTURE for i in results)


def test_raw_ref_is_serpapis_search_id_plus_position() -> None:
    results = parse_response(load_fixture(), CDG_ISB, DEPARTURE)
    assert [i.raw_ref for i in results] == [f"serpapi:{SEARCH_ID}#other:{n}" for n in range(4)]


def test_a_second_leg_departing_the_next_day_is_still_the_requested_departure() -> None:
    # TK 1834 leaves CDG on the 20th and its connection leaves IST on the 21st; only the
    # first leg's date is the departure date.
    results = parse_response(load_fixture(), CDG_ISB, DEPARTURE)
    assert results[3].flight_numbers == ["TK1834", "TK750"]
    assert results[3].departure_date == DEPARTURE


def test_the_recorded_itineraries_are_all_in_scope() -> None:
    kept, dropped = apply_scope(parse_response(load_fixture(), CDG_ISB, DEPARTURE))
    assert (len(kept), dropped) == (4, 0)


def test_search_sends_the_pinned_parameters_and_maps_the_reply() -> None:
    provider, recorder = provider_with(ok(load_fixture()))

    results = provider.search(CDG_ISB, DEPARTURE)

    assert len(results) == 4
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.method == "GET"
    assert str(request.url).startswith(ENDPOINT)
    assert dict(request.url.params) == {
        "engine": "google_flights",
        "api_key": KEY,
        "departure_id": "CDG",
        "arrival_id": "ISB",
        "outbound_date": "2026-12-20",
        "type": "2",
        "travel_class": "1",
        "adults": "1",
        "currency": "EUR",
        "hl": "fr",
        "gl": "fr",
    }


def test_best_and_other_flights_are_both_mapped() -> None:
    body = load_fixture()
    body["best_flights"] = [body["other_flights"].pop(0)]
    results = parse_response(body, CDG_ISB, DEPARTURE)
    assert [i.raw_ref for i in results] == [
        f"serpapi:{SEARCH_ID}#best:0",
        f"serpapi:{SEARCH_ID}#other:0",
        f"serpapi:{SEARCH_ID}#other:1",
        f"serpapi:{SEARCH_ID}#other:2",
    ]


def test_an_itinerary_without_a_price_is_skipped_not_an_error() -> None:
    body = load_fixture()
    del body["other_flights"][0]["price"]
    results = parse_response(body, CDG_ISB, DEPARTURE)
    assert [i.carrier for i in results] == ["J2", "TK", "TK"]


def test_google_no_results_is_an_empty_answer() -> None:
    body = error_body("Google hasn't returned any results for this query.")
    assert parse_response(body, CDG_ISB, DEPARTURE) == []


def test_a_float_price_is_rejected_not_coerced() -> None:
    body = load_fixture()
    body["other_flights"][0]["price"] = 609.99
    with pytest.raises(SerpApiResponseInvalid, match="price"):
        parse_response(body, CDG_ISB, DEPARTURE)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda o: o["flights"][0]["departure_airport"].__setitem__("id", "ORY"), "ORY->ISB"),
        (lambda o: o["flights"][-1]["arrival_airport"].__setitem__("id", "LHE"), "CDG->LHE"),
        (
            lambda o: o["flights"][0]["departure_airport"].__setitem__("time", "2026-12-21 18:30"),
            "departs 2026-12-21",
        ),
        (lambda o: o["flights"][0].__setitem__("flight_number", "PK750"), "flight_number"),
        (lambda o: o.__delitem__("total_duration"), "expected shape"),
    ],
)
def test_a_reply_that_disagrees_with_the_query_is_invalid(
    mutate: Callable[[dict[str, Any]], None], expected: str
) -> None:
    body = load_fixture()
    mutate(body["other_flights"][0])
    with pytest.raises(SerpApiResponseInvalid, match=expected):
        parse_response(body, CDG_ISB, DEPARTURE)


def test_non_json_and_non_object_replies_are_invalid() -> None:
    provider, _ = provider_with(httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(SerpApiResponseInvalid, match="not JSON"):
        provider.search(CDG_ISB, DEPARTURE)

    provider, _ = provider_with(ok([1, 2, 3]))
    with pytest.raises(SerpApiResponseInvalid, match="not a JSON object"):
        provider.search(CDG_ISB, DEPARTURE)


# --- Acceptance: missing key gives a one-line actionable error from `fd ingest` --------


def test_from_settings_without_a_key_names_the_variable_and_the_file() -> None:
    settings = Settings(
        db_path=Path("x.duckdb"), serpapi_key=None, gdelt_api_key=None, site_export_path=Path("x")
    )
    with pytest.raises(SerpApiKeyMissing) as excinfo:
        SerpApiFareProvider.from_settings(settings)
    assert "SERPAPI_KEY" in str(excinfo.value)
    assert ".env" in str(excinfo.value)


def test_an_empty_key_is_missing_too() -> None:
    with pytest.raises(SerpApiKeyMissing):
        SerpApiFareProvider("")


def test_ingest_without_a_key_fails_with_one_line_and_touches_nothing(
    isolated_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SERPAPI_KEY", raising=False)

    result = runner.invoke(app, ["ingest", "--provider", "serpapi"])

    assert result.exit_code == 2, result.output
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("error: SERPAPI_KEY is not set")
    assert ".env" in lines[0]
    assert "Traceback" not in result.output
    assert not isolated_db_path.exists()


def test_ingest_with_a_key_builds_the_serpapi_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    # Construction makes no request, so this is safe offline; the key comes from the
    # environment exactly as it would on the box.
    monkeypatch.setenv("SERPAPI_KEY", KEY)
    provider = cli.PROVIDERS["serpapi"]()
    assert isinstance(provider, SerpApiFareProvider)
    assert provider.name == "serpapi"


def test_provider_help_lists_serpapi() -> None:
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "serpapi" in result.output


# --- Acceptance: rate/quota errors are surfaced, not swallowed -----------------------------


def test_http_429_is_a_quota_error_and_is_not_retried() -> None:
    provider, recorder = provider_with(
        httpx.Response(429, json={"error": "Rate limit exceeded, slow down."})
    )
    with pytest.raises(SerpApiQuotaError, match="Rate limit"):
        provider.search(CDG_ISB, DEPARTURE)
    assert len(recorder.requests) == 1


def test_out_of_searches_is_a_quota_error_whatever_the_status() -> None:
    message = "Your account has run out of searches."
    provider, _ = provider_with(ok(error_body(message)))
    with pytest.raises(SerpApiQuotaError, match="run out of searches"):
        provider.search(CDG_ISB, DEPARTURE)
    with pytest.raises(SerpApiQuotaError):
        parse_response(error_body(message), CDG_ISB, DEPARTURE)


def test_http_401_is_an_auth_error_pointing_at_the_key() -> None:
    provider, recorder = provider_with(httpx.Response(401, json={"error": "Invalid API key"}))
    with pytest.raises(SerpApiAuthError, match="SERPAPI_KEY"):
        provider.search(CDG_ISB, DEPARTURE)
    assert len(recorder.requests) == 1


def test_other_serpapi_error_bodies_are_errors_not_empty_answers() -> None:
    with pytest.raises(SerpApiError, match="Unsupported"):
        parse_response(error_body("Unsupported `outbound_date` format."), CDG_ISB, DEPARTURE)


def test_quota_error_lands_in_ingest_run_error_and_the_command_exits_one(
    isolated_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = [httpx.Response(429, json={"error": "Rate limit exceeded"}) for _ in ROUTES]
    provider, recorder = provider_with(*responses)
    monkeypatch.setitem(cli.PROVIDERS, "serpapi", lambda: provider)

    result = runner.invoke(
        app, ["ingest", "--provider", "serpapi", "--observed-on", "2026-12-06", "--horizons", "14"]
    )

    assert result.exit_code == 1, result.output
    assert len(recorder.requests) == len(ROUTES)
    with db.connect(isolated_db_path) as conn:
        runs = db.fetch_ingest_runs(conn)
    assert len(runs) == 1
    assert runs[0].provider == "serpapi"
    assert runs[0].error is not None
    assert runs[0].error.count("quota or rate limit") == len(ROUTES)
    assert f"{len(ROUTES)} of {len(ROUTES)} queries failed" in result.stderr
    assert "Rate limit exceeded" in result.stderr


def test_one_quota_hit_costs_one_cell_not_the_run(isolated_db_path: Path) -> None:
    # CDG->ISB (the first route) is rate-limited; the other five answer "no results".
    no_results = ok(error_body("Google hasn't returned any results for this query."))
    provider, _ = provider_with(
        httpx.Response(429, json={"error": "Rate limit exceeded"}), *[no_results] * 5
    )
    with db.connect(isolated_db_path) as conn:
        db.init_schema(conn)
        run = run_ingest(conn, provider, observed_on=date(2026, 12, 6), horizons=[14])
    assert run.queries == 6
    assert run.error is not None
    assert run.error.splitlines() == [
        "CDG->ISB 2026-12-20: SerpApi quota or rate limit: Rate limit exceeded"
    ]


# --- Retries --------------------------------------------------------------------------------


def test_a_5xx_is_retried_once_then_surfaced() -> None:
    sleeps: list[float] = []
    provider, recorder = provider_with(
        httpx.Response(503, text="bad gateway"), httpx.Response(503, text=""), sleeps=sleeps
    )
    with pytest.raises(SerpApiError, match="HTTP 503"):
        provider.search(CDG_ISB, DEPARTURE)
    assert len(recorder.requests) == 2
    assert sleeps == [provider._retry_delay]


def test_a_5xx_followed_by_success_answers_normally() -> None:
    provider, recorder = provider_with(httpx.Response(502, text=""), ok(load_fixture()))
    assert len(provider.search(CDG_ISB, DEPARTURE)) == 4
    assert len(recorder.requests) == 2


def test_a_transport_error_is_retried_once_then_surfaced() -> None:
    provider, recorder = provider_with(
        httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out")
    )
    with pytest.raises(SerpApiTransportError, match="ReadTimeout"):
        provider.search(CDG_ISB, DEPARTURE)
    assert len(recorder.requests) == 2


def test_a_4xx_is_never_retried() -> None:
    provider, recorder = provider_with(httpx.Response(400, json={"error": "Missing query"}))
    with pytest.raises(SerpApiError, match="HTTP 400"):
        provider.search(CDG_ISB, DEPARTURE)
    assert len(recorder.requests) == 1


# --- The key never leaks ------------------------------------------------------------------


def test_error_messages_never_contain_the_key() -> None:
    echoed = f"Invalid API key: {KEY}"
    provider, _ = provider_with(httpx.Response(401, json={"error": echoed}))
    with pytest.raises(SerpApiAuthError) as excinfo:
        provider.search(CDG_ISB, DEPARTURE)
    assert KEY not in str(excinfo.value)
    assert "***" in str(excinfo.value)


def test_record_fixture_writes_the_raw_body_redacted(tmp_path: Path) -> None:
    # SerpApi does not echo the key back, but if it ever did, anywhere in the body,
    # the recording must not carry it. Planted outside search_metadata, which is stripped.
    body = load_fixture()
    body["search_parameters"]["echo"] = f"key={KEY}"
    provider, _ = provider_with(ok(body))

    path = record_fixture(provider, CDG_ISB, DEPARTURE, fixtures_dir=tmp_path / "serpapi")

    assert path == tmp_path / "serpapi" / "CDG-ISB-2026-12-20.json"
    text = path.read_text()
    assert KEY not in text
    assert "key=***" in text
    assert parse_response(json.loads(text), CDG_ISB, DEPARTURE) == parse_response(
        load_fixture(), CDG_ISB, DEPARTURE
    )


def test_recording_cli_rejects_bad_usage_without_a_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _main(["CDG", "ISB"]) == 2
    assert "usage" in capsys.readouterr().out


def test_recording_cli_reports_a_missing_key_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Pinned to keyless settings rather than relying on conftest hiding `.env`: with a
    # real key this call would spend a search and overwrite the shipped fixture.
    keyless = Settings(
        db_path=Path("x.duckdb"), serpapi_key=None, gdelt_api_key=None, site_export_path=Path("x")
    )
    monkeypatch.setattr(serpapi, "load_settings", lambda: keyless)
    assert _main(["CDG", "ISB", "2026-12-20"]) == 1
    assert "SERPAPI_KEY" in capsys.readouterr().err


# --- Fixture integrity ---------------------------------------------------------------------


def test_shipped_fixture_was_recorded_with_todays_query_parameters() -> None:
    """`search_parameters` is SerpApi echoing what it was asked; it must match what
    `query_params` sends now, or the mapping is being tested against another query."""
    provider, _ = provider_with()
    sent = provider.query_params(CDG_ISB, DEPARTURE)
    del sent["api_key"]
    recorded = {k: str(v) for k, v in load_fixture()["search_parameters"].items()}
    assert recorded == sent


def test_shipped_fixture_carries_no_key_and_no_archive_urls() -> None:
    text = FIXTURE.read_text()
    assert "api_key" not in text
    assert "https://serpapi.com/" not in text


def test_record_fixture_drops_serpapis_archive_urls(tmp_path: Path) -> None:
    body = load_fixture()
    body["search_metadata"]["json_endpoint"] = "https://serpapi.com/searches/abc/def.json"
    body["search_metadata"]["raw_html_file"] = "https://serpapi.com/searches/abc/def.html"
    provider, _ = provider_with(ok(body))

    path = record_fixture(provider, CDG_ISB, DEPARTURE, fixtures_dir=tmp_path)

    saved = json.loads(path.read_text())
    assert "https://serpapi.com/" not in path.read_text()
    assert saved["search_metadata"]["id"] == SEARCH_ID
    assert saved["search_metadata"]["status"] == "Success"
    # Only search_metadata is touched; the flights are byte-for-byte what was fetched.
    assert saved["other_flights"] == load_fixture()["other_flights"]


def test_strip_archive_urls_tolerates_a_body_without_metadata() -> None:
    assert strip_archive_urls({"error": "x"}) == {"error": "x"}


def test_shipped_fixture_is_a_real_recording() -> None:
    metadata = load_fixture()["search_metadata"]
    assert metadata["status"] == "Success"
    assert metadata["id"] == SEARCH_ID


def test_itinerary_type_round_trips_through_the_fixture() -> None:
    results = parse_response(load_fixture(), CDG_ISB, DEPARTURE)
    assert all(isinstance(i, Itinerary) for i in results)
    assert all(Itinerary.model_validate(i.model_dump()) == i for i in results)
