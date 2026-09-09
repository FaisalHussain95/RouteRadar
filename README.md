# Flight Detective

Fare tracking and *explanation* for Paris → Islamabad / Lahore / Sialkot: a daily ingest
into DuckDB, a calendar engine that knows why December and Eid are expensive, and a
static dashboard.

- `specs/prd.md` — what it is for and what "done" looks like.
- `specs/architecture.md` — the decisions a session must not re-take.
- `specs/backlog.md` — the stories, in priority order.

## Working on it

```
uv sync                   install / update the environment
uv run fd --help          the CLI: db init, tag-dates, ingest, …
bash scripts/check.sh     definition of done: format, lint, mypy --strict, pytest
```

`SERPAPI_KEY` goes in `.env` (git-ignored) in the repo root. Without it everything still
works against the fixture-backed fake provider: `uv run fd ingest --provider fake
--observed-on 2026-12-06 --horizons 14`.

## Scheduled pipeline

One run of the whole thing is `deploy/run-pipeline.sh`:

```
fd ingest → fd tag-dates → fd news-ingest → fd export-site → deploy/push-data.sh
```

Steps after the ingest are chained with `&&`, so a failure stops the run before anything
half-built is pushed. The ingest itself is the exception: it exits **3** when some
(route, horizon) cells failed and the rest were stored, and the chain continues, because
the PRD budgets under 2 % missing cells and a day with a few holes is still worth
exporting. Exit 1 (nothing was gathered) and exit 2 (`SERPAPI_KEY` is not set) stop it.

`deploy/flight-detective-pipeline.{service,timer}` run that daily at 06:30 Europe/Paris.
They are templates — `@REPO@` and `@UV@` are filled in at install time, because systemd
searches no `PATH` and sources no shell profile, so the absolute `uv` path has to be
written into the unit:

```
bash deploy/install.sh          # renders, verifies, daemon-reloads, enables the timer
```

Re-run it after moving the repo or changing `uv`. It never uses sudo; if this user is not
always logged in, the user manager needs `sudo loginctl enable-linger $USER` or the timer
only runs while a session is open.

**The chain is not end-to-end yet.** `fd news-ingest` (S10), `fd export-site` (S14) and
`deploy/push-data.sh` (S16) do not exist, so a scheduled run today stores fares and
calendar tags and then stops at the first missing step with a usage error. Installing now
gets the daily ingest running; the export and the push start working when those land, with
no change to the unit.

### Watching it

```
journalctl --user -u flight-detective-pipeline -n 50        # the last run
journalctl --user -u flight-detective-pipeline -f           # follow the next one
journalctl --user -u flight-detective-pipeline --since today
systemctl --user list-timers flight-detective-pipeline.timer  # when it fires next
systemctl --user start flight-detective-pipeline.service      # run it now
systemctl --user disable --now flight-detective-pipeline.timer
```

Each step prints a timestamped `run-pipeline:` line, so the journal says which step a
failure came from; `fd ingest` also lists every failed cell on stderr, and the same
counts are in the `ingest_run` table.

### What it costs

The default grid is 6 routes × 6 horizons = **36 searches** per day, about 1 100 a month.
That is well past SerpApi's free tier and needs a paid plan — check the current tiers at
<https://serpapi.com/pricing>. To spend less, override the grid in a drop-in rather than
editing the script or the unit, which `install.sh` rewrites every time it runs:

```
systemctl --user edit flight-detective-pipeline.service
```

```
[Service]
Environment=FD_HORIZONS=30,90,180     # 18 searches a day, ~550 a month
Environment=FD_PROVIDER=fake          # or: a dry run that touches no API at all
```
