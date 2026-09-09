"""`tags_for(date)`: the one calendar entry point the pipeline and the explainer use
(PRD F3). It unions the Gregorian and Hijri calendars and owns the dedupe across them.

Tags are unique within each calendar, and the two calendars use disjoint names, so the
dedupe is a guard rather than a routine path: if a tag ever appears in both (a window
moved from one file to the other and left a copy behind) the first source wins and the
row count stays what the `calendar_tag` primary key can hold, instead of the pipeline
failing on a duplicate `(date, tag)`.
"""

from collections.abc import Iterator
from datetime import date, timedelta

from flight_detective.calendar_engine.gregorian import gregorian_tags
from flight_detective.calendar_engine.hijri import hijri_tags
from flight_detective.models import CalendarTag


def tags_for(day: date) -> list[CalendarTag]:
    """Every window covering `day`, one entry per tag, sorted by tag."""
    by_tag: dict[str, CalendarTag] = {}
    for tag in (*gregorian_tags(day), *hijri_tags(day)):
        by_tag.setdefault(tag.tag, tag)
    return sorted(by_tag.values(), key=lambda t: t.tag)


def tags_for_range(start: date, end: date) -> Iterator[CalendarTag]:
    """Tags for every day from `start` to `end` inclusive, in date then tag order.
    An inverted range yields nothing rather than raising; the CLI validates it first."""
    day = start
    while day <= end:
        yield from tags_for(day)
        day += timedelta(days=1)
