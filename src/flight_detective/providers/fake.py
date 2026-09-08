"""Fixture-backed FareProvider for tests and offline development.

One JSON file per query at `<fixtures_dir>/<origin>-<dest>-<YYYY-MM-DD>.json`:

    {
      "route": {"origin": "CDG", "destination": "ISB"},
      "departure_date": "2026-12-20",
      "note": "free text, optional",
      "itineraries": [
        {"carrier": "PK", "flight_numbers": ["PK750"], "stops": 0, "layover_minutes": 0,
         "duration_minutes": 470, "cabin": "economy", "price_eur": 540, "raw_ref": "pia-direct"}
      ]
    }

Records carry only what varies per itinerary. The route, departure date and `provider`
are stamped on by the fake, and the file's own `route`/`departure_date` must agree with
its name: a fixture copied to a new name without editing its body would otherwise answer
a query with another route's fares and every downstream test would pass on wrong data.
`price_eur` must be an int or a string, never a JSON float, for the same reason `Money`
rejects floats everywhere else.

Every failure is a `ProviderError` subclass rather than the underlying `FileNotFoundError`
or `ValidationError`, so the ingest pipeline (S07) can record it in `ingest_run.error`
without special-casing the fake, and so the message names the query and the path that
was looked for.

The default fixtures directory is the repo's `tests/fixtures/fares`, located relative to
this file. That is a deliberate reach out of `src/` into `tests/`: the fake exists to run
the *real* pipeline offline (`fd ingest --provider fake`), and having one set of fixtures
for both the tests and that command is worth more than package purity.
"""

import json
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from flight_detective.models import Base, Cabin, Itinerary, Money, Route
from flight_detective.providers.base import ProviderError

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "fares"


class FixtureMissing(ProviderError):
    """No fixture file exists for the requested (route, departure_date)."""


class FixtureInvalid(ProviderError):
    """The fixture file exists but is not valid JSON, not the documented shape, or
    disagrees with its own file name."""


class FixtureItinerary(Base):
    carrier: str
    flight_numbers: list[str]
    stops: int
    layover_minutes: int
    duration_minutes: int
    cabin: Cabin
    price_eur: Money
    raw_ref: str


class FareFixture(Base):
    route: Route
    departure_date: date
    itineraries: list[FixtureItinerary]
    note: str | None = None


def fixture_path(fixtures_dir: Path, route: Route, departure_date: date) -> Path:
    return fixtures_dir / f"{route.origin}-{route.destination}-{departure_date.isoformat()}.json"


class FakeFareProvider:
    name = "fake"

    def __init__(self, fixtures_dir: Path = DEFAULT_FIXTURES_DIR) -> None:
        self.fixtures_dir = fixtures_dir

    def search(self, route: Route, departure_date: date) -> list[Itinerary]:
        path = fixture_path(self.fixtures_dir, route, departure_date)
        fixture = self._load(path, route, departure_date)
        return [
            Itinerary(
                route=route,
                departure_date=departure_date,
                provider=self.name,
                raw_ref=f"fixture:{path.name}#{index}:{record.raw_ref}",
                **record.model_dump(exclude={"raw_ref"}),
            )
            for index, record in enumerate(fixture.itineraries)
        ]

    def _load(self, path: Path, route: Route, departure_date: date) -> FareFixture:
        query = f"{route.origin}->{route.destination} departing {departure_date.isoformat()}"
        if not path.is_file():
            raise FixtureMissing(f"no fare fixture for {query}: expected {path}")
        try:
            fixture = FareFixture.model_validate(json.loads(path.read_text()))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise FixtureInvalid(f"fare fixture {path} is not valid: {exc}") from exc
        if fixture.route != route or fixture.departure_date != departure_date:
            raise FixtureInvalid(
                f"fare fixture {path} is named for {query} but its body says "
                f"{fixture.route.origin}->{fixture.route.destination} departing "
                f"{fixture.departure_date.isoformat()}"
            )
        return fixture
