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
#
# Start it detached as a transient user unit, not in tmux/screen from a login shell:
#
#   systemd-run --user --unit=flight-loop --collect \
#     -p WorkingDirectory=$PWD --setenv=PATH="$PATH" --setenv=HOME="$HOME" \
#     bash scripts/run-loop.sh 5
#   journalctl --user -u flight-loop -f        # or tail the log file
#   systemctl --user stop flight-loop          # stop between or during stories
#
# A tmux session inherits the login session's scope, and logind kills that scope when
# the session ends, tmux and the running story with it (this happened on 2026-09-09 at
# 13:48; the story was left `doing` and resumed cleanly). A user unit outlives logouts.
set -uo pipefail
cd "$(dirname "$0")/.."
max=${1:-20}
# When started from inside an interactive Claude session these mark the child as nested
# and it refuses to run; the loop's sessions are independent, so drop them.
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT
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
  # A fixed session id, published in .loop-state/active-session, lets scripts/stop-gate.sh
  # tell the loop's session (gated) apart from an interactive one sharing this tree (not
  # gated while the loop is mid-story, since the red tree is not its work).
  sid="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  echo "$sid" > .loop-state/active-session
  # Pushing is allowed for interactive sessions (project settings) but never for the
  # loop: a bad autonomous run must stay local until a human has looked at `dev`.
  # Opus for the loop: the stories are well-specified and mechanically gated, so the
  # cheaper model is enough; Fable is kept for the interactive planning sessions.
  claude -p "$(cat prompts/dev-story.md)" --model "${LOOP_MODEL:-opus}" --permission-mode auto \
    --session-id "$sid" --disallowedTools "Bash(git push:*)" 2>&1 | tee -a "$log"
  rm -f .loop-state/active-session
  echo "=== $(date -Is) session $i end" | tee -a "$log"
  last_doing="$(grep -B2 'status: doing' specs/backlog.md | grep -o '^## S[0-9]*' | head -1)"
  [ -n "$last_doing" ] && [ "$last_doing" = "$doing" ] && { echo "story $doing left 'doing' — blocked, stopping" | tee -a "$log"; exit 1; }
done
echo "reached max_stories=$max" | tee -a "$log"
