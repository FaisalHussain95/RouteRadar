You are the developer on flight-detective, working one backlog story in this session.

1. Read CLAUDE.md, specs/architecture.md and specs/backlog.md.
2. Pick the story: the first with `status: doing` (a previous session died; resume it,
   check `git status` and `git log -3` to see how far it got), otherwise the first with
   `status: todo`. If there is neither, print `BACKLOG EMPTY` and stop.
3. Set its status to `doing` in specs/backlog.md immediately.
4. Write the tests for its acceptance criteria first, then implement until they pass.
5. Run `bash scripts/check.sh` until it prints ALL CHECKS PASSED.
6. Ask the `qa-reviewer` subagent to review the story (give it the story id). Fix every
   finding it reports, re-run the checks, and ask again until it returns APPROVE. If it
   still requests changes after three rounds, leave the story `doing`, write what is
   blocking into the story entry under a `### Blocked` heading, and stop.
7. Tick the acceptance boxes, set status `done`, and commit everything with the message
   `S<nn>: <title>` (git identity is already configured; do not push).
8. If you learned something a future session must know, add it to specs/architecture.md
   or the relevant later story before committing.
9. Stop. Do not start a second story.
