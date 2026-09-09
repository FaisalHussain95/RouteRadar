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

# The site is half the definition of done, and CI runs these same five scripts by name
# (.github/workflows/deploy-site.yml), so the names must not drift. `pnpm gen` first: the
# TypeScript types are generated from the schema the Python side just checked, and a lint
# or typecheck against yesterday's types would pass on a contract that has moved.
if ! command -v pnpm > /dev/null; then
  echo "pnpm is not on PATH: nvm lives in the shell profile, so a non-login shell needs" >&2
  echo "  . ~/.nvm/nvm.sh   before running this." >&2
  exit 1
fi
cd web
echo "== web install";   pnpm install --frozen-lockfile --silent
echo "== web gen";       pnpm gen
echo "== web lint";      pnpm lint
echo "== web typecheck"; pnpm typecheck
echo "== web test";      pnpm test
echo "== web build";     pnpm build
cd ..

echo "ALL CHECKS PASSED"
