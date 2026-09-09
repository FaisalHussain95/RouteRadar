"""`dashboard.json`: everything the static site reads, in one versioned file.

The shape is dictated by `specs/ux/design-system.md` § Data the page needs, region by
region, because the site has no API to ask a follow-up question with — if the page needs
something it is not here, the export grows and `SCHEMA_VERSION` bumps.

## What is data and what is reference

Three regions never come from the database: `carriers`, `destinations` and `horizons`.
They are the filter bar, and the filter bar has to render before the first ingest, so they
are the tables below rather than a `SELECT DISTINCT`. `bands` is the same argument one step
further: it comes from `calendar_engine`, which answers for any date whether or not a fare
was ever seen, so the chart draws its seasonal shading over an empty plot.

Everything else is a query. `arbitrage` and `seasonal_gauge` are **nullable** and
`series`/`events`/`efficiency` may be empty: a partial database cannot answer them, and a
card that prints `€0` or `+0%` because it has no data is worse than one that says so.

`news_status` is the exception that proves the rule. An empty `events` list is ambiguous —
it is what a quiet week and a week of failed queries both look like — so the region that
would otherwise be read as "no news" carries the run log's own answer beside it.

## Money, dates and floats

Prices are whole euros, as integers: every provider seen quotes whole euros, `Decimal`
serialises to a JSON *string* under Pydantic, and a float price is exactly what
`models.Money` exists to keep out. Durations are integer minutes for the same reason. The
band multipliers are the one exception and are plain floats — they are a display hint on a
band chip, never an input to arithmetic that has to be exact.

## The window

The file covers `generated_at - HISTORY` to `generated_at + FORWARD`, and series, bands and
events are all clipped to it. It reaches *backwards* because news is: every pin the chart
can draw sits in the past, and a chart that began at today would have an event feed whose
rows pointed off the left edge. Forward it is the
year `fd tag-dates` tags, so no band names a window `calendar_tag` has no rows for.
"""

import os
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import duckdb
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from flight_detective import db
from flight_detective.analytics import queries
from flight_detective.calendar_engine.gregorian import BandKind
from flight_detective.calendar_engine.tags import tags_for_range, window_for
from flight_detective.ingest import DEFAULT_HORIZONS
from flight_detective.models import AwareDatetime, CalendarTag, IngestRun

# Bumped whenever a reader would have to change: a removed or renamed field, or a new
# required one. The site checks it before trusting the rest of the file.
SCHEMA_VERSION = 2

# How far back the file reaches. This used to be `news/gdelt.py`'s `MAX_DAYS` — the DOC 2.0
# archive length — on the reasoning that no pin could sit before it. That coupling is gone
# with the DOC client and must not be re-made against GDELT Cloud's `MAX_DAYS`, which is a
# *per-query* window cap of 30 days, not an archive length: the daily run asks for a week
# at a time and `news_event` accumulates, so the table holds events far older than any one
# query could reach. 90 days is now simply the dashboard's chosen depth, hand-copied into
# `web/src/lib/select.ts` as `HISTORY_DAYS` (see the test that pins the two together).
HISTORY = timedelta(days=90)

# How far forward: the range `fd tag-dates` tags by default (cli.DEFAULT_TAG_SPAN), which
# also covers the longest ingest horizon four times over.
FORWARD = timedelta(days=365)

# The M-11 motorway run between Sialkot and Lahore, as the design's arbitrage card prints
# it. A hand-set input beside `queries.DEFAULT_BREAK_EVEN_EUR`, not a measurement.
GROUND_TIME = "4h 10m"

# What the gauge is actually comparing, printed under it. The design's card says "current
# average vs February baseline"; the query behind it is the wedding premium, and the page
# should not have to know that from the code.
GAUGE_METHOD = "Wedding-window departures against the February/March baseline"

PARIS = ZoneInfo("Europe/Paris")

# Whose `ingest_run` rows answer "is the news half still working". One news source by
# decision (`specs/architecture.md` § news), so this is a literal rather than a max() over
# providers; `tests/test_export.py` pins it to the client's own `name`.
NEWS_PROVIDER = "gdeltcloud"

