"""The GDELT DOC 2.0 client (PRD F4).

GDELT is free and needs no key, which is why it is the source: the taxonomy in
brainstorming §3 is a handful of phrase queries, and the whole point of them is
*anti-noise* — a general "Pakistan flights" query returns thousands of articles a week
and none of the precision the explanation layer (S12) needs. Each `TaxonomyQuery` below
is one of the brainstorm's groups, kept verbatim, with the phrases OR-ed into a single
request; `"EASA" "PIA"` is its own query because GDELT will not mix an implicit AND with
an OR group at the same level.

Shape decisions:

- **`timespan=<n>d`, not an explicit `startdatetime`/`enddatetime` range.** `--days 7`
  means "what GDELT has seen in the last week", and a relative window keeps the clock out
  of the request parameters, so the recorded fixtures never go stale. Idempotency across
  runs comes from `news_event.dedupe_key`, not from pinning the window.
- **`mode=artlist`, `sort=datedesc`, `maxrecords=250`** (GDELT's ceiling). The article
  list is all this needs: a URL, a headline, a domain and the timestamp GDELT first saw
  it. The tone/volume APIs answer a different question.
- **`seendate` is when GDELT saw the article, not when the incident happened.** GDELT
  gives nothing better in `artlist`, and for a disruption the two are within hours. It is
  converted to a *Paris* date because every other date in this project is Paris-local and
  S12 joins events to fare rows on that calendar.
- Articles with no title or no URL are dropped rather than failing the query: dedupe has
  nothing to work with, and GDELT occasionally emits one.

Failures are `GdeltError` subclasses so `news/ingest.py` can record a dead query and keep
the others, the same bargain `run_ingest` makes for fare cells. GDELT signals an
unusable query with plain text and HTTP 200 (`Your query was too short or too long...`),
so a non-JSON body is `GdeltResponseInvalid` carrying that text. Transport errors and 5xx
are retried once; GDELT is a free service on a small research budget and a blip is common.
`REQUEST_INTERVAL_SECONDS` is the courtesy pause between queries — GDELT asks callers not
to hammer the endpoint, and four queries a day cost nothing to space out.

Recording fixtures (manual, needs network, free), from the repo root:

    uv run python -m flight_detective.news.gdelt 7

writes one file per taxonomy query to `tests/fixtures/gdelt/<slug>.json`. That path is
relative to the working directory and lives only in `_main`: unlike the fake fare
provider, nothing at *run* time reads these fixtures, so the client itself has no business
knowing where the test tree is. The committed fixtures are *hand-written* to the
documented artlist shape, not recordings, because the articles a live query returns depend
on the week it is run; re-recording is welcome, and the parsing tests read whatever is in
the files.
"""

import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# Where `_main` records fixtures, relative to the working directory. See the docstring.
RECORDING_DIR = Path("tests") / "fixtures" / "gdelt"

PARIS = ZoneInfo("Europe/Paris")

# GDELT stamps `seendate` as UTC in this compact form.
SEENDATE_FORMAT = "%Y%m%dT%H%M%SZ"

TIMEOUT_SECONDS = 30.0
RETRY_DELAY_SECONDS = 2.0
REQUEST_INTERVAL_SECONDS = 5.0
MAX_RECORDS = 250

# What `--days` means when nobody says; mirrored by `news/ingest.py`'s DEFAULT_DAYS.
DEFAULT_DAYS = 7

# DOC 2.0 indexes a rolling three-month archive; asking for more silently gets less.
MAX_DAYS = 90

Category = Literal["regulatory", "airspace_disruption", "pilgrimage_visas"]


@dataclass(frozen=True)
class TaxonomyQuery:
    """One brainstorming §3 filter group: the category it feeds and the GDELT query."""

    slug: str
    category: Category
    query: str


TAXONOMY: tuple[TaxonomyQuery, ...] = (
    TaxonomyQuery("regulatory-easa-pia", "regulatory", '"EASA" "PIA"'),
    TaxonomyQuery(
        "regulatory-traffic-rights",
        "regulatory",
        '("bilateral air service" OR "traffic rights Pakistan" OR "civil aviation authority")',
    ),
    TaxonomyQuery(
        "airspace-disruption",
        "airspace_disruption",
        '("Middle East airspace" OR "Gulf Air route disruption" OR "CDG strike" '
        'OR "Doha transit delay")',
    ),
    TaxonomyQuery(
        "pilgrimage-visas",
        "pilgrimage_visas",
        '("Umrah quota" OR "Hajj flight schedule" OR "Saudi transit visa")',
    ),
)


class GdeltError(Exception):
    """A foreseeable GDELT failure: unreachable, rate-limited, or an unusable answer."""


