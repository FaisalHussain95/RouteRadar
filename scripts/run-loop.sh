#!/usr/bin/env bash
# Autonomous dev loop: one fresh `claude -p` session per backlog story until nothing is
# left `todo`. State lives in specs/backlog.md and git, never in the conversation, which
# is why a fresh session per story is safe and why a crashed session can be resumed.
#
# Runs on branch `dev` so `main` stays a human-reviewed line. Stops early if a story is
# left `doing` twice in a row (a blocked story) so it cannot spin on one failure.
#
# Usage: bash scripts/run-loop.sh [max_stories]   (default 20)
# Log:   .loop-state/run-loop.log
set -uo pipefail
cd "$(dirname "$0")/.."
max=${1:-20}
mkdir -p .loop-state
log=.loop-state/run-loop.log

if [ "$(git branch --show-current)" != "dev" ]; then
  git switch dev 2>/dev/null || git switch -c dev
fi

last_doing=""
for i in $(seq 1 "$max"); do
  if ! grep -q 'status: todo' specs/backlog.md && ! grep -q 'status: doing' specs/backlog.md; then
    echo "backlog empty" | tee -a "$log"; exit 0
  fi
  doing="$(grep -B2 'status: doing' specs/backlog.md | grep -o '^## S[0-9]*' | head -1)"
  if [ -n "$doing" ] && [ "$doing" = "$last_doing" ]; then
    echo "story $doing still 'doing' after a full session — blocked, stopping" | tee -a "$log"; exit 1
  fi
  last_doing="$doing"
  echo "=== $(date -Is) session $i start (doing=$doing)" | tee -a "$log"
  # Pushing is allowed for interactive sessions (project settings) but never for the
  # loop: a bad autonomous run must stay local until a human has looked at `dev`.
  claude -p "$(cat prompts/dev-story.md)" --permission-mode auto \
    --disallowedTools "Bash(git push:*)" 2>&1 | tee -a "$log"
  echo "=== $(date -Is) session $i end" | tee -a "$log"
  last_doing="$(grep -B2 'status: doing' specs/backlog.md | grep -o '^## S[0-9]*' | head -1)"
  [ -n "$last_doing" ] && [ "$last_doing" = "$doing" ] && { echo "story $doing left 'doing' — blocked, stopping" | tee -a "$log"; exit 1; }
done
echo "reached max_stories=$max" | tee -a "$log"
