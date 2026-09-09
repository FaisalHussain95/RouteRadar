"""The five research questions of PRD § F5, as functions over DuckDB returning records.

Each function is one read-only query (two for the wedding premium) plus the arithmetic
DuckDB cannot do exactly. `fd report` prints them; `export/site.py` exports them. Nothing
here writes. `fare_series` is the one function no F5 question asks for: it is the chart's
own shape, added by S14, and it lives here because this is where reads of
`fare_observation` live.

## Averages are computed in Python, from `sum` and `count`

DuckDB's `avg()` over a `DECIMAL` returns a `DOUBLE`, and a float is exactly how
`611.9999999` gets into a fare table — the reason `models.Money` rejects floats outright.
So every aggregate asks for `sum(price_eur)` (an exact `DECIMAL(38,2)`) and `count(*)`,
and divides in `Decimal`, quantized to the cent with `ROUND_HALF_UP`. Ratios divide the
*sums* rather than the rounded averages, so a premium or a price-per-hour never carries
the error of two roundings.

## What "lowest fare" means here

`fare_observation` holds one row per itinerary per day it was seen, so the same departure
appears once per observation day and once per carrier. Every question below aggregates
over all of that: the lowest-fare curve is the cheapest the market ever showed, the
arbitrage rows are the cheapest way into each airport on a departure date. That is the
traveller's question ("what could I have paid"), not the snapshot question ("what does it
cost today"); `airport_arbitrage(observed_on=...)` is the pin for the latter, and the
other functions take the same scope filters so a caller can narrow rather than re-query.

## Seasonal bands come from `calendar_tag`, and a date can be in several

`lead_time_curve` joins each departure to its calendar tags, so a departure in both the
wedding rush and the Christmas window contributes to *both* band curves. That is what a
band curve means — "what do fares do inside this window" — and the alternative (picking
one tag per date) would need a precedence order that nothing in the PRD supplies. It also
means the observation counts across bands sum to more than the table. Departures with no
tag land in `UNTAGGED_BAND`, which is a real answer, not a hole: it is the off-peak curve
every band is read against.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import duckdb

from flight_detective.ingest import DEFAULT_HORIZONS
from flight_detective.models import Destination, Origin

CENTS = Decimal("0.01")

# The design's `ground_transfer_eur` (specs/ux/design-system.md § Data the page needs):
# what the M-11 motorway run between Sialkot and Lahore costs per seat. A hand-set input
# like the calendar multipliers, and the reason the parameter exists at all.
DEFAULT_BREAK_EVEN_EUR = Decimal("34")

# Brainstorming § 4 names February/March the off-peak baseline the wedding rush is read
# against: the months furthest from both the wedding rush and the French holidays.
BASELINE_MONTHS: tuple[int, ...] = (2, 3)

WEDDING_TAG = "wedding_rush"

# `calendar_tag` stores only the windows that apply, so "no window" has no row and no
# name. It needs one here: it is the curve the banded ones are compared against.
UNTAGGED_BAND = "off_peak"

# `lhe` / `skt`: that airport is cheaper by more than the ground transfer, so it wins
# outright. `either`: the spread is inside the transfer cost and the choice is about
# convenience, not money.
Verdict = Literal["lhe", "skt", "either"]


@dataclass(frozen=True)
class LowestFarePoint:
    """One point of the lowest-fare curve: the cheapest fare on a carrier in a month."""

    carrier: str
    departure_month: date
    """First day of the departure month."""
    price_eur: Decimal
    observations: int


@dataclass(frozen=True)
class FareSeriesPoint:
    """One point of the dashboard's fare chart: a curve is every point sharing the first
    three fields, ordered by departure date."""

    destination: str
    horizon_days: int
    carrier: str
    departure_date: date
    price_eur: Decimal


@dataclass(frozen=True)
class ArbitrageRow:
    """Lahore against Sialkot for one departure date, with the ground transfer priced in."""

    departure_date: date
    lhe_eur: Decimal
    skt_eur: Decimal
    isb_eur: Decimal | None
    """Islamabad on the same date, for context; `None` when nothing was seen."""
    spread_eur: Decimal
    """`lhe_eur - skt_eur`: positive means Sialkot is the cheaper airport."""
    break_even_eur: Decimal
    verdict: Verdict


@dataclass(frozen=True)
class LeadTimePoint:
    """One point of the lead-time curve: a seasonal band at one booking horizon."""

    band: str
    horizon_days: int
    min_price_eur: Decimal
    avg_price_eur: Decimal
    observations: int


@dataclass(frozen=True)
class WeddingPremium:
    """What the wedding window costs over the February/March baseline."""

    tag: str
    baseline_months: tuple[int, ...]
    wedding_avg_eur: Decimal
    baseline_avg_eur: Decimal
    multiplier: Decimal
    """`wedding_avg_eur / baseline_avg_eur`, comparable to `calendar_tag.multiplier_*`."""
    premium_pct: Decimal
    wedding_observations: int
    baseline_observations: int


@dataclass(frozen=True)
class CarrierEfficiency:
    """A carrier's price per hour of total transit: the value-leader index."""

    carrier: str
    avg_price_eur: Decimal
    avg_duration_hours: Decimal
    eur_per_hour: Decimal
    observations: int


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _scope(
    prefix: str,
    origin: Origin | None,
    destination: Destination | None,
    carrier: str | None,
    carriers: Sequence[str] | None = None,
) -> tuple[list[str], list[object]]:
    """The filters every question shares, as SQL fragments and their bound values.

    `carriers` restricts to a *set* of codes, where `carrier` picks one. The export uses it
    to hold every region of `dashboard.json` to the six carriers the palette has colours
    for; an empty sequence means "no carriers" and correctly matches nothing."""
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (("origin", origin), ("destination", destination), ("carrier", carrier)):
        if value is not None:
            clauses.append(f"{prefix}{column} = ?")
            params.append(value)
    if carriers is not None:
        codes = list(carriers)
        clauses.append(
            f"{prefix}carrier IN ({', '.join('?' for _ in codes)})" if codes else "false"
        )
        params.extend(codes)
    return clauses, params


