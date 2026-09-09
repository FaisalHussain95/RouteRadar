"""The GDELT Cloud client (PRD F4), replacing the public DOC 2.0 API.

**Why the source changed.** The free DOC 2.0 endpoint answered HTTP 429 to nearly every
request on 2026-09-09 — from two hosts, on a one-word query, at 20 s spacing. An
unauthenticated service that throttles to zero is not a daily pipeline input. GDELT Cloud
is the same project's paid-tier API: a Bearer key, a metered budget, and Stories that are
already *clusters* of articles about one development, which is the thing `news/dedupe.py`
used to approximate by comparing titles.

## Two kinds of query, and why both

Everything is `GET /api/v2/stories` over the last `days` days, but the retrieval arm
differs, and the difference is the whole anti-noise argument:

1. **Entity queries** — one per carrier in `CARRIERS`, `entity=<id>`. An entity id is the
   resolver's answer to "which real-world organisation is this", so the result is that
   airline's coverage with no semantic fuzz at all. This is why the ids are committed as
   data below rather than resolved at run time: a name crossing an endpoint boundary is
   re-resolved every call, costs a query unit, and can silently start meaning something
   else. Their category is `regulatory` unless the relevance guard reclassifies them (see
   `RELEVANCE_KEYWORDS`).
2. **Semantic queries** — one per topical group in `SEMANTIC_QUERIES`, `search=<text>`.
   Airspace closures and Umrah quotas are not entities and have no id; they have to be
   retrieved conceptually. `search` is embedded and ranked by cosine similarity: there is
   no boolean parser, and a string containing a conjunction is silently *truncated to its
   first term*. See `SEMANTIC_QUERIES` — that trap is the reason every query there is a
   single concept and the reason the echo is asserted.

   Semantic retrieval fills a **bounded relevance pool** (`meta.search.coverage_complete`
   is `false`), so a full page of hits is the normal shape of *no news* as much as of some:
   the server always returns its `limit` in nearest neighbours. Scores are therefore ranks
   within that pool, not relevance verdicts, and the real filter is `RELEVANCE_KEYWORDS`
   below. `MIN_SEARCH_SCORE` only trims the pool's floor.

## Shape decisions

- **`limit=100`, always.** A call costs one query unit whatever `limit` says, so a smaller
  page is not cheaper, it is more expensive per row. Paging walks `pagination.next_cursor`
  until it is `null` — *row count is never the truncation signal*, because the server
  fetches one row past the page to set that field and a final page can be exactly full.
  `MAX_ROWS` is a deliberate ceiling; hitting it is reported, not swallowed.
- **`applied_filters` is asserted, not assumed.** The dangerous failure on this API is
  HTTP 200: a misspelled filter is dropped and the answer is a plausible, unfiltered,
  often *empty* result. `check_applied_filters` raises when a filter we sent is missing
  from the echo or lands in `applied_filters.ignored`, so "quiet week" and "the query was
  wrong" cannot be confused.
- **`sort=recent`.** The pipeline runs daily and wants the week's developments, not the
  all-time most significant ones inside the week.
- **`languages=en,fr`.** The routes are Paris–Pakistan; French wire copy carries the CDG
  and DGAC side that English-only coverage misses.
- **Parse permissively.** New response fields ship without notice on this API, so every
  model ignores unknown keys and every optional metric tolerates `null`. A `null` metric
  means *unmeasured*, never zero.
- **`event_date` is `story_date` as given.** A Story is dated by observation on the
  publisher's calendar; there is no instant to convert, so unlike the DOC client there is
  no timezone step. Paris only re-enters at the `ingest_run.run_at` stamp.

## Errors

`GdeltCloudError` subclasses, so `news/ingest.py` can lose one query and keep the rest —
the bargain `run_ingest` makes for a fare cell. The one that is *not* per-query is quota:
HTTP 429 carries a `code`, and the two values mean opposite things. `RATE_LIMITED` is
"slow down" — waited out once, using `details.retry_after`, and retried. `QUOTA_EXCEEDED`
is "the month's budget is gone" — retrying it burns nothing but wall clock, so it raises
`GdeltCloudQuotaError` and the caller stops issuing requests entirely. Branch on the code,
never on the status. 5xx and transport blips get one retry; 4xx never do.

## Recording the fixtures

Manual, needs `GDELT_API_KEY` in `.env`, and costs 15 query units (one per call — `/search`
is *not* free, measured at `X-Quota-Cost: 1` on 2026-09-09; only `/meta/*` is 0):

    uv run python -m flight_detective.news.gdeltcloud resolve   # 6 units, /search
    uv run python -m flight_detective.news.gdeltcloud record    # 9 units, /stories

`resolve` re-runs the six carrier lookups and prints the candidates for each, so `CARRIERS`
can be re-checked by eye — it deliberately does not rewrite the table, because choosing
between candidates is the judgement the id is committed to preserve. `record` writes one
raw body per query to `tests/fixtures/gdeltcloud/`. Both paths are relative to the working
directory and live only in `_main`: nothing at *run* time reads the fixtures, so the client
has no business knowing where the test tree is.
"""

