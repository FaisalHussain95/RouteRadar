"""`specs/dashboard-data.schema.json`: the contract as JSON Schema, generated from
`site.DashboardData` and committed.

The schema is the seam between the two apps. The pipeline validates against the model on
the way out; the site generates its TypeScript types from this file on the way in
(`json-schema-to-typescript`, S15), so a field that moves without the schema moving breaks
the site's build rather than its runtime. That only works if the committed file is
regenerated whenever the model changes, which `tests/test_export.py` asserts.

It is generated in **serialization** mode: `generated_at` is a string in the file, not a
`datetime`, and the aliases (`from`/`to` on a band) are the JSON names, not the Python ones.
"""

import json
from pathlib import Path
from typing import Any

from flight_detective.export.site import SCHEMA_VERSION, DashboardData

DEFAULT_SCHEMA_PATH = Path("specs/dashboard-data.schema.json")


def dashboard_json_schema() -> dict[str, Any]:
    """The JSON Schema for `dashboard.json`, with the metadata a standalone file needs."""
    schema = DashboardData.model_json_schema(by_alias=True, mode="serialization")
    # Spread first, so the keys below win over the model's own `title`.
    return {
        **schema,
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/flight-detective/dashboard-data.schema.json",
        "title": f"Flight Detective dashboard data (schema_version {SCHEMA_VERSION})",
    }


def render_schema() -> str:
    """The file's exact bytes, as text."""
    return json.dumps(dashboard_json_schema(), indent=2) + "\n"


def write_schema(path: Path = DEFAULT_SCHEMA_PATH) -> Path:
    """Write the schema and return the path. Not atomic, unlike the export: this one is a
    committed spec a human regenerates, not something a timer writes under a live reader."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_schema())
    return path
