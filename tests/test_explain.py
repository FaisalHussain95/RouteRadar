"""S12: the explanation layer, against the seeded fixture database.

The calendar tags here are the ones `conftest.seed_analytics_db` materialised from the
real engine, so the expected labels and multipliers are literals: if a window moves in
`calendar_engine` this file fails rather than quietly agreeing with it. News events are
inserted per test, because the shared fixture has none and each case wants a different
shape (nothing, one, many, on the window edge).
"""

from datetime import date, timedelta
from decimal import Decimal

import duckdb
import pytest

from flight_detective import db
from flight_detective.analytics.explain import (
    DEFAULT_EVENT_WINDOW_DAYS,
    MAX_EVENTS_IN_SENTENCE,
    explain,
)
from flight_detective.calendar_engine.tags import tags_for
from flight_detective.models import CalendarTag, FareObservation, NewsEvent

# Departure dates picked off the seeded fixture for how many windows they carry:
UNTAGGED = date(2027, 3, 1)  # no window at all
ONE_TAG = date(2027, 1, 7)  # wedding rush only (Christmas ended on Jan 5)
TWO_TAGS = date(2026, 12, 20)  # wedding rush and Christmas / New Year
SEEN_ON = date(2026, 9, 9)


def observation(
    *,
    departure_date: date,
    observed_on: date = SEEN_ON,
    price: int = 900,
) -> FareObservation:
    """One CDG-ISB fare row on PIA; the fields the sentence prints are the ones that vary."""
    return FareObservation(
        observed_on=observed_on,
        departure_date=departure_date,
        origin="CDG",
        destination="ISB",
        carrier="PK",
        flight_numbers=["PK750"],
        stops=0,
        layover_minutes=0,
        duration_minutes=540,
        price_eur=price,
        provider="fixture",
        raw_ref="fixture:explain#1",
    )


def event(
    *,
    event_date: date,
    headline: str,
    severity: int = 3,
    category: str = "airspace_disruption",
) -> NewsEvent:
    return NewsEvent(
        event_date=event_date,
        category=category,
        severity=severity,
        headline=headline,
        source_url="https://example.test/story",
        dedupe_key=f"{event_date.isoformat()}:{headline}",
    )


def head(departure_date: date, *, price: int = 900, observed_on: date = SEEN_ON) -> str:
    """The part of every sentence that names the fare row."""
    return f"PK CDG→ISB on {departure_date} at €{price} (seen {observed_on})"


