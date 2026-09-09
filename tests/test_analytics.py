"""S11: the five F5 questions, against the seeded fixture database.

Every expected number here was worked out by hand from `tests/fixtures/analytics/fares.json`
and is written as a literal on purpose: recomputing it from the fixture inside the test
would only prove the test agrees with itself. The fixture's shape is described in
`conftest.seed_analytics_db`; the comments below say which rows feed each figure.
"""

from datetime import date
from decimal import Decimal

import duckdb
import pytest

from flight_detective import db
from flight_detective.analytics.queries import (
    DEFAULT_BREAK_EVEN_EUR,
    airport_arbitrage,
    carrier_efficiency,
    lead_time_curve,
    lowest_fare_curve,
    wedding_premium,
)
from flight_detective.models import FareObservation


@pytest.fixture
def empty_conn() -> duckdb.DuckDBPyConnection:
    """A schema-only database: every question has to answer "nothing" without crashing."""
    conn = duckdb.connect(":memory:")
    db.init_schema(conn)
    return conn


def observation(*, observed_on: date, departure_date: date, price: int) -> FareObservation:
    """One CDG-ISB row, for the cases that need a lead time the shared fixture has not got."""
    return FareObservation(
        observed_on=observed_on,
        departure_date=departure_date,
        origin="CDG",
        destination="ISB",
        carrier="QR",
        flight_numbers=["QR40", "QR614"],
        stops=1,
        layover_minutes=120,
        duration_minutes=840,
        price_eur=price,
        provider="fixture",
        raw_ref="fixture:analytics#adhoc",
    )


