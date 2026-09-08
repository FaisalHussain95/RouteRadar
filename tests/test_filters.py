"""Scope rules from the PRD table. The boundary cases (exactly 7 h layover, exactly 15 h
duration) are the ones a `<` vs `<=` slip would silently drop, so they get their own tests."""

from decimal import Decimal

import pytest

from flight_detective.models import Cabin, Itinerary, Route
from flight_detective.providers.filters import (
    MAX_DURATION_MINUTES,
    MAX_LAYOVER_MINUTES,
    MAX_STOPS,
    apply_scope,
    is_in_scope,
)


def make_itinerary(**overrides: object) -> Itinerary:
    base: dict[str, object] = {
        "route": Route(origin="CDG", destination="ISB"),
        "departure_date": "2026-12-20",
        "carrier": "QR",
        "flight_numbers": ["QR40", "QR620"],
        "stops": 1,
        "layover_minutes": 120,
        "duration_minutes": 660,
        "cabin": "economy",
        "price_eur": Decimal("612"),
        "provider": "fake",
        "raw_ref": "fixture:1",
    }
    base.update(overrides)
    return Itinerary.model_validate(base)


def test_constants_match_the_prd_scope_table() -> None:
    assert MAX_STOPS == 1
    assert MAX_LAYOVER_MINUTES == 7 * 60
    assert MAX_DURATION_MINUTES == 15 * 60


def test_typical_one_stop_economy_is_kept() -> None:
    assert is_in_scope(make_itinerary())


def test_direct_flight_is_kept() -> None:
    assert is_in_scope(make_itinerary(stops=0, layover_minutes=0, duration_minutes=480))


@pytest.mark.parametrize("stops", [2, 3])
def test_two_or_more_stops_are_rejected(stops: int) -> None:
    assert not is_in_scope(make_itinerary(stops=stops))


def test_layover_over_seven_hours_is_rejected() -> None:
    assert not is_in_scope(make_itinerary(layover_minutes=7 * 60 + 1))


def test_duration_over_fifteen_hours_is_rejected() -> None:
    assert not is_in_scope(make_itinerary(duration_minutes=15 * 60 + 1))


@pytest.mark.parametrize("cabin", ["premium_economy", "business", "first"])
def test_non_economy_is_rejected(cabin: Cabin) -> None:
    assert not is_in_scope(make_itinerary(cabin=cabin))


def test_exactly_seven_hour_layover_is_kept() -> None:
    assert is_in_scope(make_itinerary(layover_minutes=7 * 60))


def test_exactly_fifteen_hour_duration_is_kept() -> None:
    assert is_in_scope(make_itinerary(duration_minutes=15 * 60))


def test_boundary_layover_and_duration_together_are_kept() -> None:
    assert is_in_scope(make_itinerary(layover_minutes=7 * 60, duration_minutes=15 * 60))


def test_apply_scope_keeps_order_and_counts_drops() -> None:
    direct = make_itinerary(stops=0, layover_minutes=0, duration_minutes=480, raw_ref="direct")
    two_stop = make_itinerary(stops=2, raw_ref="two-stop")
    gulf = make_itinerary(raw_ref="gulf")
    business = make_itinerary(cabin="business", raw_ref="business")
    long_layover = make_itinerary(layover_minutes=8 * 60, raw_ref="long-layover")

    kept, dropped = apply_scope([direct, two_stop, gulf, business, long_layover])

    assert [i.raw_ref for i in kept] == ["direct", "gulf"]
    assert dropped == 3


def test_apply_scope_on_nothing() -> None:
    assert apply_scope([]) == ([], 0)


def test_apply_scope_accepts_any_iterable() -> None:
    kept, dropped = apply_scope(make_itinerary(stops=n) for n in range(4))
    assert [i.stops for i in kept] == [0, 1]
    assert dropped == 2
