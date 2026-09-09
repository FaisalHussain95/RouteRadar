#!/usr/bin/env bash
# Install (or re-install) the daily pipeline into the calling user's systemd. Idempotent:
# it rewrites the two units from the templates beside it and re-enables the timer.
#
# It never uses sudo. These are user units, and the one thing that does need root —
# `loginctl enable-linger`, so the user manager survives a logout — is printed rather
# than done: on a VPS it is a one-off `loginctl enable-linger <user>`, and on a desktop
# the logged-in session keeps that manager up anyway.
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

# The chain ends in a push, and the push needs a key this script deliberately does not
# create: generating one is a command, but registering it and switching Pages on are
# GitHub-UI actions, and half-doing that would leave a key nobody knows is trusted. So it
# checks and prints, which is also the reminder for a box being set up from scratch.
DEPLOY_KEY="${FD_DEPLOY_KEY:-$HOME/.ssh/flight-detective-deploy}"
if [ ! -r "$DEPLOY_KEY" ]; then
  cat >&2 <<TXT
warning: no deploy key at $DEPLOY_KEY, so deploy/push-data.sh cannot
         publish and the daily run will stop at its last step. To set it up:

  ssh-keygen -t ed25519 -N '' -C 'flight-detective deploy' -f $DEPLOY_KEY

  GitHub -> the repo -> Settings -> Deploy keys -> Add deploy key: paste
  $DEPLOY_KEY.pub and tick "Allow write access".

  GitHub -> the repo -> Settings -> Pages -> Source: "GitHub Actions".
TXT
fi

# The other precondition push-data.sh enforces, and the only one that is shell-checkable:
# it refuses to publish from a checkout parked on some other branch, because `git push
# origin main` from there would carry every unrelated commit that branch happens to hold.
# A box set up mid-development sits on a feature branch and would find this out at 06:30.
BRANCH="${FD_BRANCH:-main}"
# --quiet covers a detached HEAD; the redirect covers a box set up from an exported tarball
# rather than a clone. Both end up here with nothing in $current, and "on ''" would read as
# a bug in this script rather than as the thing it is trying to say.
current="$(git -C "$REPO" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
if [ -z "$current" ]; then
  echo "warning: $REPO is not a git checkout sitting on a branch (detached HEAD, or not a" >&2
  echo "         clone at all), so deploy/push-data.sh cannot publish: it needs '$BRANCH'." >&2
elif [ "$current" != "$BRANCH" ]; then
  echo "warning: this checkout is on '$current', and deploy/push-data.sh only publishes" >&2
  echo "         from '$BRANCH'. Move it there (git switch $BRANCH) before the timer fires." >&2
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
only runs while a session is open:  sudo loginctl enable-linger ${USER:-$(id -un)}
TXT