class TestLowestFareCurve:
    def test_one_point_per_carrier_and_departure_month(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        points = lowest_fare_curve(analytics_conn)
        assert [(p.carrier, p.departure_month.isoformat(), p.price_eur) for p in points] == [
            ("EK", "2027-01-01", Decimal("1020")),
            ("GF", "2026-09-01", Decimal("520")),  # LHE 520 beats SKT 610 on 09-23
            ("GF", "2026-10-01", Decimal("480")),
            ("GF", "2026-12-01", Decimal("800")),
            ("GF", "2027-03-01", Decimal("480")),
            ("PK", "2026-09-01", Decimal("600")),
            ("PK", "2026-12-01", Decimal("900")),
            ("PK", "2027-03-01", Decimal("560")),
            ("QR", "2026-09-01", Decimal("700")),
            ("QR", "2026-10-01", Decimal("660")),
            ("QR", "2026-12-01", Decimal("980")),  # 12-08 at 980 beats 12-20 at 1150
            ("QR", "2027-01-01", Decimal("1100")),
            ("QR", "2027-02-01", Decimal("620")),
            ("QR", "2027-03-01", Decimal("640")),
            ("TK", "2026-12-01", Decimal("870")),
        ]

    def test_the_lowest_price_is_the_lowest_across_observation_days(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # QR into ISB on 2026-09-23 was seen at 720 on 09-02 and at 700 on 09-09.
        september = next(
            p
            for p in lowest_fare_curve(analytics_conn, carrier="QR", destination="ISB")
            if p.departure_month == date(2026, 9, 1)
        )
        assert september.price_eur == Decimal("700")
        assert september.observations == 2

    def test_filters_narrow_the_curve(self, analytics_conn: duckdb.DuckDBPyConnection) -> None:
        # ORY has exactly one row in the fixture: the TK departure on 2026-12-08.
        assert [
            (p.carrier, p.price_eur) for p in lowest_fare_curve(analytics_conn, origin="ORY")
        ] == [("TK", Decimal("870"))]
        assert lowest_fare_curve(analytics_conn, destination="SKT", carrier="PK") == []

    def test_an_empty_database_gives_an_empty_curve(
        self, empty_conn: duckdb.DuckDBPyConnection
    ) -> None:
        assert lowest_fare_curve(empty_conn) == []


class TestAirportArbitrage:
    def test_spread_and_verdict_either_side_of_the_break_even(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        rows = airport_arbitrage(analytics_conn)
        assert [
            (r.departure_date.isoformat(), r.lhe_eur, r.skt_eur, r.spread_eur, r.verdict)
            for r in rows
        ] == [
            # Lahore cheaper by 90, more than the 34 transfer: fly into Lahore.
            ("2026-09-23", Decimal("520"), Decimal("610"), Decimal("-90"), "lhe"),
            # 20 apart, inside the transfer cost: the choice is not about money.
            ("2026-10-09", Decimal("500"), Decimal("480"), Decimal("20"), "either"),
            # Sialkot cheaper by 140: worth the motorway.
            ("2026-12-08", Decimal("940"), Decimal("800"), Decimal("140"), "skt"),
        ]
        assert all(r.break_even_eur == DEFAULT_BREAK_EVEN_EUR for r in rows)

    def test_break_even_is_a_parameter(self, analytics_conn: duckdb.DuckDBPyConnection) -> None:
        # A 150 EUR transfer swallows every spread in the fixture.
        rows = airport_arbitrage(analytics_conn, break_even_eur=Decimal("150"))
        assert [r.verdict for r in rows] == ["either", "either", "either"]
        # A free transfer makes the cheaper airport win outright every time.
        assert [
            r.verdict for r in airport_arbitrage(analytics_conn, break_even_eur=Decimal(0))
        ] == [
            "lhe",
            "skt",
            "skt",
        ]

    def test_a_spread_exactly_equal_to_the_break_even_does_not_win(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # 2026-10-09 is 500 against 480. At a transfer of exactly 20 the saving is
        # entirely eaten by the drive, so the verdict stays `either`; a cent less and
        # Sialkot wins. The boundary is inclusive on the "no advantage" side.
        assert (
            airport_arbitrage(analytics_conn, break_even_eur=Decimal("20"))[1].verdict == "either"
        )
        assert (
            airport_arbitrage(analytics_conn, break_even_eur=Decimal("19.99"))[1].verdict == "skt"
        )
        # The same on the Lahore side: 2026-09-23 is 520 against 610.
        assert (
            airport_arbitrage(analytics_conn, break_even_eur=Decimal("90"))[0].verdict == "either"
        )
        assert (
            airport_arbitrage(analytics_conn, break_even_eur=Decimal("89.99"))[0].verdict == "lhe"
        )

    def test_islamabad_rides_along_for_context(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # 2026-12-08 has ISB at 900 (PK), 980 (QR) and 870 (TK out of ORY).
        assert airport_arbitrage(analytics_conn)[2].isb_eur == Decimal("870")

    def test_a_date_missing_one_airport_is_not_a_row(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # 2027-03-01 has Lahore at 480 and no Sialkot fare at all: a spread needs two prices.
        assert date(2027, 3, 1) not in {r.departure_date for r in airport_arbitrage(analytics_conn)}

    def test_observed_on_pins_a_single_snapshot(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        assert airport_arbitrage(analytics_conn, observed_on=date(2026, 9, 2)) == []
        assert len(airport_arbitrage(analytics_conn, observed_on=date(2026, 9, 9))) == 3

    def test_an_empty_database_gives_no_rows(self, empty_conn: duckdb.DuckDBPyConnection) -> None:
        assert airport_arbitrage(empty_conn) == []


class TestLeadTimeCurve:
    def test_bucketed_by_horizon_and_seasonal_band(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        points = lead_time_curve(analytics_conn)
        assert [
            (p.band, p.horizon_days, p.min_price_eur, p.avg_price_eur, p.observations)
            for p in points
        ] == [
            # 2026-12-20 is in the Christmas window as well as the wedding rush.
            ("christmas_new_year", 90, Decimal("1150"), Decimal("1150.00"), 1),
            ("off_peak", 14, Decimal("520"), Decimal("630.00"), 5),
            ("off_peak", 30, Decimal("480"), Decimal("585.00"), 4),
            ("off_peak", 180, Decimal("480"), Decimal("575.00"), 4),
            ("wedding_rush", 90, Decimal("800"), Decimal("940.00"), 6),
            ("wedding_rush", 120, Decimal("1020"), Decimal("1060.00"), 2),
        ]

    def test_a_departure_in_two_windows_counts_in_both_bands(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        by_band = {(p.band, p.horizon_days): p for p in lead_time_curve(analytics_conn)}
        # The single 2026-12-20 row is the whole Christmas band and the top of the
        # wedding band's 90-day bucket at the same time.
        assert by_band[("christmas_new_year", 90)].observations == 1
        assert by_band[("wedding_rush", 90)].observations == 6

    def test_an_off_grid_lead_time_lands_in_the_nearest_bucket(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # Off-grid rows: 21 days out -> 14, 32 -> 30, 102 -> 90, 152 -> 180. Any of them
        # making a bucket of its own would show up here as an extra horizon.
        assert {p.horizon_days for p in lead_time_curve(analytics_conn)} == {14, 30, 90, 120, 180}

    def test_a_lead_time_exactly_between_two_horizons_goes_to_the_shorter(
        self, empty_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # 22 days out is the exact midpoint of the 14- and 30-day horizons. The tie has to
        # break somewhere; `days * 2 <= lo + hi` sends it down.
        db.upsert_fare_observations(
            empty_conn,
            [
                observation(
                    observed_on=date(2026, 9, 9), departure_date=date(2026, 10, 1), price=500
                )
            ],
        )
        assert [p.horizon_days for p in lead_time_curve(empty_conn, horizons=(14, 30))] == [14]

    def test_a_departure_before_the_day_it_was_seen_is_dropped(
        self, empty_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # Only a backfill mistake can produce one, and it would land in the shortest bucket
        # and drag its average down.
        db.upsert_fare_observations(
            empty_conn,
            [observation(observed_on=date(2026, 9, 9), departure_date=date(2026, 9, 1), price=500)],
        )
        assert lead_time_curve(empty_conn) == []

    def test_a_single_horizon_puts_everything_in_one_bucket(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        assert {p.horizon_days for p in lead_time_curve(analytics_conn, horizons=(60,))} == {60}

    def test_no_horizons_is_a_programming_error(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(ValueError, match="at least one horizon"):
            lead_time_curve(analytics_conn, horizons=())

    def test_an_empty_database_gives_no_points(self, empty_conn: duckdb.DuckDBPyConnection) -> None:
        assert lead_time_curve(empty_conn) == []


class TestWeddingPremium:
    def test_wedding_window_against_the_february_march_baseline(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        premium = wedding_premium(analytics_conn)
        assert premium is not None
        # Wedding: 8 rows summing 7760 -> 970. Baseline: 4 rows summing 2300 -> 575.
        assert premium.wedding_avg_eur == Decimal("970.00")
        assert premium.baseline_avg_eur == Decimal("575.00")
        assert premium.wedding_observations == 8
        assert premium.baseline_observations == 4
        assert premium.multiplier == Decimal("1.69")  # 970 / 575 = 1.68695...
        assert premium.premium_pct == Decimal("68.70")
        assert premium.tag == "wedding_rush"
        assert premium.baseline_months == (2, 3)

    def test_the_baseline_is_months_so_february_counts(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        march_only = wedding_premium(analytics_conn, baseline_months=(3,))
        assert march_only is not None
        # Dropping the 2027-02-01 row at 620 leaves 640, 560 and 480: a dearer baseline.
        assert march_only.baseline_observations == 3
        assert march_only.baseline_avg_eur == Decimal("560.00")

    def test_filters_apply_to_both_sides(self, analytics_conn: duckdb.DuckDBPyConnection) -> None:
        premium = wedding_premium(analytics_conn, carrier="QR", destination="ISB")
        assert premium is not None
        # QR into ISB: wedding 980, 1150, 1100 -> 1076.67; baseline 620, 640 -> 630.
        assert premium.wedding_observations == 3
        assert premium.wedding_avg_eur == Decimal("1076.67")
        assert premium.baseline_avg_eur == Decimal("630.00")

    def test_an_empty_side_is_unknown_not_zero(
        self, analytics_conn: duckdb.DuckDBPyConnection, empty_conn: duckdb.DuckDBPyConnection
    ) -> None:
        assert wedding_premium(empty_conn) is None
        # EK flies only inside the wedding window here, so its baseline is empty.
        assert wedding_premium(analytics_conn, carrier="EK") is None
        # And a window nothing is tagged with has no wedding side.
        assert wedding_premium(analytics_conn, tag="no_such_window") is None


class TestCarrierEfficiency:
    def test_euros_per_hour_of_transit_cheapest_first(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        rows = carrier_efficiency(analytics_conn)
        assert [
            (r.carrier, r.avg_price_eur, r.avg_duration_hours, r.eur_per_hour, r.observations)
            for r in rows
        ] == [
            # GF: 4330 EUR over 7 x 15 h. Slowest, and the value leader.
            ("GF", Decimal("618.57"), Decimal("15.00"), Decimal("41.24"), 7),
            ("QR", Decimal("807.78"), Decimal("14.00"), Decimal("57.70"), 9),
            ("TK", Decimal("870.00"), Decimal("14.83"), Decimal("58.65"), 1),
            # PK is the only direct: 9 h, and the dearest hour on the board bar EK.
            ("PK", Decimal("686.67"), Decimal("9.00"), Decimal("76.30"), 3),
            ("EK", Decimal("1020.00"), Decimal("13.00"), Decimal("78.46"), 1),
        ]

    def test_the_index_is_total_euros_over_total_hours(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # QR: 7270 EUR over 7560 minutes. Averaging the per-row indices instead would give
        # a different number as soon as the durations differ, which is why it is not done.
        qr = next(r for r in carrier_efficiency(analytics_conn) if r.carrier == "QR")
        assert qr.eur_per_hour == (Decimal(7270) * 60 / 7560).quantize(Decimal("0.01"))

    def test_filters_narrow_the_index(self, analytics_conn: duckdb.DuckDBPyConnection) -> None:
        assert [r.carrier for r in carrier_efficiency(analytics_conn, destination="SKT")] == ["GF"]
        assert [r.carrier for r in carrier_efficiency(analytics_conn, origin="ORY")] == ["TK"]

    def test_an_empty_database_gives_no_rows(self, empty_conn: duckdb.DuckDBPyConnection) -> None:
        assert carrier_efficiency(empty_conn) == []