def _where(clauses: Sequence[str]) -> str:
    return f" WHERE {' AND '.join(clauses)}" if clauses else ""


def _horizon_bucket_sql(expr: str, horizons: Sequence[int]) -> str:
    """A CASE mapping a lead time in days to the nearest configured horizon.

    A run that slips a day lands 13 or 15 days out rather than 14, and a horizon added
    later shifts every boundary, so rows are bucketed by proximity instead of by equality.
    The comparison is `days * 2 <= lo + hi` to keep the midpoint in integers; a lead time
    exactly between two horizons goes to the shorter one, arbitrarily but consistently."""
    ordered = sorted({int(h) for h in horizons})
    if not ordered:
        raise ValueError("lead_time_curve needs at least one horizon")
    if len(ordered) == 1:
        return str(ordered[0])
    branches = " ".join(
        f"WHEN ({expr}) * 2 <= {lo + hi} THEN {lo}"
        for lo, hi in zip(ordered, ordered[1:], strict=False)
    )
    return f"CASE {branches} ELSE {ordered[-1]} END"


def lowest_fare_curve(
    conn: duckdb.DuckDBPyConnection,
    *,
    origin: Origin | None = None,
    destination: Destination | None = None,
    carrier: str | None = None,
) -> list[LowestFarePoint]:
    """PRD F5: the lowest fare seen per carrier per departure month, carrier then month."""
    clauses, params = _scope("", origin, destination, carrier)
    rows = conn.execute(
        "SELECT carrier, CAST(date_trunc('month', departure_date) AS DATE) AS departure_month, "
        "min(price_eur), count(*) "
        f"FROM fare_observation{_where(clauses)} "
        "GROUP BY carrier, departure_month ORDER BY carrier, departure_month",
        params,
    ).fetchall()
    return [
        LowestFarePoint(
            carrier=str(row[0]),
            departure_month=row[1],
            price_eur=_money(Decimal(row[2])),
            observations=int(row[3]),
        )
        for row in rows
    ]