import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

from flight_detective.config import Settings

BASE_URL = "https://gdeltcloud.com/api/v2"
STORIES_PATH = "/stories"
SEARCH_PATH = "/search"
QUERY_UNITS_PATH = "/meta/query-units"

# Where `_main` records fixtures, relative to the working directory. See the docstring.
RECORDING_DIR = Path("tests") / "fixtures" / "gdeltcloud"

# `/search` took over 40 s on three of the six carrier lookups on 2026-09-09; a resolver
# fan-out is not a keyed read. `/stories` answers in a second or two, but one timeout is
# a whole missing query, so the generous bound applies to both.
TIMEOUT_SECONDS = 150.0
RETRY_DELAY_SECONDS = 2.0
REQUEST_INTERVAL_SECONDS = 2.0

# A call costs one query unit whatever `limit` is, so page at the maximum the API allows.
PAGE_LIMIT = 100

# The ceiling on one query's walk. Nine queries a day at 300 rows is far more news than the
# dashboard can show; a walk that reaches this has almost certainly matched something far
# too broad, which is worth reporting rather than paying for.
MAX_ROWS = 300

# What `--days` means when nobody says; mirrored by `news/ingest.py`'s DEFAULT_DAYS.
DEFAULT_DAYS = 7

# The API caps one story window at 30 inclusive days. Longer windows need consecutive
# chunks, which the daily pipeline never wants: it asks for the week it just lived through.
MAX_DAYS = 30

# Coverage languages. See the module docstring.
LANGUAGES = "en,fr"

# Warn below this many query units left in the month. The daily run costs 9, so this is
# roughly a fortnight of runway — enough notice to do something before the pipeline stops.
LOW_QUERY_UNITS = 150

Category = Literal["regulatory", "airspace_disruption", "pilgrimage_visas"]


class GdeltCloudError(Exception):
    """A foreseeable GDELT Cloud failure: unreachable, refused, or an unusable answer."""


class GdeltCloudKeyMissing(GdeltCloudError):
    """No `GDELT_API_KEY` configured."""


class GdeltCloudAuthError(GdeltCloudError):
    """GDELT Cloud rejected the key (HTTP 401/403)."""


class GdeltCloudRateLimited(GdeltCloudError):
    """Throttled (HTTP 429, `code: RATE_LIMITED`). Transient: waiting clears it."""


class GdeltCloudQuotaError(GdeltCloudError):
    """The month's query units are gone (HTTP 429, `code: QUOTA_EXCEEDED`).

    Distinct from `GdeltCloudRateLimited` on purpose: this is a sizing problem, not a
    retry problem, and the run must stop calling rather than spend the rest of its
    wall clock discovering the same answer nine times."""


class GdeltCloudTransportError(GdeltCloudError):
    """Could not reach GDELT Cloud, or it did not answer in time."""


class GdeltCloudResponseInvalid(GdeltCloudError):
    """GDELT Cloud answered, but not with something this module understands.

    Also raised when `applied_filters` does not echo a filter we sent: an answer that
    silently ignored a filter is wrong, not empty."""


# --- Committed data -------------------------------------------------------------------


@dataclass(frozen=True)
class Carrier:
    """One PRD § Carriers of interest airline, resolved once to a GDELT Cloud entity id.

    `resolved_from` is the exact `/search?q=` string that produced `entity_id`, and `note`
    is why *this* candidate — both so the table can be re-derived and re-argued rather
    than trusted. Re-run `python -m flight_detective.news.gdeltcloud resolve` to see the
    current candidates side by side."""

    name: str
    entity_id: str
    resolved_from: str
    note: str


