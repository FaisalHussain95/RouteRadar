"""Settings from the environment.

`.env` is read as a convenience for a human at the keyboard, but real environment
variables always win, so the systemd unit and the tests can override it without editing a
file. There is no `python-dotenv` dependency: the parser below handles the three forms a
`.env` on this box will ever contain (`KEY=value`, `export KEY=value`, quoted values) and
nothing more.

Every knob is `FD_`-prefixed except `SERPAPI_KEY` and `GDELT_API_KEY`, which keep the
names their own docs use so they can be copied verbatim.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path("data/flight_detective.duckdb")
DEFAULT_SITE_EXPORT_PATH = Path("data/site/dashboard.json")
DEFAULT_DOTENV = Path(".env")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    serpapi_key: str | None
    gdelt_api_key: str | None
    site_export_path: Path


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse a `.env` file into a dict. Missing file means no values, not an error."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from `env` (default `os.environ`) layered over the `.env` file.

    `FD_DOTENV` points at the file to read; tests set it to a non-existent path so a
    developer's real `.env` never leaks into a test run.
    """
    env = os.environ if env is None else env
    merged = read_dotenv(Path(env.get("FD_DOTENV", DEFAULT_DOTENV)))
    merged.update(env)
    return Settings(
        db_path=Path(merged.get("FD_DB_PATH", DEFAULT_DB_PATH)),
        serpapi_key=merged.get("SERPAPI_KEY") or None,
        gdelt_api_key=merged.get("GDELT_API_KEY") or None,
        site_export_path=Path(merged.get("FD_SITE_EXPORT_PATH", DEFAULT_SITE_EXPORT_PATH)),
    )