class TestStructure:
    def test_nothing_applies_gives_empty_lists_not_none(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        explanation = explain(analytics_conn, observation(departure_date=UNTAGGED))
        assert explanation.tags == []
        assert explanation.events == []
        assert explanation.window_days == DEFAULT_EVENT_WINDOW_DAYS

    def test_returns_the_windows_on_the_departure_date(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        explanation = explain(analytics_conn, observation(departure_date=TWO_TAGS))
        assert [(t.tag, t.multiplier_low, t.multiplier_high) for t in explanation.tags] == [
            ("christmas_new_year", Decimal("1.60"), Decimal("2.00")),
            ("wedding_rush", Decimal("1.40"), Decimal("1.80")),
        ]

    def test_tags_come_from_the_database_not_the_calendar_engine(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # The seeded range stops in March 2027, so July is a date the engine tags
        # (French summer) and `fd tag-dates` has not reached. The chart would draw no
        # band there, and the explanation must not claim one.
        july = date(2027, 7, 15)
        assert [t.tag for t in tags_for(july)] == ["french_summer"]
        assert explain(analytics_conn, observation(departure_date=july)).tags == []

    def test_returns_events_around_the_departure_date(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        db.upsert_news_events(
            analytics_conn,
            [
                event(event_date=TWO_TAGS, headline="Fog closes Lahore for the morning"),
                event(event_date=UNTAGGED, headline="Unrelated, months later"),
            ],
        )
        explanation = explain(analytics_conn, observation(departure_date=TWO_TAGS))
        assert [e.headline for e in explanation.events] == ["Fog closes Lahore for the morning"]

    def test_the_window_is_inclusive_and_anchored_on_departure(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        window = DEFAULT_EVENT_WINDOW_DAYS
        db.upsert_news_events(
            analytics_conn,
            [
                event(event_date=TWO_TAGS - timedelta(days=window), headline="edge before"),
                event(event_date=TWO_TAGS + timedelta(days=window), headline="edge after"),
                event(event_date=TWO_TAGS - timedelta(days=window + 1), headline="too early"),
                event(event_date=TWO_TAGS + timedelta(days=window + 1), headline="too late"),
                # Right on the observation day, which is months from the departure: the
                # anchor is the journey, not the day the market was asked.
                event(event_date=SEEN_ON, headline="on the observation day"),
            ],
        )
        explanation = explain(analytics_conn, observation(departure_date=TWO_TAGS))
        assert sorted(e.headline for e in explanation.events) == ["edge after", "edge before"]

    def test_events_are_most_severe_first_then_nearest(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        db.upsert_news_events(
            analytics_conn,
            [
                event(event_date=TWO_TAGS, headline="mild, on the day", severity=1),
                event(event_date=TWO_TAGS - timedelta(days=3), headline="severe, far", severity=3),
                event(event_date=TWO_TAGS + timedelta(days=1), headline="severe, near", severity=3),
                event(event_date=TWO_TAGS, headline="middling, on the day", severity=2),
            ],
        )
        explanation = explain(analytics_conn, observation(departure_date=TWO_TAGS))
        assert [e.headline for e in explanation.events] == [
            "severe, near",
            "severe, far",
            "middling, on the day",
            "mild, on the day",
        ]

    def test_offset_days_is_signed_from_the_departure(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        before = event(event_date=TWO_TAGS - timedelta(days=2), headline="before")
        after = event(event_date=TWO_TAGS + timedelta(days=4), headline="after")
        db.upsert_news_events(analytics_conn, [before, after])
        explanation = explain(analytics_conn, observation(departure_date=TWO_TAGS))
        assert explanation.offset_days(before) == -2
        assert explanation.offset_days(after) == 4

    def test_a_narrower_window_is_honoured(self, analytics_conn: duckdb.DuckDBPyConnection) -> None:
        db.upsert_news_events(
            analytics_conn,
            [
                event(event_date=TWO_TAGS, headline="on the day"),
                event(event_date=TWO_TAGS + timedelta(days=1), headline="next day"),
            ],
        )
        explanation = explain(analytics_conn, observation(departure_date=TWO_TAGS), window_days=0)
        assert [e.headline for e in explanation.events] == ["on the day"]

    def test_a_negative_window_is_a_caller_bug(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(ValueError, match="negative"):
            explain(analytics_conn, observation(departure_date=TWO_TAGS), window_days=-1)


class TestSentence:
    def test_no_reasons(self, analytics_conn: duckdb.DuckDBPyConnection) -> None:
        explanation = explain(analytics_conn, observation(departure_date=UNTAGGED))
        assert explanation.sentence == (
            f"{head(UNTAGGED)}: no calendar window applies and no news landed "
            "within ±7 days of departure."
        )

    def test_one_reason(self, analytics_conn: duckdb.DuckDBPyConnection) -> None:
        explanation = explain(analytics_conn, observation(departure_date=ONE_TAG))
        assert explanation.sentence == (
            f"{head(ONE_TAG)}: departure falls in Desi wedding season (×1.40–1.80)."
        )

    def test_one_reason_that_is_an_event(self, analytics_conn: duckdb.DuckDBPyConnection) -> None:
        db.upsert_news_events(
            analytics_conn,
            [
                event(
                    event_date=UNTAGGED + timedelta(days=3),
                    headline="Gulf hub cuts Hajj quotas",
                    severity=2,
                )
            ],
        )
        explanation = explain(analytics_conn, observation(departure_date=UNTAGGED))
        assert explanation.sentence == (
            f"{head(UNTAGGED)}: no calendar window applies, but 1 news event landed "
            'within ±7 days of departure: "Gulf hub cuts Hajj quotas" (medium, 3 days after).'
        )

    def test_many_reasons(self, analytics_conn: duckdb.DuckDBPyConnection) -> None:
        db.upsert_news_events(
            analytics_conn,
            [
                event(
                    event_date=TWO_TAGS - timedelta(days=2),
                    headline="PIA banned from EU airspace",
                    severity=3,
                ),
                event(
                    event_date=TWO_TAGS,
                    headline="Fog closes Lahore for the morning",
                    severity=1,
                ),
            ],
        )
        explanation = explain(analytics_conn, observation(departure_date=TWO_TAGS))
        assert explanation.sentence == (
            f"{head(TWO_TAGS)}: departure falls in Christmas / New Year (×1.60–2.00) and "
            "Desi wedding season (×1.40–1.80), and 2 news events landed within ±7 days of "
            'departure: "PIA banned from EU airspace" (high, 2 days before) and '
            '"Fog closes Lahore for the morning" (low, on the day).'
        )

    def test_a_long_list_of_events_is_counted_rather_than_named(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        db.upsert_news_events(
            analytics_conn,
            [
                event(event_date=UNTAGGED + timedelta(days=offset), headline=f"story {offset}")
                for offset in range(5)
            ],
        )
        explanation = explain(analytics_conn, observation(departure_date=UNTAGGED))
        assert len(explanation.events) == 5
        named = [f'"story {offset}"' for offset in range(MAX_EVENTS_IN_SENTENCE)]
        assert explanation.sentence == (
            f"{head(UNTAGGED)}: no calendar window applies, but 5 news events landed "
            f"within ±7 days of departure: {named[0]} (high, on the day), "
            f"{named[1]} (high, 1 day after), {named[2]} (high, 2 days after) and 2 more."
        )

    def test_an_unknown_tag_falls_back_to_its_stored_name(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        # A window retired from `calendar_engine` since the day `fd tag-dates` ran must
        # still render, as itself, rather than raising inside a tooltip.
        db.upsert_calendar_tags(
            analytics_conn,
            [
                CalendarTag(
                    date=UNTAGGED,
                    tag="mango_season",
                    multiplier_low=Decimal("1.05"),
                    multiplier_high=Decimal("1.05"),
                )
            ],
        )
        explanation = explain(analytics_conn, observation(departure_date=UNTAGGED))
        assert explanation.sentence == (
            f"{head(UNTAGGED)}: departure falls in mango_season (×1.05)."
        )

    def test_cents_are_printed_only_when_there_are_any(
        self, analytics_conn: duckdb.DuckDBPyConnection
    ) -> None:
        row = observation(departure_date=UNTAGGED).model_copy(
            update={"price_eur": Decimal("899.50")}
        )
        assert explain(analytics_conn, row).sentence.startswith(
            "PK CDG→ISB on 2027-03-01 at €899.50 (seen 2026-09-09):"
        )