def fare_series(
    conn: duckdb.DuckDBPyConnection,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    start: date | None = None,
    end: date | None = None,
    origin: Origin | None = None,
    destination: Destination | None = None,
    carrier: str | None = None,
    carriers: Sequence[str] | None = None,
) -> list[FareSeriesPoint]:
    """The dashboard's chart data: the lowest fare per departure date, per destination,
    booking horizon and carrier (S14). `start`/`end` bound the departure date inclusively.

    Origins are pooled on purpose: the design's origin control is display-only (`CDG / ORY`)
    and a reader comparing carriers wants the cheapest way out of Paris, not out of one
    terminal. `origin=` is still there for a caller that disagrees.

    Horizons are bucketed to the nearest configured one, exactly as `lead_time_curve` does,
    so a run that slipped a day still lands on the 14d curve rather than vanishing from
    every curve."""
    clauses, params = _scope("", origin, destination, carrier, carriers)
    clauses.append("departure_date >= observed_on")
    for column, bound in (("departure_date >= ?", start), ("departure_date <= ?", end)):
        if bound is not None:
            clauses.append(column)
            params.append(bound)
    bucket = _horizon_bucket_sql("departure_date - observed_on", horizons)
    rows = conn.execute(
        f"SELECT destination, {bucket} AS horizon_days, carrier, departure_date, min(price_eur) "
        f"FROM fare_observation{_where(clauses)} "
        "GROUP BY destination, horizon_days, carrier, departure_date "
        "ORDER BY destination, horizon_days, carrier, departure_date",
        params,
    ).fetchall()
    return [
        FareSeriesPoint(
            destination=str(row[0]),
            horizon_days=int(row[1]),
            carrier=str(row[2]),
            departure_date=row[3],
            price_eur=_money(Decimal(row[4])),
        )
        for row in rows
    ]


def airport_arbitrage(
    conn: duckdb.DuckDBPyConnection,
    *,
    break_even_eur: Decimal = DEFAULT_BREAK_EVEN_EUR,
    origin: Origin | None = None,
    observed_on: date | None = None,
    carriers: Sequence[str] | None = None,
) -> list[ArbitrageRow]:
    """PRD F5: Lahore against Sialkot per departure date, versus the ground transfer.

    Only departure dates with a fare into *both* airports are returned: a spread needs
    two prices, and a date where one airport was simply never quoted would otherwise read
    as an infinite saving. Islamabad rides along for context and may be `None`."""
    clauses, params = _scope("", origin, None, None, carriers)
    if observed_on is not None:
        clauses.append("observed_on = ?")
        params.append(observed_on)
    rows = conn.execute(
        "SELECT * FROM ("
        "  SELECT departure_date,"
        "         min(price_eur) FILTER (destination = 'LHE') AS lhe_eur,"
        "         min(price_eur) FILTER (destination = 'SKT') AS skt_eur,"
        "         min(price_eur) FILTER (destination = 'ISB') AS isb_eur"
        f"  FROM fare_observation{_where(clauses)}"
        "   GROUP BY departure_date"
        ") WHERE lhe_eur IS NOT NULL AND skt_eur IS NOT NULL ORDER BY departure_date",
        params,
    ).fetchall()
    result: list[ArbitrageRow] = []
    for row in rows:
        lhe = _money(Decimal(row[1]))
        skt = _money(Decimal(row[2]))
        spread = lhe - skt
        verdict: Verdict = "either"
        if spread > break_even_eur:
            verdict = "skt"
        elif -spread > break_even_eur:
            verdict = "lhe"
        result.append(
            ArbitrageRow(
                departure_date=row[0],
                lhe_eur=lhe,
                skt_eur=skt,
                isb_eur=None if row[3] is None else _money(Decimal(row[3])),
                spread_eur=spread,
                break_even_eur=break_even_eur,
                verdict=verdict,
            )
        )
    return result


def lead_time_curve(
    conn: duckdb.DuckDBPyConnection,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    origin: Origin | None = None,
    destination: Destination | None = None,
    carrier: str | None = None,
) -> list[LeadTimePoint]:
    """PRD F5: price against days before departure, per seasonal band, band then horizon.

    Observations whose departure is before the day they were seen are dropped: they can
    only be a backfill mistake, and they would all pile into the shortest bucket."""
    clauses, params = _scope("f.", origin, destination, carrier)
    clauses.append("f.departure_date >= f.observed_on")
    bucket = _horizon_bucket_sql("f.departure_date - f.observed_on", horizons)
    rows = conn.execute(
        f"SELECT coalesce(t.tag, '{UNTAGGED_BAND}') AS band, {bucket} AS horizon_days, "
        "min(f.price_eur), sum(f.price_eur), count(*) "
        "FROM fare_observation f LEFT JOIN calendar_tag t ON t.date = f.departure_date"
        f"{_where(clauses)} "
        "GROUP BY band, horizon_days ORDER BY band, horizon_days",
        params,
    ).fetchall()
    return [
        LeadTimePoint(
            band=str(row[0]),
            horizon_days=int(row[1]),
            min_price_eur=_money(Decimal(row[2])),
            avg_price_eur=_money(Decimal(row[3]) / int(row[4])),
            observations=int(row[4]),
        )
        for row in rows
    ]


