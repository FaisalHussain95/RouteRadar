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

from flight_detective.calendar_engine.gregorian import WINDOWS, Window, gregorian_tags
from flight_detective.calendar_engine.hijri import HIJRI_WINDOWS, hijri_tags
from flight_detective.models import CalendarTag

# Both calendars keep their display metadata beside the stored tag; this is the one map
# across them, so a reader of `calendar_tag` (the explainer, the export) does not have to
# know which file a window came from.
_BY_TAG: dict[str, Window] = {w.tag: w for w in (*WINDOWS, *HIJRI_WINDOWS)}


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


def window_for(tag: str) -> Window | None:
    """The window a stored tag came from, across both calendars, or `None`.

    `None` rather than a `KeyError` for the same reason `label_for` falls back: the table
    holds whatever the engine wrote on the day it ran, and a window retired since then
    must not take a tooltip or an export down with it."""
    return _BY_TAG.get(tag)


def label_for(tag: str) -> str:
    """The dashboard label for a stored tag, or the tag itself if no window owns it.

    Falling back rather than raising is deliberate: `calendar_tag` holds whatever the
    engine wrote on the day it ran, so a window retired since then would otherwise turn
    a tooltip into a KeyError."""
    window = _BY_TAG.get(tag)
    return tag if window is None else window.label