class GdeltTransportError(GdeltError):
    """Could not reach GDELT, or it did not answer in time."""


class GdeltRateLimited(GdeltError):
    """GDELT is throttling this caller (HTTP 429)."""


class GdeltResponseInvalid(GdeltError):
    """GDELT answered, but not with an article list this module understands."""


class _Lenient(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Article(_Lenient):
    """One row of a GDELT `artlist` response; only the fields used are declared."""

    url: str
    title: str
    seendate: str
    domain: str = ""
    language: str = ""
    sourcecountry: str = ""


class _Response(_Lenient):
    articles: list[Article] = []


def seen_at(article: Article) -> datetime:
    """The instant GDELT first saw the article, as an aware UTC datetime."""
    try:
        return datetime.strptime(article.seendate, SEENDATE_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise GdeltResponseInvalid(
            f"seendate {article.seendate!r} is not {SEENDATE_FORMAT}"
        ) from exc


def seen_date(article: Article) -> date:
    """`seen_at` on the Paris calendar, which is the one `news_event.event_date` uses."""
    return seen_at(article).astimezone(PARIS).date()


def parse_articles(payload: Mapping[str, Any]) -> list[Article]:
    """Map one GDELT JSON body to articles. An empty body is "no matches", not an error."""
    try:
        response = _Response.model_validate(payload)
    except ValidationError as exc:
        raise GdeltResponseInvalid(f"GDELT response is not an article list: {exc}") from exc
    articles = [a for a in response.articles if a.title.strip() and a.url.strip()]
    for article in articles:
        seen_at(article)  # reject a malformed timestamp here, not three modules later
    return articles


def query_params(query: str, days: int, *, max_records: int = MAX_RECORDS) -> dict[str, str]:
    """The DOC 2.0 request for one taxonomy query over the last `days` days."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}, got {days}")
    return {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "timespan": f"{days}d",
        "maxrecords": str(max_records),
        "sort": "datedesc",
    }


class GdeltClient:
    name = "gdelt"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        retry_delay: float = RETRY_DELAY_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client if client is not None else httpx.Client(timeout=TIMEOUT_SECONDS)
        self._retry_delay = retry_delay
        self.sleep = sleep

    def search(self, query: str, days: int) -> list[Article]:
        """Articles matching one taxonomy query over the last `days` days."""
        return parse_articles(self.fetch_raw(query, days))

    def fetch_raw(self, query: str, days: int) -> dict[str, Any]:
        """The JSON body of a successful search, unparsed."""
        params = query_params(query, days)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._request(params)
            except (GdeltTransportError, _ServerError) as exc:
                if attempt >= 2:
                    if isinstance(exc, _ServerError):
                        raise GdeltError(str(exc)) from exc
                    raise
                self.sleep(self._retry_delay)

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._client.get(ENDPOINT, params=params)
        except httpx.TransportError as exc:
            raise GdeltTransportError(
                f"could not reach GDELT: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code == 429:
            raise GdeltRateLimited(f"GDELT is rate-limiting this caller (HTTP {429})")
        if response.status_code >= 500:
            raise _ServerError(f"GDELT server error: HTTP {response.status_code}")
        if response.status_code != 200:
            raise GdeltError(f"GDELT HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            # GDELT reports an unusable query as plain text with a 200; the text is the
            # only diagnostic there is, so it goes into the message verbatim.
            raise GdeltResponseInvalid(
                f"GDELT did not answer with JSON: {response.text[:200]!r}"
            ) from exc
        if not isinstance(body, dict):
            raise GdeltResponseInvalid("GDELT response is not a JSON object")
        return body


class _ServerError(Exception):
    """Internal: a 5xx, retried once before becoming a GdeltError."""


def fixture_path(fixtures_dir: Path, entry: TaxonomyQuery) -> Path:
    return fixtures_dir / f"{entry.slug}.json"


def record_fixtures(client: GdeltClient, days: int, fixtures_dir: Path) -> list[Path]:
    """Fetch every taxonomy query and save the raw bodies where the tests read them."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, entry in enumerate(TAXONOMY):
        if index:
            client.sleep(REQUEST_INTERVAL_SECONDS)
        path = fixture_path(fixtures_dir, entry)
        body = client.fetch_raw(entry.query, days)
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        written.append(path)
    return written


def _main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: python -m flight_detective.news.gdelt [DAYS] [DIR]")
        return 2
    try:
        days = int(argv[0]) if argv else DEFAULT_DAYS
        directory = Path(argv[1]) if len(argv) == 2 else RECORDING_DIR
        for path in record_fixtures(GdeltClient(), days, directory):
            print(path)
    except (GdeltError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