# Resolved 2026-09-09 with `GET /api/v2/search?q=<resolved_from>&country_match=strict`.
# `coverage_30d` (distinct resolved stories in the last 30 days) is what separates the
# candidates: it is measured, and a zero means the id exists but the news layer has nothing
# filed under it. Positional order is *not* the tiebreak — see Emirates.
CARRIERS: tuple[Carrier, ...] = (
    Carrier(
        name="PIA",
        entity_id="wiki:96b022cd5d6a195efcd6",
        resolved_from="Pakistan International Airlines",
        note="two candidates scored 7; the wiki id measured coverage_30d=5 and the "
        "e_376a9b78754d03ae registry sibling measured 0, so the news-layer id is the one "
        "that can return stories",
    ),
    Carrier(
        name="Gulf Air",
        entity_id="e_f11af3b099743227",
        resolved_from="Gulf Air",
        note="sole candidate. coverage_30d=0 — a small carrier with thin coverage, so an "
        "empty result here is no coverage rather than a broken query",
    ),
    Carrier(
        name="Qatar Airways",
        entity_id="e_188236fd75e936b4",
        resolved_from="Qatar Airways",
        note="sole candidate, coverage_30d=19",
    ),
    Carrier(
        name="Emirates",
        entity_id="e_8eb7677d51aebda4",
        resolved_from="Emirates",
        note="NOT the first candidate. `e_43de478648df2fe0` ('Emirates', a company) ranks "
        "first on the same score of 7 but has sources.news=false and coverage_30d=0; "
        "'Emirates (airline)' is the airline and measures 16. The other candidates are the "
        "country and its central bank",
    ),
    Carrier(
        name="Turkish Airlines",
        entity_id="e_7345c93580142d43",
        resolved_from="Turkish Airlines",
        note="sole candidate, coverage_30d=15",
    ),
    Carrier(
        name="Saudia",
        entity_id="e_8d1aef1961310580",
        resolved_from="Saudia",
        note="coverage_30d=5; the rival candidates are Saudi Arabia the country and two of "
        "its ministries, which would have swamped the feed with national politics",
    ),
)


@dataclass(frozen=True)
class SemanticQuery:
    """One brainstorming §3 topical group, retrieved conceptually rather than by id."""

    slug: str
    category: Category
    search: str


# The three groups that are not entities.
#
# **Each string must express exactly one concept, with no conjunction.** `search` is
# embedded and ranked by cosine similarity; there is no boolean parser. Worse, a query
# containing `and`/`or` is *split* and every term after the first is silently discarded —
# HTTP 200, `applied_filters.ignored` empty, a plausible answer to a question nobody asked.
# Measured on 2026-09-09: "Hajj and Umrah pilgrimage flight quotas and Saudi transit visa
# rules" came back having searched `"Hajj"` alone, with the other two terms named in the
# response's `note`. The phrasings below were probed until `applied_filters.search` echoed
# them verbatim and `note` was null; `check_applied_filters` compares that echo on every
# call, so a phrasing that regresses to term-splitting fails the run instead of quietly
# narrowing it. If a group ever genuinely needs two concepts, it becomes two entries here
# — one call each — never one string with an `or` in it.
SEMANTIC_QUERIES: tuple[SemanticQuery, ...] = (
    SemanticQuery(
        "airspace-disruption",
        "airspace_disruption",
        "airspace closure",
    ),
    SemanticQuery(
        "pilgrimage-visas",
        "pilgrimage_visas",
        "Hajj pilgrimage flight quota",
    ),
    SemanticQuery(
        "cdg-strikes",
        "airspace_disruption",
        "strike at Paris Charles de Gaulle airport",
    ),
)

# Semantic retrieval fills a *bounded relevance pool* — the server says so in
# `meta.search.coverage_complete: false` — so it always returns its `limit` in near-misses
# whether or not anything relevant happened. The score is therefore a weak, query-relative
# ranking signal, not a relevance verdict, and `RELEVANCE_KEYWORDS` is what actually decides
# a story's fate. This threshold only trims the bottom of the pool.
#
# 0.20 is set from the recorded fixtures, not from a docs example. The three committed
# pools (`tests/fixtures/gdeltcloud/{airspace-disruption,pilgrimage-visas,cdg-strikes}.json`)
# are 300 scored rows spanning **0.2943 to 0.4303**, so a threshold below that floor keeps
# the whole pool and lets the keyword guard do the work.
#
# An earlier 0.55 was tried and rejected: it drops **all 300** of those rows. It was chosen
# against the superseded pre-correction recordings — the truncated one-token queries, whose
# 204 rows ran 0.25–0.62 and of which exactly one cleared 0.55 — and it was wrong there too.
# Either way the lesson is the same: these are cosine distances within a bounded relevance
# pool and they never approach 1.0, so a threshold picked by intuition cuts everything.
# `test_the_threshold_keeps_the_recorded_pools` pins the floor, the ceiling and the fact
# that no recorded row would survive 0.55.
MIN_SEARCH_SCORE = 0.20