def wedding_premium(
    conn: duckdb.DuckDBPyConnection,
    *,
    tag: str = WEDDING_TAG,
    baseline_months: Sequence[int] = BASELINE_MONTHS,
    origin: Origin | None = None,
    destination: Destination | None = None,
    carrier: str | None = None,
    carriers: Sequence[str] | None = None,
) -> WeddingPremium | None:
    """PRD F5: what departures in the wedding window cost over the Feb/March baseline.

    `None` when either side is empty — a premium against nothing is not zero, it is
    unknown, and the caller has to say so rather than print `1.00`.

    The baseline is *months*, as the PRD words it, not "dates with no calendar tag". That
    is deliberate but has a drift the Hijri calendar guarantees: Ramadan (cheap) moves
    about eleven days earlier each year and sits inside February/March for the rest of the
    decade, so the baseline is depressed and the premium reads a little high. Fixing it
    means choosing a different baseline in the PRD, not special-casing it here."""
    clauses, params = _scope("f.", origin, destination, carrier, carriers)
    wedding_row = conn.execute(
        "SELECT sum(f.price_eur), count(*) FROM fare_observation f "
        "JOIN calendar_tag t ON t.date = f.departure_date AND t.tag = ?"
        f"{_where(clauses)}",
        [tag, *params],
    ).fetchone()
    months = tuple(int(m) for m in baseline_months)
    baseline_clauses = [*clauses, f"month(f.departure_date) IN ({', '.join('?' for _ in months)})"]
    baseline_row = conn.execute(
        f"SELECT sum(f.price_eur), count(*) FROM fare_observation f{_where(baseline_clauses)}",
        [*params, *months],
    ).fetchone()
    if wedding_row is None or baseline_row is None:
        return None
    wedding_n, baseline_n = int(wedding_row[1]), int(baseline_row[1])
    if wedding_n == 0 or baseline_n == 0:
        return None
    wedding_total, baseline_total = Decimal(wedding_row[0]), Decimal(baseline_row[0])
    # Ratio of the sums, not of the rounded averages: one rounding, at the end.
    multiplier = (wedding_total * baseline_n) / (baseline_total * wedding_n)
    return WeddingPremium(
        tag=tag,
        baseline_months=months,
        wedding_avg_eur=_money(wedding_total / wedding_n),
        baseline_avg_eur=_money(baseline_total / baseline_n),
        multiplier=_money(multiplier),
        premium_pct=_money((multiplier - 1) * 100),
        wedding_observations=wedding_n,
        baseline_observations=baseline_n,
    )


def carrier_efficiency(
    conn: duckdb.DuckDBPyConnection,
    *,
    origin: Origin | None = None,
    destination: Destination | None = None,
    carriers: Sequence[str] | None = None,
) -> list[CarrierEfficiency]:
    """PRD F5: euros per hour of total transit per carrier, cheapest hour first.

    The index is total euros over total hours, so a carrier is not rewarded for one short
    cheap hop among long ones; `avg_price_eur` and `avg_duration_hours` are reported
    beside it because the index alone cannot tell "cheap and slow" from "dear and fast"."""
    clauses, params = _scope("", origin, destination, None, carriers)
    rows = conn.execute(
        "SELECT carrier, sum(price_eur), sum(duration_minutes), count(*) "
        f"FROM fare_observation{_where(clauses)} GROUP BY carrier",
        params,
    ).fetchall()
    scored: list[tuple[Decimal, str, CarrierEfficiency]] = []
    for row in rows:
        total_minutes = int(row[2])
        if total_minutes <= 0:
            # The model forbids it; the table has no CHECK. Skip rather than divide by zero.
            continue
        total_price, count = Decimal(row[1]), int(row[3])
        eur_per_hour = total_price * 60 / total_minutes
        scored.append(
            (
                eur_per_hour,
                str(row[0]),
                CarrierEfficiency(
                    carrier=str(row[0]),
                    avg_price_eur=_money(total_price / count),
                    avg_duration_hours=_money(Decimal(total_minutes) / 60 / count),
                    eur_per_hour=_money(eur_per_hour),
                    observations=count,
                ),
            )
        )
    # Sorted on the unrounded index so two carriers a cent apart keep their order.
    return [efficiency for _, _, efficiency in sorted(scored, key=lambda item: (item[0], item[1]))]
