#!/usr/bin/env bash
# PostToolUse hook: ruff-format any .py file Claude just edited, so formatting never
# shows up as a Stop-gate failure. Reads the hook's JSON from stdin, exits 0 always.
set -uo pipefail
cd "$(dirname "$0")/.."
f="$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)"
case "$f" in
  *.py) [ -f "$f" ] && uv run --quiet ruff format --quiet "$f" >/dev/null 2>&1 ;;
esac
exit 0
