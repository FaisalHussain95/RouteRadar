"""Scope rules from `specs/prd.md` § Scope rules, applied to every itinerary before storage.

This is the one place the rules live (architecture § Rules): the ingest pipeline calls
`apply_scope` and stores the drop count in `ingest_run.rows_dropped`, so a provider that
starts returning only 2-stop junk shows up as a run with a large drop count rather than
as an empty table with no explanation.

Two things the PRD table lists are deliberately *not* checked here:

- Origins and destinations. `Route` is a `Literal` over the scope airports, so an
  out-of-scope airport fails validation at the provider boundary and never reaches this
  module.
- The "carriers of interest". That row of the table is what the analysis focuses on, not
  a hard constraint: a cheap, in-scope fare on a carrier not in the list is still real
  data worth recording, and dropping it would hide exactly the surprises the dashboard
  exists to show.

All limits are inclusive (`<=`), so an itinerary at exactly 7 h layover or exactly 15 h
duration is kept. The tests pin both boundaries.
"""

from collections.abc import Iterable

from flight_detective.models import Itinerary

MAX_STOPS = 1
MAX_LAYOVER_MINUTES = 7 * 60
MAX_DURATION_MINUTES = 15 * 60


def is_in_scope(itinerary: Itinerary) -> bool:
    """True if the itinerary satisfies every hard constraint of the PRD scope table."""
    return (
        itinerary.stops <= MAX_STOPS
        and itinerary.layover_minutes <= MAX_LAYOVER_MINUTES
        and itinerary.duration_minutes <= MAX_DURATION_MINUTES
        and itinerary.cabin == "economy"
    )


def apply_scope(itineraries: Iterable[Itinerary]) -> tuple[list[Itinerary], int]:
    """Split itineraries into (kept, in provider order) and a count of the ones dropped."""
    kept: list[Itinerary] = []
    dropped = 0
    for itinerary in itineraries:
        if is_in_scope(itinerary):
            kept.append(itinerary)
        else:
            dropped += 1
    return kept, dropped
