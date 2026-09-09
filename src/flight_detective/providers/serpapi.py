"""Google Flights through SerpApi (PRD F2, the real provider).

One `search` is one SerpApi request: `engine=google_flights`, one-way (`type=2`), economy
(`travel_class=1`), pinned to `hl=fr` / `gl=fr` / `currency=EUR` because Google Flights
answers differently per locale and the PRD wants the fares a buyer in France sees. There is
no `stops` filter in the request on purpose: providers return everything (architecture,
S04) and `providers/filters.py` drops the 2-stops, so `ingest_run.rows_dropped` counts what
Google really offered.

Mapping decisions, all driven by what the recorded response looks like:

- **Carrier and flight numbers come from `flight_number` ("QR 40"), not `airline`.** The
  airline field is display text ("Pakistan International Airlines", "Turkish Airlines"),
  while the flight number's prefix is the marketing carrier's IATA code, which is what
  `Itinerary.carrier` is defined as. `carrier` is the *first* leg's code; `flight_numbers`
  keeps every leg, so a mixed-carrier itinerary still has its own primary key.
- **Cabin is what was asked for, not what `travel_class` says.** Google only returns the
  requested class, so parsing it would add nothing; and `hl=fr` localizes inconsistently
  (airport names come back in French, `travel_class` and the extensions in English), so
  nothing here matches on display strings at all.
- **`layover_minutes` is the sum of every layover**, `stops` is legs minus one and
  `duration_minutes` is `total_duration`, matching how the fake fixtures were counted.
- **An itinerary without a `price` is skipped**, not an error. Google shows those as
  "Prix indisponible"; there is no fare to observe. Everything else that fails to parse
  is a `SerpApiResponseInvalid` for the whole query, because a partially understood
  response is worse than a missing cell.
- The response's first departure airport, last arrival airport and first departure
  date must be the query's. A mismatch is `SerpApiResponseInvalid`, the same guard the
  fake applies to a fixture whose body disagrees with its name.

Failures are `SerpApiError` subclasses, all `ProviderError`s, so `run_ingest` records them
as one bad cell: `SerpApiAuthError` (401), `SerpApiQuotaError` (429, or SerpApi's "run out
of searches" text at any status), `SerpApiTransportError` (connection/timeout) and
`SerpApiResponseInvalid`. A 200 whose `error` says Google returned no results is the
"no flights" answer and maps to `[]`, like an empty fake fixture. Transport errors and 5xx
get exactly one retry: at 36 calls a day one lost cell is already 2.8 % of the day, above
the PRD's 2 % target, and a second attempt a couple of seconds later usually clears a
blip. 4xx are never retried; they will not change.

The API key is never printed: every message that could carry a URL is redacted, and
`raw_ref` is `serpapi:<search_metadata.id>#<best|other>:<index>`, SerpApi's own archive
handle for the raw result.

Recording a fixture (manual, needs `SERPAPI_KEY` in `.env`, costs one search):

    uv run python -m flight_detective.providers.serpapi CDG ISB 2026-12-20

writes the raw JSON to `tests/fixtures/serpapi/CDG-ISB-2026-12-20.json`, with the key
redacted should it ever appear in the body and SerpApi's account-scoped archive URLs
dropped from `search_metadata`. The shipped fixture is a real recording made
on 2026-09-09 (four `other_flights`, no `best_flights`: a PIA direct, an Azerbaijan
Airlines 1-stop and two Turkish 1-stops). The tests pin its exact contents, so re-recording
means updating their expected values too; `search_parameters` in the file is the proof
that it was fetched with the parameters `query_params` sends today.
"""

import json
import re
import sys
import time
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flight_detective.config import Settings, load_settings
from flight_detective.models import Cabin, Itinerary, Money, Route
from flight_detective.providers.base import ProviderError

ENDPOINT = "https://serpapi.com/search.json"
DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "serpapi"

# SerpApi's economy class id, and what it means in our terms.
TRAVEL_CLASS = "1"
CABIN: Cabin = "economy"

# Substrings of SerpApi's `error` text that mean something other than "bad response".
NO_RESULTS_MARKER = "hasn't returned any results"
QUOTA_MARKER = "run out of searches"

# "QR 40", "TK 1828", "9W 123" -> (carrier, number). The space is how Google formats it.
FLIGHT_NUMBER = re.compile(r"^([A-Z0-9]{2,3}) (\d{1,4}[A-Z]?)$")

# Google Flights searches routinely take 10-30 s server-side at SerpApi.
TIMEOUT_SECONDS = 60.0
RETRY_DELAY_SECONDS = 2.0


class SerpApiError(ProviderError):
    """Any foreseeable SerpApi failure."""


