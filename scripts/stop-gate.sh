#!/usr/bin/env bash
# Claude Code Stop hook. Refuses to let a session end while scripts/check.sh is red,
# feeding the failure output back to Claude so it fixes it. Exit 2 = "block the stop".
#
# Without a cap this could loop forever on a failure Claude cannot fix, so it allows
# three blocked stops per session (keyed on the session_id the hook receives on stdin)
# and then lets the session end with the failure printed for the human.
set -uo pipefail
cd "$(dirname "$0")/.."
input="$(cat)"
session="$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("session_id","nosession"))' 2>/dev/null || echo nosession)"
state_dir=".loop-state"; mkdir -p "$state_dir"

# The dev loop and an interactive session share this working tree. While a loop story is
# in flight the tree is legitimately red with its half-written files, and that is not the
# interactive session's to fix. run-loop.sh publishes its session id; any other session
# is waved through while that file exists and the loop's process is alive.
if [ -f "$state_dir/active-session" ] && [ "$(cat "$state_dir/active-session")" != "$session" ] \
   && pgrep -f '^claude -p' >/dev/null 2>&1; then
  exit 0
fi
counter="$state_dir/stop-gate.$session"
n=$(cat "$counter" 2>/dev/null || echo 0)

if out="$(bash scripts/check.sh 2>&1)"; then
  rm -f "$counter"
  exit 0
fi

n=$((n + 1)); echo "$n" > "$counter"
if [ "$n" -gt 3 ]; then
  echo "stop-gate: checks still failing after 3 attempts, letting the session end." >&2
  echo "$out" | tail -40 >&2
  exit 0
fi
echo "scripts/check.sh is failing (attempt $n/3). Fix this before stopping:" >&2
echo "$out" | tail -60 >&2
exit 2
