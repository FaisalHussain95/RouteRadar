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
uv run fd --help          the CLI: db init, tag-dates, ingest, news-ingest, …
bash scripts/check.sh     definition of done: both halves, Python and the site
```

`scripts/check.sh` is ruff, mypy `--strict` and pytest, then `pnpm gen/lint/typecheck/
test/build` in `web/`. It needs `pnpm` on `PATH`; nvm lives in the shell profile, so a
non-login shell wants `. ~/.nvm/nvm.sh` first.

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

`fd news-ingest` is the one step that tolerates its own partial failure: it pulls the
four GDELT taxonomy queries (brainstorming §3), collapses duplicate coverage of one
incident into one `news_event` row, and only fails the command when *every* query died.
News is context around the fares, not the dataset, so one flaky query on a free API must
not cost the day's export. GDELT needs no key and no quota; the courtesy pause between
queries makes the step take about 20 seconds.

`fd export-site` writes `data/site/dashboard.json`, the one file the static site reads:
carriers, destinations and horizons for the filter bar, a fare curve per
destination x horizon x carrier, the calendar bands, the news pins, and the three module
cards. It runs on any database, including one that has never been ingested into, so the
site can build before the first pipeline run. The shape is described by
`specs/dashboard-data.schema.json`, generated from the Pydantic model by
`fd export-schema` and committed; regenerate it whenever the model changes. The write is a
temp file plus a rename, so a reader never sees a half-written export.

### Publishing

`deploy/push-data.sh` is the last step and the only thing here that pushes. It stages
`data/site/dashboard.json` and nothing else, refuses to run if anything else is already
staged, and commits as `flight-detective bot` with the message `data: <observed_on>` —
then pushes `main`. An export identical to yesterday's makes no commit, and no commit is
what stops the site rebuilding for nothing.

It publishes from `main` and refuses from anywhere else — pushing a branch you are not
standing on carries every unrelated commit on it — so **the box's checkout has to sit on
`main`**, not on a feature branch. `deploy/install.sh` warns when it does not. `FD_BRANCH`,
`FD_REMOTE` and `FD_DEPLOY_KEY` override the three defaults for a rehearsal.

The push uses a deploy key scoped to this repo, `~/.ssh/flight-detective-deploy`, with
`IdentitiesOnly=yes` so ssh cannot fall back to a personal key. `deploy/install.sh` prints
how to create it and register it; the two GitHub-side steps are manual, since neither can
be done from a shell:

- the repo → **Settings → Deploy keys → Add deploy key**, the `.pub` contents, **Allow
  write access** ticked;
- the repo → **Settings → Pages → Source: "GitHub Actions"**.

That push triggers `.github/workflows/deploy-site.yml`, which builds `web/` and deploys to
Pages. It runs on `web/**` and on the exported JSON only, so a commit that touches just
pipeline code or specs rebuilds nothing. The deployment swaps atomically and a failed build
leaves the previous one serving, which is the zero-downtime requirement in `specs/prd.md`
F8 without a server of our own.

The site is at **<https://faisalhussain95.github.io/RouteRadar/>**. Pages serves a project
site from `/<repo>/` and Vite bakes that prefix into every asset URL, so the workflow sets
`VITE_BASE` from the repository name; the local default in `web/vite.config.ts` is only for
`pnpm dev` and `vite preview`.

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
counts are in the `ingest_run` table — `fd news-ingest` logs there too, under provider
`gdelt`, so a taxonomy query that has quietly stopped matching anything shows up as a run
with `rows_kept = 0`.

### What it costs

Only the fare ingest costs money; GDELT is free. The default grid is 6 routes × 6 horizons = **36 searches** per day, about 1 100 a month.
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

## The dashboard (`web/`)

Vite + React + TypeScript, built to static files and served by GitHub Pages. It reads one
file and makes no request of its own at runtime.

```
cd web
pnpm install
pnpm dev                  local dev server
pnpm gen                  regenerate src/types.ts from specs/dashboard-data.schema.json
pnpm build                → web/dist, all assets content-hashed
```

- **Where the data comes from.** `src/data.ts` imports `@dashboard-data`, an alias resolved
  in `vite.config.ts` to `data/site/dashboard.json` when the pipeline has written one and to
  the committed `web/fixtures/dashboard.json` otherwise. It is an import, not a fetch: the
  JSON is bundled into the hashed JS, so a clean checkout builds and a deployed page never
  asks the network for anything.
- **Types are generated, not written.** `pnpm gen` runs `json-schema-to-typescript` over the
  schema `fd export-schema` emits, so a field that moves in the Pydantic model is a
  TypeScript error rather than an `undefined` on the page. `src/types.ts` is committed and
  regenerated by `scripts/check.sh`.
- **Regenerating the fixture**: `uv run python web/fixtures/build.py`. It is the seeded test
  database plus a handful of hand-written news rows, with `generated_at` pinned — see the
  script's docstring for why both.
- **The base path.** `vite.config.ts` reads `VITE_BASE`, defaulting to `/flight-detective/`
  because Pages serves a project site under the repo name. `VITE_BASE=/ pnpm build` for a
  root deploy. `pnpm build` fails if any asset but `index.html` is unhashed or if
  `index.html` points outside the base.
