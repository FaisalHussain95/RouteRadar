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

## Money, dates and floats

Prices are whole euros, as integers: every provider seen quotes whole euros, `Decimal`
serialises to a JSON *string* under Pydantic, and a float price is exactly what
`models.Money` exists to keep out. Durations are integer minutes for the same reason. The
band multipliers are the one exception and are plain floats — they are a display hint on a
band chip, never an input to arithmetic that has to be exact.

## The window

The file covers `generated_at - HISTORY` to `generated_at + FORWARD`, and series, bands and
events are all clipped to it. It reaches *backwards* because news is: GDELT's archive is the
last 90 days, so every pin the chart can ever draw sits in the past, and a chart that began
at today would have an event feed whose rows pointed off the left edge. Forward it is the
year `fd tag-dates` tags, so no band names a window `calendar_tag` has no rows for.
"""

import os
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import duckdb
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from flight_detective import db
from flight_detective.analytics import queries
from flight_detective.calendar_engine.gregorian import BandKind
from flight_detective.calendar_engine.tags import tags_for_range, window_for
from flight_detective.ingest import DEFAULT_HORIZONS
from flight_detective.models import AwareDatetime, CalendarTag
from flight_detective.news.gdelt import MAX_DAYS

# Bumped whenever a reader would have to change: a removed or renamed field, or a new
# required one. The site checks it before trusting the rest of the file.
SCHEMA_VERSION = 1

# How far back the file reaches, and why: GDELT's DOC 2.0 archive is 90 days, so an event
# older than this cannot be in `news_event` and no pin can sit before it.
HISTORY = timedelta(days=MAX_DAYS)

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
