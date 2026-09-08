"""Fixed Gregorian windows: the wedding rush, Christmas/New Year, and French Zone C holidays.

Each window is data, not code: a `Window` row in `WINDOWS` with its tag, its multiplier
range and a `span(year)` rule that yields the inclusive first and last day of the edition
that *starts* in `year`. Windows that straddle New Year (wedding rush, Christmas) are
therefore anchored on the year of their first day, and `contains` checks both the
edition starting this year and the one that started last year, which is what makes
Jan 3 land in both the wedding rush and the Christmas window.

Multiplier ranges are hand-set inputs (PRD § Non-goals, architecture § Rules). Sources:

- `specs/brainstorming.md` § 2 gives the wedding rush a sustained 1.4–1.8x lift and
  describes Christmas/New Year only as the "peak winter spike" on top of it and the French
  summer as a "heavy European exit tax", without numbers.
- `specs/ux/design-system.md` § Data the page needs records the design's illustrative
  point values: wedding 1.34, Toussaint 1.11, French summer 1.26. Toussaint is in the
  design but not in the brainstorm table; the design-system note asks for it here.

So: the wedding range is the brainstorm's; Christmas sits above it, topping out at the
2x swing the PRD quotes for the whole year; the two French ranges bracket the design's
point values. The design's 1.34 for weddings is below the brainstorm's floor, and the
brainstorm wins because it is the cited table. Revise all of these from observed data
once the analytics exist, never from a chat.

French holiday dates are the Ministry's national calendar. Toussaint is identical in all
three zones and follows a stable rule rather than a per-year lookup: two full weeks,
classes resuming on the first Monday strictly after 1 November. Summer is fixed to
Jul 1 – Aug 31 per the brainstorm table even though the actual break starts on the first
Saturday of July, because the fare effect is the whole month. Both spans include the day
classes resume, matching the design's bands (`2026-10-17 → 2026-11-02`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from flight_detective.models import CalendarTag

BandKind = Literal["wedding", "religious", "french"]

_MONDAY = 0
_TOUSSAINT_LENGTH = timedelta(days=16)  # Saturday to the Monday two weeks later


def fixed_span(
    start_md: tuple[int, int], end_md: tuple[int, int]
) -> Callable[[int], tuple[date, date]]:
    """A span rule for a window with fixed month/day bounds.

    If the end falls before the start within a year, the window wraps into the next year
    (Nov 15 – Jan 15 starting in `year` ends in `year + 1`)."""

    def span(year: int) -> tuple[date, date]:
        start = date(year, *start_md)
        end = date(year, *end_md)
        if end < start:
            end = date(year + 1, *end_md)
        return start, end

    return span


def toussaint_span(year: int) -> tuple[date, date]:
    """Toussaint holiday for the autumn of `year`: the Saturday to the Monday two weeks
    later, where that Monday is the first one strictly after 1 November.

    "Strictly after" matters: when 1 November is itself a Monday (2021, 2027) the return
    to class is the 8th, not the 1st. Verified against the published 2021–2027 calendars
    in the tests."""
    all_saints = date(year, 11, 1)
    days_to_monday = (_MONDAY - all_saints.weekday()) % 7 or 7
    end = all_saints + timedelta(days=days_to_monday)
    return end - _TOUSSAINT_LENGTH, end


@dataclass(frozen=True)
class Window:
    tag: str
    """Stable identifier stored in `calendar_tag.tag`."""
    label: str
    """Human label for the dashboard band."""
    kind: BandKind
    """Band colour family in the design: `--band-wedding`, `--band-religious`, `--band-french`."""
    multiplier_low: Decimal
    multiplier_high: Decimal
    span: Callable[[int], tuple[date, date]]
    """Inclusive (first, last) day of the edition that starts in the given year."""

    def contains(self, day: date) -> bool:
        # An edition that started last year can still be running in January.
        for year in (day.year - 1, day.year):
            start, end = self.span(year)
            if start <= day <= end:
                return True
        return False

    def tag_for(self, day: date) -> CalendarTag:
        return CalendarTag(
            date=day,
            tag=self.tag,
            multiplier_low=self.multiplier_low,
            multiplier_high=self.multiplier_high,
        )


WINDOWS: tuple[Window, ...] = (
    Window(
        tag="wedding_rush",
        label="Desi wedding season",
        kind="wedding",
        multiplier_low=Decimal("1.40"),
        multiplier_high=Decimal("1.80"),
        span=fixed_span((11, 15), (1, 15)),
    ),
    Window(
        tag="christmas_new_year",
        label="Christmas / New Year",
        kind="wedding",
        multiplier_low=Decimal("1.60"),
        multiplier_high=Decimal("2.00"),
        span=fixed_span((12, 18), (1, 5)),
    ),
    Window(
        tag="french_summer",
        label="French summer",
        kind="french",
        multiplier_low=Decimal("1.15"),
        multiplier_high=Decimal("1.35"),
        span=fixed_span((7, 1), (8, 31)),
    ),
    Window(
        tag="french_toussaint",
        label="French Toussaint",
        kind="french",
        multiplier_low=Decimal("1.05"),
        multiplier_high=Decimal("1.15"),
        span=toussaint_span,
    ),
)

# Tag uniqueness is enforced by the tests, not an assert here: asserts vanish under -O.
_BY_TAG: dict[str, Window] = {w.tag: w for w in WINDOWS}


def window_by_tag(tag: str) -> Window:
    """Look a window up by its stored tag; `KeyError` for an unknown one."""
    return _BY_TAG[tag]


def gregorian_tags(day: date) -> list[CalendarTag]:
    """Every Gregorian window covering `day`, sorted by tag so output is deterministic."""
    return sorted((w.tag_for(day) for w in WINDOWS if w.contains(day)), key=lambda t: t.tag)
