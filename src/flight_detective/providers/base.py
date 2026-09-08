"""The FareProvider contract (PRD F2).

The pipeline only ever sees this protocol, so swapping SerpApi for Apify, or running the
fixture-backed fake offline, is a constructor call in `cli.py` and nothing else. A provider
returns *everything* it found for one (route, departure date) query; scope filtering is the
pipeline's job (`providers/filters.py`), not the provider's, so that `ingest_run.rows_dropped`
counts what each provider really returned.

`ProviderError` is the one exception type the pipeline catches. Anything a provider can
foresee (quota exhausted, missing fixture, unparseable response) is raised as a subclass so
S07 can record it in `ingest_run.error`; anything else is a bug and should propagate.
"""

from datetime import date
from typing import Protocol, runtime_checkable

from flight_detective.models import Itinerary, Route


class ProviderError(Exception):
    """A foreseeable provider failure: quota, missing fixture, bad response."""


@runtime_checkable
class FareProvider(Protocol):
    name: str
    """Short identifier stored in `fare_observation.provider` and `ingest_run.provider`."""

    def search(self, route: Route, departure_date: date) -> list[Itinerary]:
        """All itineraries on offer for `route` departing `departure_date`, unfiltered."""
        ...
