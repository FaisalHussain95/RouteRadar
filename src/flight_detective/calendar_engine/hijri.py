"""Drifting Islamic windows: Ramadan phase 1, Chaand Raat / Eid-ul-Fitr, and the Hajj /
Eid-ul-Adha corridor, computed from `hijri-converter` rather than looked up per year
(PRD F3). The Hijri year is ~354 days, so each window lands ~11 days earlier every
Gregorian year; the converter's Umm al-Qura tables carry that drift.

Same shape as `gregorian.py`: each window is a row in `HIJRI_WINDOWS` whose `span(year)`
yields the inclusive Gregorian first and last day of the edition that *starts* in that
**Hijri** year, and `contains` checks the edition starting in the day's Hijri year and the
one before it. None of the three windows crosses Muharram today, but the two-year check
costs nothing and keeps a future window (a Muharram one, say) from needing a special case.

Two things about accuracy worth knowing before anyone "fixes" a date:

- Umm al-Qura is the *calculated* Saudi calendar. Pakistan declares Ramadan and both Eids
  by moon sighting (Ruet-e-Hilal) and runs a day behind Saudi about half the time (2025:
  both Eids). The PRD accepts ±1 day and the tests pin that against the observed Pakistani
  dates, so a one-day gap against a news report is expected, not a bug.
- `hijri-converter` is deprecated upstream in favour of `hijridate`, a rename with the
  same API and tables. It is still the package the PRD names, so the import warning is
  silenced here rather than re-deciding the stack; switching is a one-line change in this
  file and `pyproject.toml` when the time comes.

Multiplier ranges are hand-set inputs. `specs/brainstorming.md` § 2 gives only the
direction ("outbound demand drop", "highest sudden spike", "Gulf seat rationing");
`specs/ux/design-system.md` § Data the page needs gives the design's point values
(Ramadan 0.92, Eid-ul-Fitr 1.27, Hajj 1.21). The ranges below bracket those points, with
Eid-ul-Fitr's ceiling left high because the brainstorm calls it the sharpest spike of the
year. Revise from observed data once the analytics exist, never from a chat.
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from flight_detective.calendar_engine.gregorian import Window
from flight_detective.models import CalendarTag

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from hijri_converter.convert import Gregorian, Hijri

RAMADAN = 9
SHAWWAL = 10
DHUL_HIJJAH = 12


def to_gregorian(year: int, month: int, day: int) -> date:
    """Gregorian date of a Hijri (year, month, day). `ValueError` for a day the month
    does not have (Hijri months are 29 or 30 days, never 31)."""
    g = Hijri(year, month, day).to_gregorian()
    return date(g.year, g.month, g.day)


def hijri_year_of(day: date) -> int:
    return int(Gregorian(day.year, day.month, day.day).to_hijri().year)


def hijri_span(
    start_md: tuple[int, int], end_md: tuple[int, int]
) -> Callable[[int], tuple[date, date]]:
    """A span rule for a window bounded by fixed Hijri month/days within one Hijri year."""

    def span(year: int) -> tuple[date, date]:
        return to_gregorian(year, *start_md), to_gregorian(year, *end_md)

    return span


def around(
    anchor_md: tuple[int, int], before: int, after: int
) -> Callable[[int], tuple[date, date]]:
    """A span rule for a window of Gregorian days around a Hijri anchor date: from
    `before` days before it to `after` days after it, inclusive. Working in Gregorian
    days sidesteps Hijri month lengths (Eid −4 is Ramadan 26 or 27 depending on the year)."""

    def span(year: int) -> tuple[date, date]:
        anchor = to_gregorian(year, *anchor_md)
        return anchor - timedelta(days=before), anchor + timedelta(days=after)

    return span


def eid_ul_fitr(hijri_year: int) -> date:
    """1 Shawwal."""
    return to_gregorian(hijri_year, SHAWWAL, 1)


def eid_ul_adha(hijri_year: int) -> date:
    """10 Dhul Hijjah."""
    return to_gregorian(hijri_year, DHUL_HIJJAH, 10)


@dataclass(frozen=True)
class HijriWindow(Window):
    """A `Window` whose `span` is keyed by Hijri year instead of Gregorian year."""

    def contains(self, day: date) -> bool:
        hijri_year = hijri_year_of(day)
        for year in (hijri_year - 1, hijri_year):
            start, end = self.span(year)
            if start <= day <= end:
                return True
        return False


HIJRI_WINDOWS: tuple[HijriWindow, ...] = (
    HijriWindow(
        tag="ramadan_phase1",
        label="Ramadan (first half)",
        kind="religious",
        multiplier_low=Decimal("0.85"),
        multiplier_high=Decimal("0.95"),
        span=hijri_span((RAMADAN, 1), (RAMADAN, 15)),
    ),
    HijriWindow(
        tag="eid_ul_fitr",
        label="Chaand Raat / Eid-ul-Fitr",
        kind="religious",
        multiplier_low=Decimal("1.20"),
        multiplier_high=Decimal("1.60"),
        span=around((SHAWWAL, 1), before=4, after=2),
    ),
    HijriWindow(
        tag="hajj_eid_ul_adha",
        label="Hajj / Eid-ul-Adha",
        kind="religious",
        multiplier_low=Decimal("1.10"),
        multiplier_high=Decimal("1.30"),
        span=hijri_span((DHUL_HIJJAH, 1), (DHUL_HIJJAH, 13)),
    ),
)

_BY_TAG: dict[str, HijriWindow] = {w.tag: w for w in HIJRI_WINDOWS}


def hijri_window_by_tag(tag: str) -> HijriWindow:
    """Look a window up by its stored tag; `KeyError` for an unknown one."""
    return _BY_TAG[tag]


def hijri_tags(day: date) -> list[CalendarTag]:
    """Every Hijri window covering `day`, sorted by tag so output is deterministic."""
    return sorted((w.tag_for(day) for w in HIJRI_WINDOWS if w.contains(day)), key=lambda t: t.tag)