class SerpApiKeyMissing(SerpApiError):
    """No `SERPAPI_KEY` configured."""


class SerpApiAuthError(SerpApiError):
    """SerpApi rejected the key (HTTP 401)."""


class SerpApiQuotaError(SerpApiError):
    """Out of searches or rate-limited (HTTP 429 or SerpApi's quota message)."""


class SerpApiTransportError(SerpApiError):
    """Could not reach SerpApi, or it did not answer in time."""


class SerpApiResponseInvalid(SerpApiError):
    """SerpApi answered, but not with something this module understands."""


# --- Response shape (only the fields used; everything else is ignored) -----------------


class _Lenient(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _Airport(_Lenient):
    id: str
    time: str


class _Leg(_Lenient):
    departure_airport: _Airport
    arrival_airport: _Airport
    flight_number: str
    duration: int


class _Layover(_Lenient):
    duration: int = Field(ge=0)


class _Option(_Lenient):
    flights: list[_Leg] = Field(min_length=1)
    layovers: list[_Layover] = []
    total_duration: int
    price: Money | None = None


class _Metadata(_Lenient):
    id: str | None = None


class _Response(_Lenient):
    search_metadata: _Metadata | None = None
    best_flights: list[_Option] = []
    other_flights: list[_Option] = []
    error: str | None = None


def fixture_path(fixtures_dir: Path, route: Route, departure_date: date) -> Path:
    return fixtures_dir / f"{route.origin}-{route.destination}-{departure_date.isoformat()}.json"


def _split_flight_number(text: str) -> tuple[str, str]:
    match = FLIGHT_NUMBER.match(text.strip())
    if match is None:
        raise SerpApiResponseInvalid(f"flight_number {text!r} is not '<carrier> <number>'")
    return match.group(1), match.group(1) + match.group(2)


def _departure_day(leg: _Leg) -> date:
    try:
        return date.fromisoformat(leg.departure_airport.time[:10])
    except ValueError as exc:
        raise SerpApiResponseInvalid(
            f"departure time {leg.departure_airport.time!r} does not start with a date"
        ) from exc


def _to_itinerary(
    option: _Option, route: Route, departure_date: date, *, provider: str, raw_ref: str
) -> Itinerary | None:
    if option.price is None:
        return None
    first, last = option.flights[0], option.flights[-1]
    if first.departure_airport.id != route.origin or last.arrival_airport.id != route.destination:
        raise SerpApiResponseInvalid(
            f"{raw_ref} flies {first.departure_airport.id}->{last.arrival_airport.id}, "
            f"asked for {route.origin}->{route.destination}"
        )
    if (day := _departure_day(first)) != departure_date:
        raise SerpApiResponseInvalid(
            f"{raw_ref} departs {day.isoformat()}, asked for {departure_date.isoformat()}"
        )
    codes = [_split_flight_number(leg.flight_number) for leg in option.flights]
    return Itinerary(
        route=route,
        departure_date=departure_date,
        carrier=codes[0][0],
        flight_numbers=[number for _, number in codes],
        stops=len(option.flights) - 1,
        layover_minutes=sum(layover.duration for layover in option.layovers),
        duration_minutes=option.total_duration,
        cabin=CABIN,
        price_eur=option.price,
        provider=provider,
        raw_ref=raw_ref,
    )


def parse_response(
    payload: Mapping[str, Any], route: Route, departure_date: date, *, provider: str = "serpapi"
) -> list[Itinerary]:
    """Map one SerpApi JSON body (HTTP 200) to itineraries, unfiltered.

    Raises `SerpApiQuotaError` / `SerpApiError` for an `error` body, `SerpApiResponseInvalid`
    for anything that does not parse; returns `[]` for Google's "no results".
    """
    try:
        response = _Response.model_validate(payload)
    except ValidationError as exc:
        raise SerpApiResponseInvalid(f"SerpApi response is not the expected shape: {exc}") from exc
    if response.error is not None:
        if NO_RESULTS_MARKER in response.error:
            return []
        if QUOTA_MARKER in response.error:
            raise SerpApiQuotaError(f"SerpApi quota: {response.error}")
        raise SerpApiError(f"SerpApi error: {response.error}")
    search_id = response.search_metadata.id if response.search_metadata else None
    groups = (("best", response.best_flights), ("other", response.other_flights))
    itineraries: list[Itinerary] = []
    for group, options in groups:
        for index, option in enumerate(options):
            raw_ref = f"serpapi:{search_id or 'unknown'}#{group}:{index}"
            itinerary = _to_itinerary(
                option, route, departure_date, provider=provider, raw_ref=raw_ref
            )
            if itinerary is not None:
                itineraries.append(itinerary)
    return itineraries


class SerpApiFareProvider:
    name = "serpapi"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        retry_delay: float = RETRY_DELAY_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise SerpApiKeyMissing("SerpApi API key is empty")
        self._api_key = api_key
        self._client = client if client is not None else httpx.Client(timeout=TIMEOUT_SECONDS)
        self._retry_delay = retry_delay
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> Self:
        if settings.serpapi_key is None:
            raise SerpApiKeyMissing(
                "SERPAPI_KEY is not set: add SERPAPI_KEY=<key> to .env or export it "
                "(keys: https://serpapi.com/manage-api-key)"
            )
        return cls(settings.serpapi_key, **kwargs)

    def redact(self, text: str) -> str:
        """Blank the key out of anything that might be logged."""
        return text.replace(self._api_key, "***")

    def query_params(self, route: Route, departure_date: date) -> dict[str, str]:
        return {
            "engine": "google_flights",
            "api_key": self._api_key,
            "departure_id": route.origin,
            "arrival_id": route.destination,
            "outbound_date": departure_date.isoformat(),
            "type": "2",
            "travel_class": TRAVEL_CLASS,
            "adults": "1",
            "currency": "EUR",
            "hl": "fr",
            "gl": "fr",
        }

    def search(self, route: Route, departure_date: date) -> list[Itinerary]:
        return parse_response(self.fetch_raw(route, departure_date), route, departure_date)

    def fetch_raw(self, route: Route, departure_date: date) -> dict[str, Any]:
        """The JSON body of a successful (HTTP 200) search, unparsed."""
        params = self.query_params(route, departure_date)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._request(params)
            except (SerpApiTransportError, _ServerError) as exc:
                if attempt >= 2:
                    if isinstance(exc, _ServerError):
                        raise SerpApiError(str(exc)) from exc
                    raise
                self._sleep(self._retry_delay)

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._client.get(ENDPOINT, params=params)
        except httpx.TransportError as exc:
            raise SerpApiTransportError(
                f"could not reach SerpApi: {type(exc).__name__}: {self.redact(str(exc))}"
            ) from exc
        body = self._json(response)
        message = body.get("error") if isinstance(body, dict) else None
        detail = self.redact(str(message)) if message else f"HTTP {response.status_code}"
        if response.status_code == 401:
            raise SerpApiAuthError(f"SerpApi rejected the API key ({detail}); check SERPAPI_KEY")
        if response.status_code == 429 or (message and QUOTA_MARKER in str(message)):
            raise SerpApiQuotaError(f"SerpApi quota or rate limit: {detail}")
        if response.status_code >= 500:
            raise _ServerError(f"SerpApi server error: {detail}")
        if response.status_code != 200:
            raise SerpApiError(f"SerpApi HTTP {response.status_code}: {detail}")
        if not isinstance(body, dict):
            raise SerpApiResponseInvalid("SerpApi response is not a JSON object")
        return body

    def _json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            if response.status_code != 200:
                # Error pages need not be JSON; the status is the information.
                return {}
            raise SerpApiResponseInvalid(
                f"SerpApi response is not JSON: {self.redact(response.text[:200])!r}"
            ) from exc


class _ServerError(Exception):
    """Internal: a 5xx, retried once before becoming a SerpApiError."""


def strip_archive_urls(body: dict[str, Any]) -> dict[str, Any]:
    """Drop the `search_metadata` entries that point into SerpApi's per-account search
    archive (`json_endpoint`, `raw_html_file`, ...). They are not the key, but they are
    account-scoped handles with no use in a committed fixture; only `id` is read."""
    metadata = body.get("search_metadata")
    if isinstance(metadata, dict):
        body["search_metadata"] = {
            key: value
            for key, value in metadata.items()
            if not (isinstance(value, str) and value.startswith("https://serpapi.com/"))
        }
    return body


def record_fixture(
    provider: SerpApiFareProvider,
    route: Route,
    departure_date: date,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
) -> Path:
    """Fetch one real response and save it where the tests read it. Costs one search."""
    body = strip_archive_urls(provider.fetch_raw(route, departure_date))
    path = fixture_path(fixtures_dir, route, departure_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
    path.write_text(provider.redact(text))
    return path


def _main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m flight_detective.providers.serpapi ORIGIN DEST YYYY-MM-DD")
        return 2
    origin, destination, day = argv
    try:
        provider = SerpApiFareProvider.from_settings(load_settings())
        route = Route.model_validate({"origin": origin, "destination": destination})
        path = record_fixture(provider, route, date.fromisoformat(day))
    except (ProviderError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