# Which axis a keyword speaks to. See `RELEVANCE_KEYWORDS` for why one word is not enough.
Axis = Literal["subject", "place"]


@dataclass(frozen=True)
class RelevanceKeyword:
    """One word the guard accepts a story on, and why it is on the list.

    `category` is set only where the word *is* the topic rather than merely evidence of
    it. A carrier's entity query defaults to `regulatory`, and a matched keyword carrying
    a category moves that story to where it belongs — a PIA story about a closed airspace
    is an airspace story, not a regulatory one."""

    word: str
    axis: Axis
    why: str
    category: Category | None = None


# The guard on top of *both* retrieval arms, and the thing that actually decides what is
# news about a Paris-Pakistan fare. An entity query returns the airline's sponsorship
# deals; a semantic query returns a bounded pool that is mostly near-misses. A story
# survives only if this list matches its title or one of its top articles' titles.
# Matching is word-bounded and case-insensitive.
#
# **One keyword is not enough, and that was measured.** A first pass accepted any single
# word here and let 40 of 300 recorded rows through, of which most were not aviation at
# all: "Pakistan tenders for wheat imports", "Pakistan court grants bail to protester",
# "Eiffel Tower strike over women's restrictions". The reason is that half this list is
# *geography* — a country or a hub city appears in every kind of story filed from it, so on
# its own it selects a place, not a topic. So the list carries two axes:
#
# - `subject` — the thing itself: a scope carrier, or a topic that is inherently about
#   flying. Sufficient alone, because a story naming PIA or an airspace closure is in scope
#   whatever else it says.
# - `place` — an origin, destination or hub. Admits a story only alongside an
#   `AVIATION_TERMS` word, which is what turns "somewhere we care about" into "aviation
#   news from somewhere we care about".
#
# Re-measured on the same fixtures, that takes 300 rows to 16 and every survivor is
# aviation news. Add a city or country as a `place`; add an airline or a flying topic as a
# `subject`. A `place` promoted to `subject` re-admits the wheat imports.
RELEVANCE_KEYWORDS: tuple[RelevanceKeyword, ...] = (
    RelevanceKeyword(
        "airspace",
        "subject",
        "the single highest-impact disruption on these routes",
        "airspace_disruption",
    ),
    RelevanceKeyword(
        "hajj",
        "subject",
        "PRD § seasonal drivers: the Hajj corridor moves Saudia and PIA capacity",
        "pilgrimage_visas",
    ),
    RelevanceKeyword(
        "umrah",
        "subject",
        "year-round pilgrimage demand, and the quota news that shifts it",
        "pilgrimage_visas",
    ),
    RelevanceKeyword("pia", "subject", "the only direct operator, and the one regulators act on"),
    RelevanceKeyword("gulf air", "subject", "scope carrier"),
    RelevanceKeyword("qatar airways", "subject", "scope carrier"),
    RelevanceKeyword(
        "emirates",
        "subject",
        "scope carrier; also matches 'United Arab Emirates', accepted as a hub-country "
        "false positive because the airline and the country rarely make separate news here",
    ),
    RelevanceKeyword("flydubai", "subject", "scope carrier: Emirates' codeshare feed into SKT"),
    RelevanceKeyword("turkish airlines", "subject", "scope carrier"),
    RelevanceKeyword("saudia", "subject", "scope carrier"),
    RelevanceKeyword(
        "easa", "subject", "the regulator whose PIA ban is the reference capacity shock"
    ),
    RelevanceKeyword(
        "pakistan", "place", "the destination country; every route in scope ends there"
    ),
    RelevanceKeyword("islamabad", "place", "scope destination ISB"),
    RelevanceKeyword("lahore", "place", "scope destination LHE"),
    RelevanceKeyword("sialkot", "place", "scope destination SKT"),
    RelevanceKeyword("paris", "place", "the origin city, covering both CDG and ORY"),
    RelevanceKeyword("cdg", "place", "the origin airport code as wire copy writes it"),
    RelevanceKeyword("roissy", "place", "what French coverage calls CDG"),
    RelevanceKeyword("doha", "place", "Qatar Airways hub; a hub problem is a route problem"),
    RelevanceKeyword("dubai", "place", "Emirates/flydubai hub"),
    RelevanceKeyword("istanbul", "place", "Turkish Airlines hub"),
    RelevanceKeyword("jeddah", "place", "Saudia hub, and the Hajj gateway"),
    RelevanceKeyword("riyadh", "place", "Saudia's second hub"),
    RelevanceKeyword("bahrain", "place", "Gulf Air's hub; the carrier name rarely appears alone"),
    RelevanceKeyword("manama", "place", "Gulf Air's hub city as some wires name it"),
)

