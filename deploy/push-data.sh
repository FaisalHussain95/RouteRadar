#!/usr/bin/env bash
# Publish one day's export: the last step of deploy/run-pipeline.sh, and the only thing on
# this box that pushes to GitHub. The push is what triggers .github/workflows/deploy-site.yml,
# so this script is the whole distance between "the pipeline ran" and "the site is new".
#
# Why it is this narrow. The box's checkout is a working repo a human also edits, and this
# runs unattended at 06:30. A `git commit -a` here would sweep up whatever was left in the
# tree overnight and publish it. So it stages exactly one path, refuses to run when anything
# else is already staged, and commits only when that one path actually changed — which is
# also the site's change detection: no commit means no Actions run means no rebuild.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

DATA="data/site/dashboard.json"                    # the one file this script may touch
REMOTE="${FD_REMOTE:-origin}"
BRANCH="${FD_BRANCH:-main}"                        # deploy-site.yml only watches main
DEPLOY_KEY="${FD_DEPLOY_KEY:-$HOME/.ssh/flight-detective-deploy}"

# Not the human's git identity: `git log` should say at a glance which commits came from
# the timer. A noreply address because the commits are public and nobody reads that inbox.
BOT_NAME="flight-detective bot"
BOT_EMAIL="flight-detective@users.noreply.github.com"

log() { printf 'push-data: %s\n' "$*"; }
die() { printf 'push-data: %s\n' "$*" >&2; exit 1; }

# --- Preconditions, all of them before the commit ------------------------------------
# Everything that can refuse is checked up front: a run that dies after committing but
# before pushing leaves a commit the next run would have to reason about.

[ -f "$DATA" ] || die "$DATA does not exist: run \`fd export-site\` first"

# The index is shared with whoever else uses this checkout. Committing on top of someone
# else's staged work would publish it, and there is no safe way to guess what they meant.
staged="$(git diff --cached --name-only)"
if [ -n "$staged" ] && [ "$staged" != "$DATA" ]; then
  die "refusing to run, files other than $DATA are staged:"$'\n'"$staged"
fi

# Pushing a branch we are not standing on would publish every unrelated commit that
# happens to be on it. The pipeline's checkout is meant to sit on $BRANCH and stay there.
current="$(git symbolic-ref --quiet --short HEAD || true)"
[ "$current" = "$BRANCH" ] || die "on '$current', expected '$BRANCH' (override with FD_BRANCH)"

# Fail loudly rather than let ssh fall back to ~/.ssh/id_ed25519: the deploy key is scoped
# to this one repo, a personal key is not, and an unattended push should never reach
# further than it needs to. `deploy/install.sh` prints how to create and register it.
[ -r "$DEPLOY_KEY" ] || die "no deploy key at $DEPLOY_KEY: see \`bash deploy/install.sh\`"

# --- Commit, but only on a real change ------------------------------------------------

git add -- "$DATA"
if git diff --cached --quiet; then
  log "unchanged: nothing to commit or push"
  exit 0
fi

# The message names the day the fares are *from*, not the day the push happened: a run at
# 06:30 can publish either, and the log should say which. Null before the first ingest.
observed="$(python3 - "$DATA" <<'PY'
import json, sys

with open(sys.argv[1]) as fh:
    print(json.load(fh)["observed_on"] or "no observations")
PY
)"

git -c "user.name=$BOT_NAME" -c "user.email=$BOT_EMAIL" commit --quiet -m "data: $observed"
log "committed data: $observed"

# --- Push -----------------------------------------------------------------------------
# IdentitiesOnly=yes is the half that matters: without it ssh offers every key the agent
# holds and -i becomes a preference rather than a restriction.
#
# A failed push deliberately keeps the commit. The next run adds its own on top and pushes
# both, so a night without a network costs nothing; undoing it here would lose the day.
# The exception this cannot repair by itself is a remote that has moved on — a rejected
# non-fast-forward needs a human, and says so in git's own words.
export GIT_SSH_COMMAND="ssh -i \"$DEPLOY_KEY\" -o IdentitiesOnly=yes"
git push --quiet "$REMOTE" "$BRANCH"
log "pushed to $REMOTE/$BRANCH"
