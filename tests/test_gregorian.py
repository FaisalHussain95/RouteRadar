"""Gregorian calendar windows. The edge tests are table-driven on purpose: an off-by-one
on a window boundary would still produce plausible-looking tags, so every boundary is
pinned from both sides (last day out, first day in, last day in, first day out)."""

from datetime import date
from decimal import Decimal

import pytest

from flight_detective.calendar_engine.gregorian import (
    WINDOWS,
    Window,
    gregorian_tags,
    toussaint_span,
    window_by_tag,
)
from flight_detective.models import CalendarTag


def tags_on(day: str) -> set[str]:
    return {t.tag for t in gregorian_tags(date.fromisoformat(day))}


# (day, tags expected on that day). Each window contributes its four boundary days.
EDGE_CASES: list[tuple[str, set[str]]] = [
    # wedding rush: Nov 15 – Jan 15
    ("2026-11-14", set()),
    ("2026-11-15", {"wedding_rush"}),
    ("2027-01-15", {"wedding_rush"}),
    ("2027-01-16", set()),
    # christmas / new year: Dec 18 – Jan 5, always inside the wedding rush
    ("2026-12-17", {"wedding_rush"}),
    ("2026-12-18", {"wedding_rush", "christmas_new_year"}),
    ("2027-01-05", {"wedding_rush", "christmas_new_year"}),
    ("2027-01-06", {"wedding_rush"}),
    # french summer, Zone C: Jul 1 – Aug 31
    ("2026-06-30", set()),
    ("2026-07-01", {"french_summer"}),
    ("2026-08-31", {"french_summer"}),
    ("2026-09-01", set()),
    # french toussaint 2026, from the design: Oct 17 – Nov 2
    ("2026-10-16", set()),
    ("2026-10-17", {"french_toussaint"}),
    ("2026-11-02", {"french_toussaint"}),
    ("2026-11-03", set()),
    # nothing at all in the off-peak baseline months
    ("2026-02-14", set()),
    ("2026-03-20", set()),
]


@pytest.mark.parametrize(("day", "expected"), EDGE_CASES, ids=[d for d, _ in EDGE_CASES])
def test_window_edges(day: str, expected: set[str]) -> None:
    assert tags_on(day) == expected


@pytest.mark.parametrize("day", ["2027-01-03", "2026-01-03", "2025-01-03"])
def test_year_spanning_windows_cover_early_january(day: str) -> None:
    assert tags_on(day) == {"wedding_rush", "christmas_new_year"}


@pytest.mark.parametrize("day", ["2026-12-31", "2027-01-01"])
def test_year_spanning_windows_cover_the_new_year_boundary(day: str) -> None:
    assert tags_on(day) == {"wedding_rush", "christmas_new_year"}


# Published French school calendars (all zones share Toussaint). The rule in the module
# must reproduce every one of these, including 2021 and 2027 where Nov 1 is itself a
# Monday and the return to class is the Monday *after* it.
TOUSSAINT_PUBLISHED: list[tuple[int, str, str]] = [
    (2021, "2021-10-23", "2021-11-08"),
    (2022, "2022-10-22", "2022-11-07"),
    (2023, "2023-10-21", "2023-11-06"),
    (2024, "2024-10-19", "2024-11-04"),
    (2025, "2025-10-18", "2025-11-03"),
    (2026, "2026-10-17", "2026-11-02"),
    (2027, "2027-10-23", "2027-11-08"),
]


@pytest.mark.parametrize(("year", "start", "end"), TOUSSAINT_PUBLISHED, ids=str)
def test_toussaint_rule_matches_published_calendars(year: int, start: str, end: str) -> None:
    assert toussaint_span(year) == (date.fromisoformat(start), date.fromisoformat(end))


def test_toussaint_is_always_a_saturday_to_monday_fortnight() -> None:
    for year in range(2020, 2040):
        start, end = toussaint_span(year)
        assert start.weekday() == 5, year  # Saturday
        assert end.weekday() == 0, year  # Monday
        assert (end - start).days == 16, year
        assert start < date(year, 11, 1) < end, year


def test_tags_are_calendar_tags_stamped_with_the_date() -> None:
    day = date(2026, 12, 25)
    tags = gregorian_tags(day)
    assert tags, "Christmas Day must carry tags"
    for tag in tags:
        assert isinstance(tag, CalendarTag)
        assert tag.date == day


def test_every_tag_carries_a_multiplier_range() -> None:
    for tag in gregorian_tags(date(2026, 12, 25)):
        assert isinstance(tag.multiplier_low, Decimal)
        assert isinstance(tag.multiplier_high, Decimal)
        assert Decimal("1") < tag.multiplier_low <= tag.multiplier_high


def test_tags_are_sorted_and_unique() -> None:
    tags = [t.tag for t in gregorian_tags(date(2026, 12, 25))]
    assert tags == sorted(tags)
    assert len(tags) == len(set(tags))


def test_window_table_is_well_formed() -> None:
    tags = [w.tag for w in WINDOWS]
    assert len(tags) == len(set(tags)), "window tags must be unique"
    assert set(tags) == {"wedding_rush", "christmas_new_year", "french_summer", "french_toussaint"}
    for window in WINDOWS:
        assert isinstance(window, Window)
        assert window.multiplier_low <= window.multiplier_high
        start, end = window.span(2026)
        assert start <= end
        assert (end - start).days < 366


def test_window_multipliers_flow_into_tags() -> None:
    window = window_by_tag("wedding_rush")
    (tag,) = [t for t in gregorian_tags(date(2026, 11, 20)) if t.tag == "wedding_rush"]
    assert (tag.multiplier_low, tag.multiplier_high) == (
        window.multiplier_low,
        window.multiplier_high,
    )


def test_unknown_window_tag_is_an_error() -> None:
    with pytest.raises(KeyError):
        window_by_tag("no_such_window")


def test_window_span_is_inclusive_and_contains_agrees_with_tags() -> None:
    for window in WINDOWS:
        start, end = window.span(2026)
        assert window.contains(start) and window.contains(end)
        assert not window.contains(start - date.resolution)
        assert not window.contains(end + date.resolution)
