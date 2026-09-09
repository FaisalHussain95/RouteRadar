#!/usr/bin/env bash
# Install (or re-install) the daily pipeline into the calling user's systemd. Idempotent:
# it rewrites the two units from the templates beside it and re-enables the timer.
#
# It never uses sudo. These are user units, and the one thing that does need root —
# `loginctl enable-linger`, so the user manager survives a logout — is printed rather
# than done, because on this box the gaming session keeps that manager up anyway.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(flight-detective-pipeline.service flight-detective-pipeline.timer)

# The unit records where uv is because systemd does not source the shell profile. Taking
# it from the installing shell's PATH is what keeps the committed template machine-neutral.
UV="${FD_UV:-$(command -v uv || true)}"
if [ -z "$UV" ]; then
  echo "error: uv is not on PATH; install it or set FD_UV=/abs/path/to/uv" >&2
  exit 1
fi
UV="$(cd "$(dirname "$UV")" && pwd)/$(basename "$UV")"

mkdir -p "$UNIT_DIR"
for unit in "${UNITS[@]}"; do
  sed -e "s#@REPO@#$REPO#g" -e "s#@UV@#$UV#g" "$REPO/deploy/$unit" > "$UNIT_DIR/$unit"
  echo "wrote $UNIT_DIR/$unit"
done

# Catch a bad path or a typo now rather than at 06:30 tomorrow, when the only trace is a
# journal nobody is reading. Not fatal if the tool is absent: a trimmed systemd can still
# run the units it cannot check.
if command -v systemd-analyze >/dev/null; then
  systemd-analyze --user verify "${UNITS[@]/#/$UNIT_DIR/}"
else
  echo "note: systemd-analyze not found, units written unverified" >&2
fi

# The chain is only as complete as the stories that have landed. push-data.sh is its last
# step, so its absence is the cheap tell that a scheduled run will still stop short.
if [ ! -x "$REPO/deploy/push-data.sh" ]; then
  echo "warning: deploy/push-data.sh does not exist yet, so a run stops before the" >&2
  echo "         push (and before any step after the last one that exists)." >&2
fi

systemctl --user daemon-reload
systemctl --user enable --now flight-detective-pipeline.timer
systemctl --user list-timers flight-detective-pipeline.timer --no-pager || true

cat <<TXT

Installed. The timer fires daily at 06:30 Europe/Paris.

  run it now          systemctl --user start flight-detective-pipeline.service
  read the journal    journalctl --user -u flight-detective-pipeline -n 50
  next run            systemctl --user list-timers flight-detective-pipeline.timer
  stop scheduling     systemctl --user disable --now flight-detective-pipeline.timer

If this user is not always logged in, the user manager needs lingering, or the timer
only runs while a session is open:  sudo loginctl enable-linger $USER
TXT