# What makes a `place` match aviation news rather than news from that place. Deliberately
# generic — this axis is not trying to be precise, it is trying to exclude the politics,
# sport and commodity copy that a country name otherwise drags in. French terms are here
# for the same reason `languages` includes `fr`: the CDG and DGAC side is written in it.
# "carrier" is *not* on the list: an aircraft carrier is a warship, and the scope carriers
# are all named on the `subject` axis anyway.
AVIATION_TERMS: tuple[str, ...] = (
    "flight",
    "flights",
    "airline",
    "airlines",
    "airport",
    "airports",
    "aviation",
    "airspace",
    "aircraft",
    "plane",
    "planes",
    "air travel",
    "runway",
    "terminal",
    "vol",
    "vols",
    "aérien",
    "aérienne",
    "aéroport",
)


# --- Response shape (only the fields used; everything else is ignored) -----------------


class _Lenient(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


def _text(value: Any) -> Any:
    """Coerce an explicit JSON `null` to the empty string.

    Every string field on this API is nullable in practice even where the docs show a
    string: 3 of the 100 rows in the recorded `airspace-disruption` fixture carry an
    article with `title: null` and `domain: null`. Rejecting those would fail the whole
    page over one incomplete article — the opposite of the permissive parsing this module
    promises — so a missing string is simply absent, and the callers that care
    (`headline`, `source_url`, `titles`) already skip empties."""
    return "" if value is None else value


Text = Annotated[str, BeforeValidator(_text)]


class StoryArticle(_Lenient):
    """One source article behind a Story. `top_articles[0]` is the canonical link."""

    url: Text = ""
    title: Text = ""
    domain: Text = ""


class StoryMetrics(_Lenient):
    """The cluster's measured size. `significance` is `null` when it was not measured."""

    significance: float | None = None
    article_count: int = 0


class Story(_Lenient):
    """One deduplicated cluster of articles about one development."""

    id: str
    title: str | None = None
    story_date: date
    metrics: StoryMetrics = StoryMetrics()
    top_articles: list[StoryArticle] = []
    # Present only on a `search=` request: which arm found the row, `semantic` or `name`.
    # **Both carry a `search_score`** — a `name` row is a lexical hit that is scored like any
    # other, not an unscored exact match, which is why `keep_search_hit` thresholds it. An
    # entity query returns neither field; there is no phrase to have scored.
    match_type: str | None = None
    search_score: float | None = None


class _Page(_Lenient):
    data: list[Story] = []


def source_url(story: Story) -> str:
    """The article to link to: the top-ranked source, else the Story's own page."""
    for article in story.top_articles:
        if article.url.strip():
            return article.url.strip()
    return f"{BASE_URL.removesuffix('/api/v2')}/stories/{story.id}"


def headline(story: Story) -> str:
    """The Story's title, falling back to its best article's."""
    if story.title and story.title.strip():
        return story.title.strip()
    for article in story.top_articles:
        if article.title.strip():
            return article.title.strip()
    return ""


def titles(story: Story) -> list[str]:
    """Everything the relevance guard is allowed to read: the cluster title and its
    articles' titles. Article bodies are not fetched — a keyword buried in paragraph nine
    is not what the story is about."""
    return [t for t in [story.title or "", *(a.title for a in story.top_articles)] if t.strip()]


def check_applied_filters(body: Mapping[str, Any], sent: Mapping[str, str]) -> None:
    """Raise unless the server echoed every filter we sent and ignored none of them.

    This is the guard against the API's characteristic failure: a filter it does not
    recognise is dropped, and the reply is a well-formed 200 describing a *different*
    question. `limit` and `cursor` are excluded by the caller — they are paging, not
    filters, and are echoed under `pagination` instead."""
    applied = body.get("applied_filters")
    if not isinstance(applied, dict):
        raise GdeltCloudResponseInvalid("response carries no applied_filters block")
    if ignored := applied.get("ignored"):
        raise GdeltCloudResponseInvalid(f"GDELT Cloud ignored filters: {ignored}")
    for key, value in sent.items():
        if key not in applied:
            raise GdeltCloudResponseInvalid(f"filter {key!r} was not applied")
        echoed = applied[key]
        # A comma list comes back as a JSON array, and numbers come back as strings;
        # compare on the values rather than on the wire form.
        wanted = value.split(",")
        got = [str(v) for v in echoed] if isinstance(echoed, list) else [str(echoed)]
        if sorted(got) != sorted(wanted):
            raise GdeltCloudResponseInvalid(
                f"filter {key!r} was applied as {echoed!r}, not {value!r}"
            )


def parse_page(payload: Mapping[str, Any]) -> tuple[list[Story], str | None]:
    """One `/stories` body as its cards and the cursor to the next page (`None` = last)."""
    try:
        page = _Page.model_validate(payload)
    except ValidationError as exc:
        raise GdeltCloudResponseInvalid(f"not a story page: {exc}") from exc
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        raise GdeltCloudResponseInvalid("response carries no pagination block")
    # `next_cursor` is the only truthful truncation signal: the server looks one row past
    # the page to set it, so a full last page reports None and a short page can report a
    # cursor. Never infer truncation from `len(data)`.
    cursor = pagination.get("next_cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise GdeltCloudResponseInvalid(f"next_cursor is {type(cursor).__name__}, not a string")
    return page.data, cursor


def is_relevant(story: Story) -> RelevanceKeyword | None:
    """The `RELEVANCE_KEYWORDS` entry this story is kept on, or None to drop it.

    A `subject` match wins outright. A `place` match is only accepted next to an
    `AVIATION_TERMS` word — see `RELEVANCE_KEYWORDS` for the measurement behind that.
    Subjects are tried first so the returned keyword is the most specific reason available,
    which is what `impact_note` prints and what a surprising row is traced back through."""
    haystack = " ".join(titles(story)).casefold()
    places: list[RelevanceKeyword] = []
    for keyword in RELEVANCE_KEYWORDS:
        if not _word_in(keyword.word, haystack):
            continue
        if keyword.axis == "subject":
            return keyword
        places.append(keyword)
    if places and any(_word_in(term, haystack) for term in AVIATION_TERMS):
        return places[0]
    return None


def _word_in(word: str, haystack: str) -> bool:
    """Whole-word containment, without a regex per call. Guards against 'PIA' inside
    'Olympia' and 'Doha' inside 'Dohan'."""
    start = 0
    while (index := haystack.find(word, start)) != -1:
        before = index == 0 or not haystack[index - 1].isalnum()
        end = index + len(word)
        after = end == len(haystack) or not haystack[end].isalnum()
        if before and after:
            return True
        start = index + 1
    return False


def keep_search_hit(story: Story) -> bool:
    """Whether one semantic-query row clears the precision bar.

    Every row is thresholded, including `match_type=name`. That is the opposite of what the
    name suggests and was measured rather than assumed. On 2026-09-09 the `cdg-strikes`
    query was still being truncated to the single token `"strike"` (see `SEMANTIC_QUERIES`),
    and that recording came back as 100 rows of `match_type=name` scoring 0.25–0.36 — the
    literal token matched in a hundred stories about football, politics and air raids. So a
    `name` row is a *lexical* hit, not a verified one, and exempting it would have waved the
    noisiest query straight past the filter.

    The committed fixtures no longer show this: with the corrected one-concept queries all
    three pools come back `semantic`. The rule is kept because `name` rows are a shape this
    API returns, not a shape it has stopped returning. A missing score is treated as 0 and
    dropped — unmeasured has cleared no bar."""
    return (story.search_score or 0.0) >= MIN_SEARCH_SCORE


# --- The client -----------------------------------------------------------------------


@dataclass(frozen=True)
class QueryUnits:
    """This key's position in the month's budget, from the free `/meta/query-units`."""

    plan: str
    remaining: int
    effective: int

    @property
    def is_low(self) -> bool:
        return self.remaining < LOW_QUERY_UNITS


@dataclass(frozen=True)
class Walk:
    """One completed query: the rows, and whether `MAX_ROWS` cut it short."""

    stories: list[Story]
    truncated: bool


class GdeltCloudClient:
    name = "gdeltcloud"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        retry_delay: float = RETRY_DELAY_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        max_rows: int = MAX_ROWS,
    ) -> None:
        self._api_key = api_key
        self._client = client if client is not None else httpx.Client(timeout=TIMEOUT_SECONDS)
        self._retry_delay = retry_delay
        self.sleep = sleep
        self._max_rows = max_rows

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> "GdeltCloudClient":
        if not settings.gdelt_api_key:
            raise GdeltCloudKeyMissing(
                "GDELT_API_KEY is not set; put it in .env or the environment"
            )
        return cls(settings.gdelt_api_key, **kwargs)

    def query_units(self) -> QueryUnits:
        """This key's remaining budget. Free: `/meta/*` has an endpoint weight of 0."""
        body = self._request(QUERY_UNITS_PATH, {})
        usage = body.get("usage")
        if not isinstance(usage, dict):
            raise GdeltCloudResponseInvalid("query-units response carries no usage block")
        allowance = usage.get("allowance") or {}
        try:
            return QueryUnits(
                plan=str(usage.get("plan_display_name") or usage.get("plan") or "unknown"),
                remaining=int(usage["remaining"]),
                effective=int(allowance.get("effective", 0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GdeltCloudResponseInvalid(f"query-units usage is not readable: {exc}") from exc

    def stories_for_carrier(self, carrier: Carrier, days: int) -> Walk:
        """That airline's coverage over the last `days` days, by resolved entity id.

        Paged to the end: an entity's coverage in a week is a finite set — 0 to 2 rows on
        the recorded fixtures — and completeness is the point of asking by id."""
        return self._walk({"entity": carrier.entity_id, **_window(days)})

    def stories_for_search(self, query: SemanticQuery, days: int) -> Walk:
        """One topical group's top page, score-filtered. **Deliberately not paged.**

        A semantic query does not return "the matches"; it returns a *bounded relevance
        pool* ranked by similarity, and the server says as much in
        `meta.search.coverage_complete: false` — "cursor exhaustion ends a bounded relevance
        pool and does not establish exhaustive matching coverage". So there is no end worth
        walking to: every extra page costs a query unit for rows that are, by construction,
        worse matches than the ones already in hand. All three recorded pools come back
        full with a `next_cursor` set whether or not anything relevant happened that week,
        so paging them would cost 3 extra units a day, every day, to fetch the tail of a
        ranking. One page of 100 is the top 100, which is what `sort=recent` and the
        relevance guard are there to work on.

        This is what keeps the daily run at the 9 calls the budget assumes."""
        page = self._page({"search": query.search, **_window(days)})
        return Walk([s for s in page if keep_search_hit(s)], truncated=False)

    def resolve(self, name: str) -> list[dict[str, Any]]:
        """Entity candidates for a name. Used only by the fixture recorder: at run time the
        ids in `CARRIERS` are already the answer."""
        body = self._request(SEARCH_PATH, {"q": name, "country_match": "strict"})
        data = body.get("data")
        if not isinstance(data, list):
            raise GdeltCloudResponseInvalid("search response carries no candidate list")
        return [row for row in data if isinstance(row, dict)]

    def fetch_raw(self, filters: Mapping[str, str], days: int) -> dict[str, Any]:
        """One unpaged `/stories` body, for the fixture recorder."""
        return self._request(STORIES_PATH, {**filters, **_window(days), "limit": str(PAGE_LIMIT)})

    def _page(self, filters: Mapping[str, str]) -> list[Story]:
        """The first page for one query, with the filter echo checked. One unit."""
        body = self._request(STORIES_PATH, {**filters, "limit": str(PAGE_LIMIT)})
        check_applied_filters(body, filters)
        return parse_page(body)[0]

    def _walk(self, filters: Mapping[str, str]) -> Walk:
        """Every row for one query, following `next_cursor` to the end or to `MAX_ROWS`."""
        stories: list[Story] = []
        cursor: str | None = None
        while True:
            params = {**filters, "limit": str(PAGE_LIMIT)}
            if cursor is not None:
                params["cursor"] = cursor
            body = self._request(STORIES_PATH, params)
            # Filters must be identical on every page of a walk, so the echo is checked on
            # each one: a cursor page that quietly widened the query is the same bug.
            check_applied_filters(body, filters)
            page, cursor = parse_page(body)
            stories.extend(page)
            if cursor is None:
                return Walk(stories, truncated=False)
            if len(stories) >= self._max_rows:
                return Walk(stories, truncated=True)

    def _request(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._get(path, params)
            except (GdeltCloudTransportError, GdeltCloudRateLimited, _ServerError) as exc:
                if attempt >= 2:
                    if isinstance(exc, _ServerError):
                        raise GdeltCloudError(str(exc)) from exc
                    raise
                self.sleep(_retry_after(exc, self._retry_delay))

    def _get(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        try:
            response = self._client.get(
                BASE_URL + path,
                params=dict(params),
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TransportError as exc:
            raise GdeltCloudTransportError(
                f"could not reach GDELT Cloud: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code >= 500:
            raise _ServerError(f"GDELT Cloud server error: HTTP {response.status_code}")
        body = self._body(response)
        if response.status_code in (401, 403):
            raise GdeltCloudAuthError(f"GDELT Cloud refused the key: {_error_text(body)}")
        if response.status_code == 429:
            raise _throttled(body)
        if response.status_code != 200:
            raise GdeltCloudError(f"GDELT Cloud HTTP {response.status_code}: {_error_text(body)}")
        return body

    @staticmethod
    def _body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise GdeltCloudResponseInvalid(
                f"GDELT Cloud did not answer with JSON: {response.text[:200]!r}"
            ) from exc
        if not isinstance(body, dict):
            raise GdeltCloudResponseInvalid("GDELT Cloud response is not a JSON object")
        return body


class _ServerError(Exception):
    """Internal: a 5xx, retried once before becoming a GdeltCloudError."""


def _window(days: int) -> dict[str, str]:
    """The filters that bound one query's window, shared by both retrieval arms."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}, got {days}")
    return {"days": str(days), "sort": "recent", "languages": LANGUAGES}


def _error_text(body: Mapping[str, Any]) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return f"{error.get('code', '?')}: {error.get('message', '')}".strip()
    return str(body.get("code") or body.get("message") or error or "no detail")


def _throttled(body: Mapping[str, Any]) -> GdeltCloudError:
    """Split one HTTP 429 into its two opposite meanings. See the module docstring."""
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    code = str(error.get("code", "")) if isinstance(error, dict) else ""
    if code == "QUOTA_EXCEEDED":
        return GdeltCloudQuotaError(f"GDELT Cloud query units exhausted: {_error_text(body)}")
    details = error.get("details") if isinstance(error, dict) else None
    retry_after = details.get("retry_after") if isinstance(details, dict) else None
    exc = GdeltCloudRateLimited(f"GDELT Cloud is throttling this caller: {_error_text(body)}")
    exc.retry_after = float(retry_after) if isinstance(retry_after, (int, float)) else None  # type: ignore[attr-defined]
    return exc


def _retry_after(exc: Exception, default: float) -> float:
    """How long to wait before the one retry. `RATE_LIMITED` says so in the body."""
    hinted = getattr(exc, "retry_after", None)
    return float(hinted) if isinstance(hinted, (int, float)) else default


# --- Fixture recording (manual; see the module docstring) -----------------------------


def fixture_path(fixtures_dir: Path, slug: str) -> Path:
    return fixtures_dir / f"{slug}.json"


def carrier_slug(carrier: Carrier) -> str:
    return "carrier-" + carrier.name.lower().replace(" ", "-")


def resolution_slug(carrier: Carrier) -> str:
    return "resolve-" + carrier.name.lower().replace(" ", "-")


def record_queries(
    client: GdeltCloudClient,
    days: int,
    fixtures_dir: Path,
    *,
    carriers: Sequence[Carrier] = CARRIERS,
    semantic: Sequence[SemanticQuery] = SEMANTIC_QUERIES,
) -> list[Path]:
    """Fetch every daily query once, in the mode it runs in, and save the raw bodies."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    jobs: list[tuple[str, dict[str, str]]] = [
        *((carrier_slug(c), {"entity": c.entity_id}) for c in carriers),
        *((q.slug, {"search": q.search}) for q in semantic),
    ]
    for index, (slug, filters) in enumerate(jobs):
        if index:
            client.sleep(REQUEST_INTERVAL_SECONDS)
        path = fixture_path(fixtures_dir, slug)
        path.write_text(_dump(client.fetch_raw(filters, days)))
        written.append(path)
    return written


def record_resolutions(
    client: GdeltCloudClient,
    fixtures_dir: Path,
    *,
    carriers: Sequence[Carrier] = CARRIERS,
) -> list[Path]:
    """Save one `/search` candidate list per carrier, so `CARRIERS` can be re-argued."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, carrier in enumerate(carriers):
        if index:
            client.sleep(REQUEST_INTERVAL_SECONDS)
        path = fixture_path(fixtures_dir, resolution_slug(carrier))
        path.write_text(
            _dump({"query": carrier.resolved_from, "data": client.resolve(carrier.resolved_from)})
        )
        written.append(path)
    return written


def _dump(body: Mapping[str, Any]) -> str:
    return json.dumps(body, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def _main(argv: list[str]) -> int:
    from flight_detective.config import load_settings

    if not argv or argv[0] not in ("record", "resolve"):
        print("usage: python -m flight_detective.news.gdeltcloud (record|resolve) [DAYS]")
        return 2
    try:
        client = GdeltCloudClient.from_settings(load_settings())
        if argv[0] == "resolve":
            for path in record_resolutions(client, RECORDING_DIR):
                print(path)
        else:
            days = int(argv[1]) if len(argv) > 1 else DEFAULT_DAYS
            for path in record_queries(client, days, RECORDING_DIR):
                print(path)
    except (GdeltCloudError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
