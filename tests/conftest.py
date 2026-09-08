"""Shared fixtures. The autouse env fixture is what guarantees "tests never use data/":
every test process sees FD_DB_PATH pointing into pytest's tmp_path, so even a test that
forgets to pass a path cannot open the real database."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setenv("FD_DB_PATH", str(db_path))
    # Also keep a stray .env in the repo root from leaking into tests.
    monkeypatch.setenv("FD_DOTENV", str(tmp_path / "no-such.env"))
    return db_path
