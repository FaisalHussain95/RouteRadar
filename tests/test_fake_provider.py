"""S04: the FareProvider protocol and the fixture-backed fake.

The fake is what every pipeline test (S07 onwards) runs against, so besides the story's
own criteria these tests pin the fixture *format* and check that the shipped fixtures
really contain the shapes later stories rely on: a PIA direct, a Gulf 1-stop, and
out-of-scope itineraries that `apply_scope` drops.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest

from flight_detective.models import Destination, Itinerary, Route
from flight_detective.providers.base import FareProvider, ProviderError
from flight_detective.providers.fake import (
    DEFAULT_FIXTURES_DIR,
    FakeFareProvider,
    FixtureInvalid,
    FixtureMissing,
    fixture_path,
)
from flight_detective.providers.filters import apply_scope

CDG_ISB = Route(origin="CDG", destination="ISB")
DEPARTURE = date(2026, 12, 20)
GULF_CARRIERS = {"GF", "QR", "EK", "FZ", "SV"}


def write_fixture(directory: Path, route: Route, departure: date, body: object) -> Path:
    path = fixture_path(directory, route, departure)
    path.write_text(json.dumps(body) if not isinstance(body, str) else body)
    return path


def minimal_body(route: Route = CDG_ISB, departure: date = DEPARTURE) -> dict[str, object]:
    return {
        "route": {"origin": route.origin, "destination": route.destination},
        "departure_date": departure.isoformat(),
        "itineraries": [
            {
                "carrier": "PK",
                "flight_numbers": ["PK750"],
                "stops": 0,
                "layover_minutes": 0,
                "duration_minutes": 470,
                "cabin": "economy",
                "price_eur": 540,
                "raw_ref": "pia-direct",
            }
        ],
    }


# --- Protocol -----------------------------------------------------------------------


def test_fake_satisfies_the_provider_protocol() -> None:
    provider: FareProvider = FakeFareProvider()
    assert isinstance(provider, FareProvider)
    assert provider.name == "fake"


def test_fixture_errors_are_provider_errors_not_os_errors() -> None:
    assert issubclass(FixtureMissing, ProviderError)
    assert issubclass(FixtureInvalid, ProviderError)
    assert not issubclass(FixtureMissing, OSError)


# --- search() on the shipped fixtures ----------------------------------------------------


def test_search_returns_validated_itineraries_for_the_requested_query() -> None:
    results = FakeFareProvider().search(CDG_ISB, DEPARTURE)

    assert results
    assert all(isinstance(i, Itinerary) for i in results)
    assert all(i.route == CDG_ISB and i.departure_date == DEPARTURE for i in results)
    assert all(i.provider == "fake" for i in results)
    assert all(isinstance(i.price_eur, Decimal) for i in results)


def test_search_stamps_a_unique_raw_ref_per_itinerary() -> None:
    results = FakeFareProvider().search(CDG_ISB, DEPARTURE)
    refs = [i.raw_ref for i in results]
    assert len(set(refs)) == len(refs)
    assert all(ref.startswith("fixture:") for ref in refs)


def test_search_is_deterministic() -> None:
    provider = FakeFareProvider()
    assert provider.search(CDG_ISB, DEPARTURE) == provider.search(CDG_ISB, DEPARTURE)


def test_a_fixture_exists_for_every_destination_from_cdg() -> None:
    for destination in get_args(Destination):
        route = Route(origin="CDG", destination=destination)
        assert fixture_path(DEFAULT_FIXTURES_DIR, route, DEPARTURE).is_file(), destination
        assert FakeFareProvider().search(route, DEPARTURE)


def test_fixtures_cover_pia_direct_gulf_one_stop_and_two_stop() -> None:
    everything: list[Itinerary] = []
    for destination in get_args(Destination):
        everything += FakeFareProvider().search(
            Route(origin="CDG", destination=destination), DEPARTURE
        )
    kept, _ = apply_scope(everything)

    assert any(i.carrier == "PK" and i.stops == 0 for i in kept), "no PIA direct"
    assert any(i.carrier in GULF_CARRIERS and i.stops == 1 for i in kept), "no Gulf 1-stop"
    assert any(i.stops >= 2 for i in everything), "no out-of-scope 2-stop"


def test_every_shipped_fixture_mixes_in_and_out_of_scope_itineraries() -> None:
    for destination in get_args(Destination):
        results = FakeFareProvider().search(Route(origin="CDG", destination=destination), DEPARTURE)
        kept, dropped = apply_scope(results)
        assert kept, destination
        assert dropped > 0, destination
        assert len({i.carrier for i in kept}) >= 2, destination


def test_an_empty_fixture_means_no_flights_not_an_error() -> None:
    assert FakeFareProvider().search(Route(origin="ORY", destination="ISB"), DEPARTURE) == []


# --- Errors -------------------------------------------------------------------------------


def test_missing_fixture_raises_fixture_missing_naming_the_path(tmp_path: Path) -> None:
    provider = FakeFareProvider(fixtures_dir=tmp_path)
    expected = fixture_path(tmp_path, CDG_ISB, date(2030, 1, 1))

    with pytest.raises(FixtureMissing) as excinfo:
        provider.search(CDG_ISB, date(2030, 1, 1))

    assert not isinstance(excinfo.value, FileNotFoundError)
    assert str(expected) in str(excinfo.value)
    assert "CDG" in str(excinfo.value) and "2030-01-01" in str(excinfo.value)


def test_fixture_path_follows_the_documented_naming() -> None:
    assert fixture_path(Path("/x"), CDG_ISB, DEPARTURE) == Path("/x/CDG-ISB-2026-12-20.json")


def test_custom_fixtures_dir_is_used(tmp_path: Path) -> None:
    write_fixture(tmp_path, CDG_ISB, DEPARTURE, minimal_body())
    results = FakeFareProvider(fixtures_dir=tmp_path).search(CDG_ISB, DEPARTURE)
    assert [i.carrier for i in results] == ["PK"]
    assert results[0].price_eur == Decimal("540")


def test_fixture_whose_content_disagrees_with_its_name_is_invalid(tmp_path: Path) -> None:
    other = Route(origin="CDG", destination="LHE")
    write_fixture(tmp_path, CDG_ISB, DEPARTURE, minimal_body(route=other))
    with pytest.raises(FixtureInvalid, match="LHE"):
        FakeFareProvider(fixtures_dir=tmp_path).search(CDG_ISB, DEPARTURE)

    write_fixture(tmp_path, CDG_ISB, DEPARTURE, minimal_body(departure=date(2026, 12, 21)))
    with pytest.raises(FixtureInvalid, match="2026-12-21"):
        FakeFareProvider(fixtures_dir=tmp_path).search(CDG_ISB, DEPARTURE)


def test_malformed_json_is_invalid_not_a_decode_error(tmp_path: Path) -> None:
    path = write_fixture(tmp_path, CDG_ISB, DEPARTURE, "{not json")
    with pytest.raises(FixtureInvalid) as excinfo:
        FakeFareProvider(fixtures_dir=tmp_path).search(CDG_ISB, DEPARTURE)
    assert str(path) in str(excinfo.value)


def test_float_price_in_a_fixture_is_invalid(tmp_path: Path) -> None:
    body = minimal_body()
    itineraries = body["itineraries"]
    assert isinstance(itineraries, list)
    itineraries[0]["price_eur"] = 540.5
    write_fixture(tmp_path, CDG_ISB, DEPARTURE, body)
    with pytest.raises(FixtureInvalid, match="price_eur"):
        FakeFareProvider(fixtures_dir=tmp_path).search(CDG_ISB, DEPARTURE)


def test_unknown_field_in_a_fixture_is_invalid(tmp_path: Path) -> None:
    body = minimal_body()
    body["itinerarys"] = body.pop("itineraries")
    write_fixture(tmp_path, CDG_ISB, DEPARTURE, body)
    with pytest.raises(FixtureInvalid, match="itinerarys"):
        FakeFareProvider(fixtures_dir=tmp_path).search(CDG_ISB, DEPARTURE)


def test_shipped_fixtures_all_load_and_have_provider_free_records() -> None:
    """Fixtures carry no `provider`/`route`/`departure_date` per record; the fake stamps them."""
    for path in sorted(DEFAULT_FIXTURES_DIR.glob("*.json")):
        body = json.loads(path.read_text())
        for record in body["itineraries"]:
            assert "provider" not in record, path.name
            assert "route" not in record, path.name
            assert "departure_date" not in record, path.name
