#!/usr/bin/env bash
# One daily run of the pipeline: what flight-detective-pipeline.service executes.
#
# It is a script rather than a one-liner in ExecStart= because ExecStart has no shell —
# a `&&` chain there needs `/bin/sh -c '…'` anyway — and because the chain has one
# exception in it (partial ingests, below) that is worth explaining and testing.
#
# Steps after the ingest are chained with `&&`: each one's output is the next one's
# input, and a failure must stop the run before deploy/push-data.sh publishes a
# half-built day.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

UV="${FD_UV:-uv}"                                  # absolute under systemd, plain by hand
PROVIDER="${FD_PROVIDER:-serpapi}"
HORIZONS="${FD_HORIZONS:-14,30,60,90,120,180}"     # PRD F1; see README on what it costs

# `fd ingest` exit codes: 0 every query answered, 3 some answered and some failed,
# 1 every query failed, 2 misconfiguration (no SERPAPI_KEY). A day missing a few
# (route, horizon) cells is still worth tagging and exporting — the PRD budgets < 2 %
# missing cells — so 3, and only 3, continues.
INGEST_PARTIAL=3

log() { printf '%s run-pipeline: %s\n' "$(date -Is)" "$*"; }

# Code moves on main between runs (merges, docs) while this checkout only ever gains its
# own data commits, so without a fast-forward first the day's push would be a
# non-fast-forward that push-data.sh refuses. Sync the env too, in case a dependency
# changed. Skipped where there is no origin (the test harness runs a copy outside git),
# and fatal otherwise: a run that cannot publish is not worth its SerpApi searches.
if git remote get-url origin >/dev/null 2>&1; then
  if ! git pull --ff-only --quiet origin main || ! "$UV" sync --quiet; then
    log "checkout could not fast-forward to origin/main or sync; aborting before the ingest"
    exit 1
  fi
fi
log "ingest --provider $PROVIDER --horizons $HORIZONS"
"$UV" run fd ingest --provider "$PROVIDER" --horizons "$HORIZONS"
rc=$?
if [ "$rc" -eq "$INGEST_PARTIAL" ]; then
  log "ingest was partial (exit $rc): some cells are missing, continuing"
elif [ "$rc" -ne 0 ]; then
  log "ingest failed (exit $rc): nothing will be exported or pushed"
  exit "$rc"
fi

"$UV" run fd tag-dates \
  && "$UV" run fd news-ingest \
  && "$UV" run fd export-site \
  && "$REPO/deploy/push-data.sh"
rc=$?
[ "$rc" -eq 0 ] && log "done" || log "failed (exit $rc)"
exit "$rc"
