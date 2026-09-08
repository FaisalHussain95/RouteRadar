from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from flight_detective.models import (
    CalendarTag,
    FareObservation,
    IngestRun,
    Itinerary,
    NewsEvent,
    Route,
)


def make_itinerary(**overrides: object) -> Itinerary:
    base: dict[str, object] = {
        "route": Route(origin="CDG", destination="ISB"),
        "departure_date": date(2026, 12, 20),
        "carrier": "PK",
        "flight_numbers": ["PK750"],
        "stops": 0,
        "layover_minutes": 0,
        "duration_minutes": 480,
        "cabin": "economy",
        "price_eur": Decimal("612"),
        "provider": "fake",
        "raw_ref": "fixture:1",
    }
    base.update(overrides)
    return Itinerary.model_validate(base)


def test_price_is_decimal_end_to_end() -> None:
    itinerary = make_itinerary(price_eur=Decimal("612.50"))
    observation = FareObservation.from_itinerary(itinerary, observed_on=date(2026, 9, 9))
    assert isinstance(itinerary.price_eur, Decimal)
    assert isinstance(observation.price_eur, Decimal)
    assert observation.price_eur == Decimal("612.50")
    # Round-trip through the serialised form keeps it a Decimal, not a float.
    restored = FareObservation.model_validate(observation.model_dump())
    assert isinstance(restored.price_eur, Decimal)
    assert restored.price_eur == Decimal("612.50")


def test_float_price_fails_validation() -> None:
    with pytest.raises(ValidationError, match="float"):
        make_itinerary(price_eur=612.5)


@pytest.mark.parametrize("raw", [612, "612", "612.50", Decimal("612")])
def test_int_string_and_decimal_prices_are_accepted(raw: object) -> None:
    assert make_itinerary(price_eur=raw).price_eur == Decimal(str(raw))


def test_negative_price_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_itinerary(price_eur=Decimal("-1"))


def test_route_only_accepts_scope_airports() -> None:
    with pytest.raises(ValidationError):
        Route(origin="LHR", destination="ISB")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Route(origin="CDG", destination="KHI")  # type: ignore[arg-type]


def test_itinerary_needs_at_least_one_flight_number() -> None:
    with pytest.raises(ValidationError):
        make_itinerary(flight_numbers=[])


def test_from_itinerary_flattens_route_and_keeps_provider() -> None:
    itinerary = make_itinerary(
        route=Route(origin="ORY", destination="SKT"),
        carrier="EK",
        flight_numbers=["EK76", "FZ333"],
        stops=1,
        layover_minutes=150,
    )
    observation = FareObservation.from_itinerary(itinerary, observed_on=date(2026, 9, 9))
    assert observation.observed_on == date(2026, 9, 9)
    assert (observation.origin, observation.destination) == ("ORY", "SKT")
    assert observation.flight_numbers == ["EK76", "FZ333"]
    assert observation.provider == "fake"
    assert observation.raw_ref == "fixture:1"


def test_observation_key_is_the_primary_key_columns() -> None:
    observation = FareObservation.from_itinerary(make_itinerary(), observed_on=date(2026, 9, 9))
    assert observation.key == (
        date(2026, 9, 9),
        date(2026, 12, 20),
        "CDG",
        "ISB",
        "PK",
        "PK750",
    )


def test_calendar_tag_multipliers_are_ordered_decimals() -> None:
    tag = CalendarTag(
        date=date(2026, 12, 20),
        tag="wedding_rush",
        multiplier_low=Decimal("1.4"),
        multiplier_high=Decimal("1.8"),
    )
    assert isinstance(tag.multiplier_low, Decimal)
    with pytest.raises(ValidationError):
        CalendarTag(
            date=date(2026, 12, 20),
            tag="wedding_rush",
            multiplier_low=Decimal("1.8"),
            multiplier_high=Decimal("1.4"),
        )


def test_news_event_severity_is_one_to_three() -> None:
    kwargs: dict[str, object] = {
        "event_date": date(2026, 6, 1),
        "category": "airspace",
        "headline": "Gulf airspace closed",
        "source_url": "https://example.test/a",
        "dedupe_key": "2026-06-01:gulf-airspace-closed",
    }
    assert NewsEvent.model_validate({**kwargs, "severity": 3}).impact_note is None
    for bad in (0, 4):
        with pytest.raises(ValidationError):
            NewsEvent.model_validate({**kwargs, "severity": bad})


def test_ingest_run_requires_aware_timestamp() -> None:
    IngestRun(
        run_at=datetime(2026, 9, 9, 6, 30, tzinfo=UTC),
        provider="fake",
        queries=36,
        rows_kept=10,
        rows_dropped=2,
    )
    with pytest.raises(ValidationError):
        IngestRun(
            run_at=datetime(2026, 9, 9, 6, 30),
            provider="fake",
            queries=36,
            rows_kept=10,
            rows_dropped=2,
        )
