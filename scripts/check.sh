#!/usr/bin/env bash
# Definition of done, mechanically. Every dev session must leave this green.
# Used by hand, by the Stop hook (see scripts/stop-gate.sh) and by the QA reviewer.
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --quiet
echo "== ruff format"; uv run ruff format --check src tests
echo "== ruff lint";   uv run ruff check src tests
echo "== mypy";        uv run mypy
echo "== pytest";      uv run pytest
echo "ALL CHECKS PASSED"
