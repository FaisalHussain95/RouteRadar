"""`tags_for` is the one entry point the pipeline uses; these tests cover the union
of the two calendars, the dedupe, and the ordering."""

from datetime import date, timedelta

import pytest

from flight_detective.calendar_engine import tags
from flight_detective.calendar_engine.tags import tags_for, tags_for_range
from flight_detective.models import CalendarTag


def test_gregorian_only_day() -> None:
    assert [t.tag for t in tags_for(date(2026, 12, 25))] == ["christmas_new_year", "wedding_rush"]


def test_hijri_only_day() -> None:
    assert [t.tag for t in tags_for(date(2026, 3, 20))] == ["eid_ul_fitr"]


def test_union_of_both_calendars() -> None:
    # Dhul Hijjah 6, 1443 fell on 2022-07-05, inside the French summer window.
    assert [t.tag for t in tags_for(date(2022, 7, 5))] == ["french_summer", "hajj_eid_ul_adha"]
    # Ramadan 1451 starts 2030-01-05, inside the wedding rush.
    assert [t.tag for t in tags_for(date(2030, 1, 10))] == ["ramadan_phase1", "wedding_rush"]


def test_quiet_day_is_empty() -> None:
    assert tags_for(date(2026, 9, 9)) == []


def test_every_tag_is_stamped_with_the_day() -> None:
    day = date(2026, 12, 25)
    assert all(t.date == day for t in tags_for(day))


def test_duplicates_across_sources_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2026, 3, 20)
    dup = CalendarTag(date=day, tag="eid_ul_fitr", multiplier_low="1.00", multiplier_high="1.00")
    monkeypatch.setattr(tags, "gregorian_tags", lambda _day: [dup])
    result = tags_for(day)
    assert [t.tag for t in result] == ["eid_ul_fitr"]
    # The first source wins on a clash, so the fake Gregorian tag is the survivor.
    assert result[0].multiplier_high == 1


def test_range_is_inclusive_and_in_date_order() -> None:
    start, end = date(2026, 3, 15), date(2026, 3, 23)
    rows = list(tags_for_range(start, end))
    assert {r.date for r in rows} == {start + timedelta(days=i) for i in range(1, 8)}
    assert [r.date for r in rows] == sorted(r.date for r in rows)
    assert all(r.tag == "eid_ul_fitr" for r in rows)


def test_empty_range_yields_nothing() -> None:
    assert list(tags_for_range(date(2026, 3, 20), date(2026, 3, 19))) == []
