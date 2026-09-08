# CLAUDE.md — flight-detective

Flight price intelligence for Paris → Islamabad/Lahore/Sialkot. Python 3.12, uv, DuckDB,
Typer CLI. Read `specs/prd.md` (what), `specs/architecture.md` (how) and `specs/backlog.md`
(what next) before doing anything. The parent-directory CLAUDE.md describes the host
machine (a Bazzite TV box); it is not about this project.

## Commands

```
uv sync                   install / update the env
uv run fd --help          the CLI
bash scripts/check.sh     definition of done: format, lint, mypy --strict, pytest
uv run pytest tests/x.py  one test file
```

## Definition of done (mechanical)

A story is done when, and only when:

1. Every acceptance checkbox in its backlog entry is ticked, with a test proving it.
2. `bash scripts/check.sh` prints `ALL CHECKS PASSED`. The Stop hook enforces this.
3. The `qa-reviewer` agent has reviewed the diff and returned APPROVE.
4. It is committed with message `S<nn>: <title>` and its `status:` is `done` in the backlog.

## Rules

- Follow `specs/architecture.md` § Rules every story follows. Do not re-decide the stack.
- No network in tests. Providers are fixture-backed under `tests/fixtures/`.
- Never commit `data/`, `.env`, or a real API key. `SERPAPI_KEY` comes from `.env`.
- Keep stories inside their scope. If a story needs something a later story owns, do
  the minimum and note it in the later story's entry, do not pull the later story in.
- Update `specs/architecture.md` if you make a decision a future session must know. A
  decision that lives only in a chat is lost.
- Comments explain *why*, not *what*. Match the density of `scripts/stop-gate.sh`.
- Money is `Decimal`, dates are `datetime.date`, timezone Europe/Paris when needed.

## Working a story (the loop prompt in `prompts/dev-story.md` says the same)

Pick the first `doing` story, else the first `todo`. Set it to `doing`. Write tests for
its acceptance criteria first, implement, run `scripts/check.sh`, ask `qa-reviewer`, fix
what it finds, tick boxes, commit, set `done`. One story per session. Stop.
