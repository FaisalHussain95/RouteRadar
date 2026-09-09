from pathlib import Path

import pytest

from flight_detective.config import DEFAULT_DB_PATH, Settings, load_settings, read_dotenv


def test_default_db_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FD_DB_PATH")
    assert load_settings().db_path == DEFAULT_DB_PATH
    assert Path("data/flight_detective.duckdb") == DEFAULT_DB_PATH


def test_env_overrides_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FD_DB_PATH", str(tmp_path / "x.duckdb"))
    assert load_settings().db_path == tmp_path / "x.duckdb"


def test_dotenv_is_read_but_never_overrides_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# comment\n\nSERPAPI_KEY=from-dotenv\nFD_DB_PATH=/from/dotenv.duckdb\nexport Q='q v'\n"
    )
    monkeypatch.setenv("FD_DOTENV", str(dotenv))
    monkeypatch.setenv("FD_DB_PATH", str(tmp_path / "env.duckdb"))
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    settings = load_settings()
    assert settings.serpapi_key == "from-dotenv"
    assert settings.db_path == tmp_path / "env.duckdb"
    assert read_dotenv(dotenv)["Q"] == "q v"


def test_missing_key_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    assert load_settings().serpapi_key is None


def test_the_gdelt_key_is_read_from_the_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S17: `GDELT_API_KEY` keeps the name GDELT Cloud's own docs use, like `SERPAPI_KEY`,
    so it can be copied verbatim out of the dashboard."""
    dotenv = tmp_path / ".env"
    dotenv.write_text("GDELT_API_KEY=from-dotenv\n")
    monkeypatch.setenv("FD_DOTENV", str(dotenv))
    monkeypatch.delenv("GDELT_API_KEY", raising=False)
    assert load_settings().gdelt_api_key == "from-dotenv"


def test_a_missing_gdelt_key_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GDELT_API_KEY", raising=False)
    assert load_settings().gdelt_api_key is None


def test_an_empty_gdelt_key_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty `GDELT_API_KEY=` in `.env` is a key nobody filled in, not a key of "".
    Treating it as absent is what makes `fd news-ingest` say so in one line."""
    monkeypatch.setenv("GDELT_API_KEY", "")
    assert load_settings().gdelt_api_key is None


def test_settings_is_immutable() -> None:
    settings = Settings(
        db_path=Path("a"), serpapi_key=None, gdelt_api_key=None, site_export_path=Path("b")
    )
    with pytest.raises(AttributeError):
        settings.db_path = Path("c")  # type: ignore[misc]
