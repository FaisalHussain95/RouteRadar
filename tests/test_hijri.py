"""Hijri calendar windows. The Eid table pins the engine to the published dates the
PRD's success metric names (2025–2027, ±1 day); the edge table pins every window
boundary from both sides in the same style as `test_gregorian.py`."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from flight_detective.calendar_engine.hijri import (
    HIJRI_WINDOWS,
    eid_ul_adha,
    eid_ul_fitr,
    hijri_tags,
    hijri_window_by_tag,
    hijri_year_of,
)
from flight_detective.models import CalendarTag


def tags_on(day: str) -> set[str]:
    return {t.tag for t in hijri_tags(date.fromisoformat(day))}


# Published Eid dates as observed in Pakistan (moon sighting), which can trail the
# Umm al-Qura calculation by a day: 2025 is exactly that case on both Eids.
# (hijri year, Eid-ul-Fitr, Eid-ul-Adha)
EID_PUBLISHED: list[tuple[int, str, str]] = [
    (1446, "2025-03-31", "2025-06-07"),
    (1447, "2026-03-20", "2026-05-27"),
    (1448, "2027-03-09", "2027-05-16"),
]


@pytest.mark.parametrize(
    ("year", "fitr", "adha"), EID_PUBLISHED, ids=[str(y) for y, _, _ in EID_PUBLISHED]
)
def test_eid_dates_match_published_within_one_day(year: int, fitr: str, adha: str) -> None:
    one_day = timedelta(days=1)
    assert abs(eid_ul_fitr(year) - date.fromisoformat(fitr)) <= one_day
    assert abs(eid_ul_adha(year) - date.fromisoformat(adha)) <= one_day


@pytest.mark.parametrize(
    ("year", "fitr", "adha"), EID_PUBLISHED, ids=[str(y) for y, _, _ in EID_PUBLISHED]
)
def test_published_eids_fall_inside_their_windows(year: int, fitr: str, adha: str) -> None:
    assert "eid_ul_fitr" in tags_on(fitr)
    assert "hajj_eid_ul_adha" in tags_on(adha)


# (day, tags expected). Anchored on 1447 AH: Ramadan 1 = 2026-02-18 (30 days),
# Eid-ul-Fitr = 2026-03-20, Dhul Hijjah 1 = 2026-05-18.
EDGE_CASES: list[tuple[str, set[str]]] = [
    # ramadan phase 1: Ramadan 1–15
    ("2026-02-17", set()),
    ("2026-02-18", {"ramadan_phase1"}),
    ("2026-03-04", {"ramadan_phase1"}),
    ("2026-03-05", set()),
    # chaand raat / eid-ul-fitr: Eid −4 .. +2, i.e. the last days of Ramadan into Shawwal 3
    ("2026-03-15", set()),
    ("2026-03-16", {"eid_ul_fitr"}),
    ("2026-03-20", {"eid_ul_fitr"}),
    ("2026-03-22", {"eid_ul_fitr"}),
    ("2026-03-23", set()),
    # hajj / eid-ul-adha corridor: Dhul Hijjah 1–13
    ("2026-05-17", set()),
    ("2026-05-18", {"hajj_eid_ul_adha"}),
    ("2026-05-30", {"hajj_eid_ul_adha"}),
    ("2026-05-31", set()),
    # nothing in the off-peak baseline months
    ("2026-02-14", set()),
    ("2026-09-09", set()),
]


@pytest.mark.parametrize(("day", "expected"), EDGE_CASES, ids=[d for d, _ in EDGE_CASES])
def test_window_edges(day: str, expected: set[str]) -> None:
    assert tags_on(day) == expected


def test_windows_drift_with_the_hijri_year() -> None:
    # ~11 days earlier each Gregorian year: 1446's Eid-ul-Fitr window must not be found
    # by looking at 1447's dates, and vice versa.
    assert tags_on("2025-03-30") == {"eid_ul_fitr"}
    assert tags_on("2025-03-20") == set()


def test_hijri_year_of_crosses_muharram() -> None:
    # 1 Muharram 1448 is 2026-06-16 per Umm al-Qura.
    assert hijri_year_of(date(2026, 6, 15)) == 1447
    assert hijri_year_of(date(2026, 6, 16)) == 1448


def test_every_tag_carries_multipliers() -> None:
    for window in HIJRI_WINDOWS:
        tag = window.tag_for(date(2026, 1, 1))
        assert isinstance(tag, CalendarTag)
        assert isinstance(tag.multiplier_low, Decimal)
        assert isinstance(tag.multiplier_high, Decimal)
        assert tag.multiplier_low <= tag.multiplier_high
        assert window.kind == "religious"


def test_ramadan_is_a_dip_and_eids_are_spikes() -> None:
    assert hijri_window_by_tag("ramadan_phase1").multiplier_high < 1
    assert hijri_window_by_tag("eid_ul_fitr").multiplier_low > 1
    assert hijri_window_by_tag("hajj_eid_ul_adha").multiplier_low > 1


def test_tags_are_unique() -> None:
    tags = [w.tag for w in HIJRI_WINDOWS]
    assert len(tags) == len(set(tags))


def test_unknown_tag_lookup_raises() -> None:
    with pytest.raises(KeyError):
        hijri_window_by_tag("no_such_window")
