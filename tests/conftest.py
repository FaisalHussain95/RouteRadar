"""Shared fixtures. The autouse env fixture is what guarantees "tests never use data/":
every test process sees FD_DB_PATH pointing into pytest's tmp_path, so even a test that
forgets to pass a path cannot open the real database."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

GDELT_FIXTURES = Path(__file__).parent / "fixtures" / "gdelt"


@pytest.fixture(autouse=True)
def isolated_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setenv("FD_DB_PATH", str(db_path))
    # Also keep a stray .env in the repo root from leaking into tests.
    monkeypatch.setenv("FD_DOTENV", str(tmp_path / "no-such.env"))
    return db_path


def load_gdelt_body(slug: str) -> dict[str, Any]:
    """One recorded GDELT response body, by taxonomy slug."""
    body = json.loads((GDELT_FIXTURES / f"{slug}.json").read_text())
    assert isinstance(body, dict)
    return body


@pytest.fixture
def gdelt_body() -> Callable[[str], dict[str, Any]]:
    """`gdelt_body("airspace-disruption")` -> that fixture's JSON. Shared by the client,
    dedupe and pipeline tests, which all answer from the same four files."""
    return load_gdelt_body
