"""PRD F6: why a fare row is what it is — the calendar windows on its departure date and
the news that landed near it, as a structured `Explanation` and one line of prose.

## Both halves are anchored on the departure date

A fare row carries two dates and they say different things: `observed_on` is the day the
market was asked, `departure_date` is what was asked about. A wedding window and an
airspace closure are properties of the journey, not of the day someone looked, so both
are looked up around the departure date. That is also the axis the dashboard draws its
bands and event pins on (`specs/ux/design-system.md` § Page anatomy), so a tooltip built
from this object lines up with the pins beside it instead of drifting by the horizon.

## Tags come from the database, not from `calendar_engine`

`tags_for(departure_date)` would answer without a query, and would answer for dates
`fd tag-dates` has not reached. That is exactly why it is not used here: the chart shades
its bands from `calendar_tag`, so an explanation computed from the engine could name a
window the chart does not draw. Reading the same table keeps the two in step, and no tags
on a date that should have some is a true report that the pipeline has not tagged it yet.

## The window is a parameter because the PRD and the design disagree

`DEFAULT_EVENT_WINDOW_DAYS` is the PRD's F6 figure (±7 days). The design's hover card
lists events within ±5. Both are display choices, so the caller passes one rather than
this module picking the winner.

Nothing here writes, and nothing here re-runs the calendar or the severity heuristic: an
explanation is a read of what the pipeline already decided.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import duckdb

from flight_detective import db
from flight_detective.calendar_engine.tags import label_for
from flight_detective.models import CalendarTag, FareObservation, NewsEvent

# PRD F6. `specs/ux/design-system.md` § Page anatomy asks for ±5 on the hover card.
DEFAULT_EVENT_WINDOW_DAYS = 7

# The design's three pin glyphs (`!` high, `▲` med, `•` low) in words, for the sentence.
SEVERITY_WORDS: dict[int, str] = {1: "low", 2: "medium", 3: "high"}

# A tooltip line has to stay one line. Beyond this the sentence counts the rest instead of
# naming them; the structured `events` list keeps all of them for the drawer to render.
MAX_EVENTS_IN_SENTENCE = 3


@dataclass(frozen=True)
class Explanation:
    """One fare row with everything that plausibly explains it.

    `tags` and `events` are always lists: nothing applying is an answer ("this fare is
    off-peak and uneventful"), not a missing value, and a caller rendering a tooltip
    should never have to tell `None` from `[]`."""

    observation: FareObservation
    tags: list[CalendarTag]
    """Windows on the departure date, sorted by tag, straight out of `calendar_tag`."""
    events: list[NewsEvent]
    """News within the window, most severe first and then nearest to the departure."""
    window_days: int
    """How far either side of the departure date `events` was gathered from."""

    def offset_days(self, event: NewsEvent) -> int:
        """Days from the departure to `event`: negative before it, positive after."""
        return (event.event_date - self.observation.departure_date).days

    @property
    def sentence(self) -> str:
        """The whole explanation as one line, for a chart tooltip or a log."""
        return _sentence(self)


def explain(
    conn: duckdb.DuckDBPyConnection,
    observation: FareObservation,
    *,
    window_days: int = DEFAULT_EVENT_WINDOW_DAYS,
) -> Explanation:
    """Everything the database knows about why `observation` costs what it costs.

    A window of 0 is legal and means same-day news only; a negative one is a caller bug
    that would silently return no events, so it raises instead."""
    if window_days < 0:
        raise ValueError("window_days cannot be negative")
    departure = observation.departure_date
    tags = db.fetch_calendar_tags(conn, departure, departure)
    span = timedelta(days=window_days)
    events = db.fetch_news_events(conn, departure - span, departure + span)
    return Explanation(
        observation=observation,
        tags=tags,
        # Most severe first, then nearest: the order a reader wants the reasons in, and
        # the order the sentence truncates from. `dedupe_key` breaks the last tie so two
        # equally severe events on one day do not swap places between runs.
        events=sorted(
            events,
            key=lambda e: (
                -e.severity,
                abs((e.event_date - departure).days),
                e.event_date,
                e.dedupe_key,
            ),
        ),
        window_days=window_days,
    )


def _euros(amount: Decimal) -> str:
    """`€900` for whole euros, `€900.50` otherwise — every provider seen quotes whole
    euros (architecture § Rules), so the cents would be noise in the common case."""
    if amount == amount.to_integral_value():
        return f"€{amount:.0f}"
    return f"€{amount:.2f}"


def _join(parts: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c` — the list reads as prose rather than as data."""
    if len(parts) <= 2:
        return " and ".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _multiplier(tag: CalendarTag) -> str:
    if tag.multiplier_low == tag.multiplier_high:
        return f"×{tag.multiplier_low:.2f}"
    return f"×{tag.multiplier_low:.2f}–{tag.multiplier_high:.2f}"


def _when(offset: int) -> str:
    """Where an event sits relative to the departure, in words."""
    if offset == 0:
        return "on the day"
    days = abs(offset)
    return f"{days} day{'' if days == 1 else 's'} {'after' if offset > 0 else 'before'}"


def _window(days: int) -> str:
    return f"±{days} day{'' if days == 1 else 's'}"


def _event_phrase(explanation: Explanation, event: NewsEvent) -> str:
    severity = SEVERITY_WORDS.get(event.severity, f"severity {event.severity}")
    return f'"{event.headline}" ({severity}, {_when(explanation.offset_days(event))})'


def _tags_clause(explanation: Explanation) -> str:
    if not explanation.tags:
        return ""
    windows = [f"{label_for(tag.tag)} ({_multiplier(tag)})" for tag in explanation.tags]
    return f"departure falls in {_join(windows)}"


def _events_clause(explanation: Explanation) -> str:
    events = explanation.events
    if not events:
        return ""
    named = [_event_phrase(explanation, event) for event in events[:MAX_EVENTS_IN_SENTENCE]]
    if len(events) > MAX_EVENTS_IN_SENTENCE:
        # Counted, not named, and joined as the last item so it reads "A, B, C and 2 more".
        named.append(f"{len(events) - MAX_EVENTS_IN_SENTENCE} more")
    plural = "" if len(events) == 1 else "s"
    return (
        f"{len(events)} news event{plural} landed within "
        f"{_window(explanation.window_days)} of departure: {_join(named)}"
    )


def _sentence(explanation: Explanation) -> str:
    """The four shapes an explanation comes in: both halves, either half, or neither.

    Written as clauses joined by a stem rather than as four templates, so a change to how
    a window or an event reads happens in one place."""
    observation = explanation.observation
    head = (
        f"{observation.carrier} {observation.origin}→{observation.destination} "
        f"on {observation.departure_date} at {_euros(observation.price_eur)} "
        f"(seen {observation.observed_on})"
    )
    tags, events = _tags_clause(explanation), _events_clause(explanation)
    if tags and events:
        return f"{head}: {tags}, and {events}."
    if tags:
        return f"{head}: {tags}."
    if events:
        return f"{head}: no calendar window applies, but {events}."
    return (
        f"{head}: no calendar window applies and no news landed within "
        f"{_window(explanation.window_days)} of departure."
    )