# How a quota failure is recognised in `ingest_run.error`. It is the one failure worth
# naming on the page — nothing retries its way out of it before the month rolls over —
# hence the special case in `_news_error_line`.
#
# Matched against `GdeltCloudQuotaError`'s own words, which are on the failing query's line.
# **Not** against the `not issued after quota exhausted:` line `run_news_ingest` appends
# after it: that one is written only `if skipped`, so quota dying on the last query of the
# run would leave it out and the failure would go unnamed. `test_export.py` pins this to the
# real error text through the real ingest, since it is a string agreed across two modules.
_QUOTA_MARKER = "query units exhausted"

Severity = Literal["high", "med", "low"]

# `news_event.severity` is 1–3 (news/ingest.py § The severity heuristic); the design's
# pins and feed dots are named.
_SEVERITY_NAMES: dict[int, Severity] = {3: "high", 2: "med", 1: "low"}

_WHOLE = Decimal("1")


class ExportModel(BaseModel):
    """Frozen and closed, like `models.Base`."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Carrier(ExportModel):
    """One line on the chart, one chip in the filter bar, one dot in the scatter."""

    code: str
    name: str
    color: str = Field(description="`--carrier-<code>` from specs/ux/design-system.md § Tokens")
    hub: str | None = Field(description="Transit airport, or null when the carrier flies direct")
    direct: bool
    typical_transit_minutes: int = Field(
        description="Reference door-to-door time, for the hover card before a fare is loaded"
    )


class DestinationInfo(ExportModel):
    code: str
    label: str


class SeriesPoint(ExportModel):
    departure_date: date
    price_eur: int


class CarrierSeries(ExportModel):
    """The lowest fare per departure date for one (destination, horizon, carrier)."""

    destination: str
    horizon_days: int
    carrier: str
    points: list[SeriesPoint]


class Band(ExportModel):
    """One edition of a calendar window, clipped to the export window."""

    tag: str
    label: str
    short_label: str
    kind: BandKind
    # `from` and `to` are the names the design uses and neither is a legal Python
    # identifier. Only the *serialization* alias is set: an `alias=` would rename the
    # constructor argument too, which type checkers then reject as `Band(start=...)`.
    start: date = Field(serialization_alias="from", validation_alias=AliasChoices("from", "start"))
    end: date = Field(serialization_alias="to", validation_alias=AliasChoices("to", "end"))
    multiplier_low: float
    multiplier_high: float


class Event(ExportModel):
    """One `news_event` row as the feed, the drawer and the chart pins need it."""

    date: date
    severity: Severity
    headline: str
    source: str = Field(description="Publisher host, for the feed's `date · source` line")
    source_url: str
    impact_text: str | None = Field(
        description="The ingest's own note on the row: how many outlets carried it and which "
        "keyword set the severity. v1 has no estimate of the fare impact itself."
    )
    body: str | None = Field(
        description="Always null in v1: GDELT's DOC 2.0 artlist carries titles and URLs, "
        "not article text."
    )


class Arbitrage(ExportModel):
    """Lahore against Sialkot on one departure date, with the ground transfer priced in."""

    departure_date: date
    lhe_eur: int
    skt_eur: int
    isb_eur: int | None
    spread_eur: int = Field(
        description="`lhe_eur - skt_eur`, as analytics defines it: positive means Sialkot is "
        "the cheaper airport. The design's card captions this the other way round."
    )
    ground_transfer_eur: int
    ground_time: str
    verdict: queries.Verdict


class Efficiency(ExportModel):
    """One dot of the price-against-transit-time scatter."""

    carrier: str
    price_eur: int
    transit_minutes: int


class NewsStatus(ExportModel):
    """Whether the news half of the pipeline is still working, regardless of what it found.

    A week in which nothing happened and a week in which every query failed both export an
    empty `events` list, and the run log is the only thing that tells them apart — so the
    feed reads its health from here rather than from its own row count."""

    last_success: date | None = Field(
        description="Europe/Paris day of the newest news run whose queries answered; null "
        "before the news step has ever succeeded"
    )
    last_error: str | None = Field(
        description="One line naming why the newest failing news run failed; null if none "
        "has ever failed. Exported even when a later run recovered — the page shows it only "
        "beside a stale last_success, and dropping it here would lose the reason."
    )


class SeasonalGauge(ExportModel):
    current_avg_eur: int
    baseline_avg_eur: int
    pct: int
    method: str


class DashboardData(ExportModel):
    schema_version: int
    generated_at: AwareDatetime
    observed_on: date | None = Field(
        description="Latest day the fares were observed; null before the first ingest"
    )
    carriers: list[Carrier]
    destinations: list[DestinationInfo]
    horizons: list[int]
    series: list[CarrierSeries]
    bands: list[Band]
    events: list[Event]
    news_status: NewsStatus
    arbitrage: Arbitrage | None
    efficiency: list[Efficiency]
    seasonal_gauge: SeasonalGauge | None


# Reference data, straight off the design's `CARRIERS` constant, with the colours replaced
# by the S13 palette (`RouteRadar.dc.html` still carries the pre-S13 hexes; the spec wins).
# Six is the ceiling — a seventh carrier is a design change, not a row here — so a code
# that is not in this table is left out of the export rather than drawn without a colour.
# The scope filters are not carrier filters, so a real ingest does return other codes; the
# restriction is applied to *every* region below, not just the two the chart draws, or the
# arbitrage card would print a spread no line on the chart could account for.
CARRIERS: tuple[Carrier, ...] = (
    Carrier(
        code="PK", name="PIA", color="#53a000", hub=None, direct=True, typical_transit_minutes=486
    ),
    Carrier(
        code="QR",
        name="Qatar",
        color="#f33a87",
        hub="DOH",
        direct=False,
        typical_transit_minutes=744,
    ),
    Carrier(
        code="EK",
        name="Emirates",
        color="#b44900",
        hub="DXB",
        direct=False,
        typical_transit_minutes=834,
    ),
    Carrier(
        code="GF",
        name="Gulf Air",
        color="#9d3ebf",
        hub="BAH",
        direct=False,
        typical_transit_minutes=936,
    ),
    Carrier(
        code="TK",
        name="Turkish",
        color="#0565fc",
        hub="IST",
        direct=False,
        typical_transit_minutes=672,
    ),
    Carrier(
        code="SV",
        name="Saudia",
        color="#1d93a6",
        hub="JED",
        direct=False,
        typical_transit_minutes=888,
    ),
)

# A tuple, not a set: it goes into an SQL `IN (...)` list, and a stable order keeps the
# generated statement — and so DuckDB's plan cache — the same from one run to the next.
CARRIER_CODES: tuple[str, ...] = tuple(c.code for c in CARRIERS)

# The design's segmented control shows the codes; the cities are here so a message like
# "no fares for Lahore at 60d" can name the place rather than the airport.
DESTINATIONS: tuple[DestinationInfo, ...] = (
    DestinationInfo(code="ISB", label="Islamabad"),
    DestinationInfo(code="LHE", label="Lahore"),
    DestinationInfo(code="SKT", label="Sialkot"),
)


def _euros(value: Decimal) -> int:
    """A price as a whole number of euros, rounded half up."""
    return int(value.quantize(_WHOLE, rounding=ROUND_HALF_UP))


def _publisher(url: str) -> str:
    """The host a story came from, `www.` dropped. The raw URL if it has no host: a feed
    row with a visible oddity beats one with a blank source."""
    host = urlsplit(url).hostname
    if host is None:
        return url
    return host[len("www.") :] if host.startswith("www.") else host


def _bands(start: date, end: date) -> list[Band]:
    """Calendar windows over `start..end`, as contiguous runs of tagged days.

    Built from `tags_for_range` rather than from each window's `span()` so the bands are
    exactly the rows `fd tag-dates` would write — a window whose edition is half outside
    the export window comes back clipped, and one that recurs twice inside it (Ramadan
    drifts ~11 days a year) comes back as two bands, both without a special case."""
    runs: dict[str, list[tuple[CalendarTag, date]]] = {}
    for tag in tags_for_range(start, end):
        run = runs.setdefault(tag.tag, [])
        if run and run[-1][1] == tag.date - timedelta(days=1):
            run[-1] = (run[-1][0], tag.date)
        else:
            run.append((tag, tag.date))
    bands: list[Band] = []
    for name, run in runs.items():
        window = window_for(name)
        if window is None:
            # `tags_for_range` only ever emits tags the engine owns, so this cannot happen
            # today; skipping keeps a future retired window out of a KeyError anyway.
            continue
        bands.extend(
            Band(
                tag=name,
                label=window.label,
                short_label=window.short_label,
                kind=window.kind,
                start=first.date,
                end=last,
                multiplier_low=float(first.multiplier_low),
                multiplier_high=float(first.multiplier_high),
            )
            for first, last in run
        )
    return sorted(bands, key=lambda b: (b.start, b.tag))


def _series(
    conn: duckdb.DuckDBPyConnection, start: date, end: date, horizons: tuple[int, ...]
) -> list[CarrierSeries]:
    grouped: dict[tuple[str, int, str], list[SeriesPoint]] = {}
    for point in queries.fare_series(
        conn, horizons=horizons, start=start, end=end, carriers=CARRIER_CODES
    ):
        key = (point.destination, point.horizon_days, point.carrier)
        grouped.setdefault(key, []).append(
            SeriesPoint(departure_date=point.departure_date, price_eur=_euros(point.price_eur))
        )
    # `fare_series` already orders by the key then the departure date, so the groups and
    # their points come out sorted and nothing has to be re-sorted here.
    return [
        CarrierSeries(destination=destination, horizon_days=horizon, carrier=carrier, points=points)
        for (destination, horizon, carrier), points in grouped.items()
    ]


def _events(conn: duckdb.DuckDBPyConnection, start: date, end: date) -> list[Event]:
    """Stored events inside the window, oldest first — the axis the chart pins them on.

    `fetch_news_events` answers newest first for a feed; sorted here rather than reversed so
    two events on one day keep the same tie-break (`dedupe_key`) either way round."""
    return [
        Event(
            date=event.event_date,
            severity=_SEVERITY_NAMES[event.severity],
            headline=event.headline,
            source=_publisher(event.source_url),
            source_url=event.source_url,
            impact_text=event.impact_note,
            body=None,
        )
        for event in sorted(
            db.fetch_news_events(conn, start, end), key=lambda e: (e.event_date, e.dedupe_key)
        )
    ]


def _news_succeeded(run: IngestRun) -> bool:
    """Did this run's queries actually reach GDELT Cloud?

    Not `rows_kept > 0`, which is the obvious reading and the wrong one: the relevance
    guard throws away nearly everything it is shown — 300 rows down to 16 on S17's recorded
    week — so a real day can keep nothing while all nine queries answered perfectly. Scoring
    that as a failure would put "News unavailable" on the page for precisely the quiet week
    the feed is supposed to state plainly. A run counts when it kept rows (proof the calls
    landed) or when it issued queries and recorded no failure at all."""
    return run.queries > 0 and (run.rows_kept > 0 or run.error is None)


def _news_error_line(run: IngestRun) -> str:
    """The newest failure as one line, saying which of the two shapes it is.

    Quota exhaustion is a sizing problem that lasts until the month rolls over and is worth
    naming; a dead query or two is usually transient, so its own first line is quoted and
    the rest are counted rather than spilled onto a dashboard."""
    lines = (run.error or "").splitlines()
    if any(_QUOTA_MARKER in line for line in lines):
        count = run.queries
        return f"GDELT Cloud query units exhausted after {count} quer{'y' if count == 1 else 'ies'}"
    if not lines:
        return "the news run failed without saying why"
    extra = f" (+{len(lines) - 1} more)" if len(lines) > 1 else ""
    return f"{lines[0]}{extra}"


def _news_status(conn: duckdb.DuckDBPyConnection) -> NewsStatus:
    """The news half's health, read out of the `ingest_run` log.

    The two fields are answered independently of each other, so a run that failed a query
    but still kept rows sets both: the query is worth naming if the feed ever goes stale,
    and the run is still a success today."""
    runs = [run for run in db.fetch_ingest_runs(conn) if run.provider == NEWS_PROVIDER]
    successes = [run for run in runs if _news_succeeded(run)]
    failures = [run for run in runs if run.error]
    # `fetch_ingest_runs` orders oldest first, so the newest of each is the last one.
    return NewsStatus(
        last_success=successes[-1].run_at.astimezone(PARIS).date() if successes else None,
        last_error=_news_error_line(failures[-1]) if failures else None,
    )


def _arbitrage(
    conn: duckdb.DuckDBPyConnection, observed_on: date | None, break_even_eur: Decimal
) -> Arbitrage | None:
    """The nearest departure, on the latest observation day, that was priced into both
    Lahore and Sialkot.

    The card is "what would I pay to go", so it is the snapshot question rather than the
    cheapest the market ever showed, and the nearest departure is the one a reader is
    deciding about. `None` when no date has both airports: a spread needs two prices."""
    if observed_on is None:
        return None
    rows = queries.airport_arbitrage(
        conn, break_even_eur=break_even_eur, observed_on=observed_on, carriers=CARRIER_CODES
    )
    if not rows:
        return None
    row = rows[0]
    return Arbitrage(
        departure_date=row.departure_date,
        lhe_eur=_euros(row.lhe_eur),
        skt_eur=_euros(row.skt_eur),
        isb_eur=None if row.isb_eur is None else _euros(row.isb_eur),
        spread_eur=_euros(row.spread_eur),
        ground_transfer_eur=_euros(row.break_even_eur),
        ground_time=GROUND_TIME,
        verdict=row.verdict,
    )


def _efficiency(conn: duckdb.DuckDBPyConnection) -> list[Efficiency]:
    return [
        Efficiency(
            carrier=row.carrier,
            price_eur=_euros(row.avg_price_eur),
            # Recovered from the reported average hours rather than re-queried: the round
            # trip through two decimal places moves a dot by less than half a minute.
            transit_minutes=_euros(row.avg_duration_hours * 60),
        )
        for row in queries.carrier_efficiency(conn, carriers=CARRIER_CODES)
    ]


def _seasonal_gauge(conn: duckdb.DuckDBPyConnection) -> SeasonalGauge | None:
    premium = queries.wedding_premium(conn, carriers=CARRIER_CODES)
    if premium is None:
        return None
    return SeasonalGauge(
        current_avg_eur=_euros(premium.wedding_avg_eur),
        baseline_avg_eur=_euros(premium.baseline_avg_eur),
        pct=int(premium.premium_pct.quantize(_WHOLE, rounding=ROUND_HALF_UP)),
        method=GAUGE_METHOD,
    )


def latest_observation(conn: duckdb.DuckDBPyConnection) -> date | None:
    """The most recent day fares were seen, or `None` on a database with no fares."""
    row = conn.execute("SELECT max(observed_on) FROM fare_observation").fetchone()
    if row is None or row[0] is None:
        return None
    observed_on: date = row[0]
    return observed_on


def build_dashboard(
    conn: duckdb.DuckDBPyConnection,
    *,
    generated_at: datetime,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    break_even_eur: Decimal = queries.DEFAULT_BREAK_EVEN_EUR,
) -> DashboardData:
    """Everything the page needs, read out of `conn` in one pass per region.

    `generated_at` is a parameter rather than `now()` because the export window is anchored
    on it: a caller replaying a day gets that day's file, and the tests get a fixed one."""
    today = generated_at.date()
    start, end = today - HISTORY, today + FORWARD
    observed_on = latest_observation(conn)
    return DashboardData(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        observed_on=observed_on,
        carriers=list(CARRIERS),
        destinations=list(DESTINATIONS),
        horizons=list(horizons),
        series=_series(conn, start, end, horizons),
        bands=_bands(start, end),
        events=_events(conn, start, end),
        news_status=_news_status(conn),
        arbitrage=_arbitrage(conn, observed_on, break_even_eur),
        efficiency=_efficiency(conn),
        seasonal_gauge=_seasonal_gauge(conn),
    )


def render(data: DashboardData) -> str:
    """The file's exact bytes, as text. Indented and newline-terminated: it is committed
    and pushed by `deploy/push-data.sh`, so a diff between two days has to be readable."""
    return data.model_dump_json(by_alias=True, indent=2) + "\n"


def write_dashboard(data: DashboardData, path: Path) -> Path:
    """Write `data` to `path` atomically, and return the path.

    A sibling `.tmp` plus `os.replace` rather than a write in place: the site build and
    `deploy/push-data.sh` read this file on a schedule, and a reader that opens it while
    the pipeline is writing must get the whole previous file or the whole new one. The
    temp file is a fixed sibling name (not `tempfile`) so a crashed run leaves one
    identifiable file to delete rather than a growing pile of random ones, and it is
    removed on any failure so a half-written export is never left lying next to a good one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(render(data))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return path
