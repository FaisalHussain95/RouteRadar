# Architecture — Flight Detective

Lead-tech decisions. Dev sessions follow these without re-deciding them. Change here first.

## Shape: two apps, one contract

```
 [ pipeline ]  Python, runs daily on this box              [ site ]  static, built in CI
 fd ingest → fd tag-dates → fd news-ingest → fd export-site
                                              │
                                              ▼
                              data/site/dashboard.json  ── git commit + push ──▶  GitHub
                              (atomic write, versioned schema)                       │
                                                                 push touching web/** or the JSON
                                                                                     ▼
                                                        Actions: pnpm build → deploy-pages (atomic)
                                                                                     ▼
                                                                          GitHub Pages (CDN)
```

Decisions this encodes (2026-09-09):

- **The dashboard is a static site, fully cacheable.** No API, no server-side rendering,
  no runtime data fetch. The site is built *from* `dashboard.json`; the data is bundled as
  a hashed asset. Every deploy is a new immutable set of files behind a tiny `index.html`.
- **The pipeline never builds the site.** It writes one JSON file and commits it. The
  build and deploy live in `.github/workflows/deploy-site.yml`, triggered by that commit
  (and by changes under `web/`). A failed ingest commits nothing, so yesterday's site
  stays up; a failed build leaves the previous Pages deployment serving.
- **Zero downtime comes from Pages, not from us.** A Pages deployment swaps atomically.
  There is no symlink dance and no nginx to keep alive on the box.
- **Rebuild only on change.** The workflow's `paths:` filter is the change detection: a
  pipeline run that produces an identical JSON makes no commit (`git diff --quiet`), and
  no commit means no build.
- **Deployment is a directory.** `web/dist/` is plain files. Pages is the chosen host
  because it needs no server and no secrets beyond the repo itself; moving to Cloudflare
  Pages or the box's nginx changes only the last workflow step.
- **The box needs push rights.** A deploy key limited to this repo, in
  `~/.ssh/flight-detective-deploy` on the box, used only by `deploy/push-data.sh`.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Pipeline | Python 3.12, `uv`, src layout | `hijri-converter`, DuckDB and the scraping APIs are Python-first; uv makes a clean `uv sync` on the immutable host. |
| Storage | DuckDB, single file `data/flight_detective.duckdb` | One process, one host, analytical queries. No server to babysit. |
| CLI | Typer (`fd …`) | Every pipeline step is a subcommand so a systemd timer and a human run the same thing. |
| Models | Pydantic v2 | Validated boundaries at provider adapters and for the JSON contract. |
| HTTP | httpx | Sync is fine; 36 calls a day. |
| Contract | `data/site/dashboard.json`, JSON Schema in `specs/dashboard-data.schema.json` | Generated from the Pydantic model; the site's TypeScript types are generated from the schema, so both sides fail loudly on drift. |
| Site | Vite + React + TypeScript in `web/`, `pnpm` | The design (`specs/ux/routeradar/`) is one React-style component; Vite builds it in seconds on the N100 and emits hashed static files. Next.js was considered and rejected: heavier build, and nothing here needs routing or SSR. |
| Charts | Hand-rolled SVG, as in the design | The design draws its own paths and bands; a chart library would fight it. |
| Serving | GitHub Pages via `.github/workflows/deploy-site.yml` | CDN, atomic deployments, zero infrastructure. Pages sets its own cache headers (short max-age on everything); hashed asset names make that safe. |
| CI | `.github/workflows/ci.yml` runs `scripts/check.sh` | One definition of green, shared by the Stop hook, the QA agent and CI. |
| Tests | pytest with fixtures; Vitest for the site | No network in tests, ever. |
| Quality | ruff, mypy `--strict`; eslint + tsc for `web/` | `scripts/check.sh` is the definition of done for both. |

## Module map (`src/flight_detective/`)

```
cli.py              Typer app; one subcommand per pipeline step
config.py           Settings from env (.env for local), API keys, DB path, site export path
models.py           Route, Itinerary, FareObservation, CalendarTag, NewsEvent, IngestRun
db.py               DuckDB connection, schema migrations (idempotent CREATE), upserts
providers/
  base.py           FareProvider protocol: search(route, departure_date) -> list[Itinerary]
  fake.py           Fixture-backed provider for tests/offline
  serpapi.py        Google Flights via SerpApi
  filters.py        Scope rules from the PRD (stops, layover, duration, cabin)
calendar_engine/
  gregorian.py      Fixed windows: wedding rush, Christmas, French Zone C holidays
  hijri.py          Ramadan, Eid-ul-Fitr, Eid-ul-Adha/Hajj via hijri-converter
  tags.py           tags_for(date) -> list[CalendarTag] (union of the above)
news/
  gdelt.py          GDELT DOC 2.0 client with the taxonomy queries
  dedupe.py         One row per incident (title similarity + same day)
analytics/
  queries.py        The five F5 questions as functions returning records
  explain.py        F6: tags + nearby events for a fare row
export/
  site.py           Builds DashboardData (Pydantic) from analytics and writes dashboard.json
  schema.py         Emits specs/dashboard-data.schema.json from the model
```

`web/` (added by its story): `src/App.tsx` and components per design region, `src/data.ts`
importing `dashboard.json` (real export if present, else `web/fixtures/dashboard.json`),
`src/types.ts` generated from the schema.

`deploy/`: systemd units for the box, `push-data.sh`, `install.sh`.

## Data model (pipeline)

`fare_observation` — one row per itinerary seen:
`observed_on DATE, departure_date DATE, origin, destination, carrier, flight_numbers,
stops INT, layover_minutes INT, duration_minutes INT, price_eur DECIMAL, provider,
raw_ref` with primary key `(observed_on, departure_date, origin, destination, carrier,
flight_numbers)`.

`calendar_tag` — materialised per departure date: `date, tag, multiplier_low,
multiplier_high`. Recomputed by `fd tag-dates`; cheap, deterministic.

`news_event` — `event_date, category, severity (1–3), headline, source_url, dedupe_key,
impact_note`.

`ingest_run` — `run_at, provider, queries, rows_kept, rows_dropped, error` so silent
provider failures show up as a run with zero rows kept. `run_at` is `TIMESTAMPTZ` and the
model refuses a naive datetime.

Decisions from S02 (2026-09-09):

- `flight_numbers` is stored `+`-joined (`QR40+QR620`), not as a DuckDB LIST, because it
  is part of the primary key and DuckDB cannot index a list column. Models carry
  `list[str]`; `db.py` joins on write and splits on read.
- No schema-version table. `db.init_schema` runs a list of `CREATE … IF NOT EXISTS`
  statements; adding a column is one more `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in
  that list. Revisit only when a change needs data rewritten.
- `Money` (and `Multiplier`) reject floats at validation, but accept ints and strings,
  because providers emit whole euros as ints and Decimal strict mode would reject those.
- Settings: `FD_DB_PATH`, `FD_SITE_EXPORT_PATH`, `SERPAPI_KEY`, read from the process
  environment layered over `.env` (path in `FD_DOTENV`). Real env always wins over `.env`.
  The test `conftest.py` points both `FD_DB_PATH` and `FD_DOTENV` into `tmp_path`, which is
  what keeps tests away from `data/` and from a developer's real key.

Decisions from S03 (2026-09-09):

- `providers/filters.py` checks stops, layover, duration and cabin only. Origins and
  destinations are already enforced by the `Route` literals, and the PRD's "carriers of
  interest" row is an analysis focus, not a filter: an in-scope fare on an unlisted carrier
  is recorded, not dropped. All limits are inclusive.

Decisions from S04 (2026-09-09):

- `providers/base.py` defines `FareProvider` (a `runtime_checkable` Protocol: `name` plus
  `search(route, departure_date) -> list[Itinerary]`) and `ProviderError`, the one exception
  family the pipeline catches. Providers raise a `ProviderError` subclass for anything they
  can foresee (quota, missing fixture, unparseable response) so S07 can record it in
  `ingest_run.error`; anything else is a bug and propagates. Providers return unfiltered
  results; scope filtering stays in the pipeline so drop counts reflect what came back.
- Fake fixtures live at `tests/fixtures/fares/<origin>-<dest>-<YYYY-MM-DD>.json`, one file
  per query, with `route` and `departure_date` in the body that **must match the file name**
  (`FixtureInvalid` otherwise). Records omit route/date/provider; the fake stamps them and
  builds `raw_ref` as `fixture:<file>#<index>:<label>`. An empty `itineraries` list is a
  valid "no flights" answer (ORY has none), not an error.
- `FakeFareProvider` defaults its fixtures directory to the repo's `tests/fixtures/fares`
  resolved relative to the package, reaching out of `src/` on purpose: `fd ingest --provider
  fake` and the tests share one set of fixtures.

Decisions from S05 (2026-09-09):

- `calendar_engine/gregorian.py` keeps each window as a `Window` row in `WINDOWS`: stored
  `tag` (`wedding_rush`, `christmas_new_year`, `french_summer`, `french_toussaint`), a band
  `label` and `kind` for the dashboard export, the multiplier range, and a `span(year)` rule
  returning the inclusive first and last day of the edition *starting* in that year.
  `contains(day)` checks the editions starting this year and last year, which is how
  year-spanning windows work without special cases. Add a window by adding a row.
- Christmas/New Year is `kind="wedding"`: the brainstorm treats it as the spike on top of
  the wedding rush, and `religious` is reserved for the Hijri bands. If the export wants
  a separate colour for it, that is a design-token change, not a calendar one.
- Toussaint is a rule, not a lookup: two full weeks ending on the first Monday strictly
  after 1 November (the same in every zone). The tests pin it to the published 2021–2027
  calendars. Both French spans include the Monday classes resume, so the bands match the
  design's `from`/`to` exactly.
- Multiplier ranges: wedding 1.40–1.80 (brainstorm § 2), Christmas 1.60–2.00 (the PRD's
  2x ceiling), summer 1.15–1.35 and Toussaint 1.05–1.15 (bracketing the design's point
  values). The design's wedding 1.34 is below the brainstorm floor; the brainstorm wins as
  the cited source. These are inputs to revise from data, not conclusions.
- `gregorian_tags(day)` returns `CalendarTag`s sorted by tag. S06's `tags_for` unions it
  with the Hijri tags and owns dedupe across the two.

Decisions from S06 (2026-09-09):

- `calendar_engine/hijri.py` mirrors `gregorian.py`: `HijriWindow` is a `Window` whose
  `span(year)` is keyed by **Hijri** year (the edition starting in that year) and whose
  `contains` checks the day's Hijri year and the one before. Tags: `ramadan_phase1`
  (Ramadan 1–15), `eid_ul_fitr` (1 Shawwal −4 to +2 Gregorian days), `hajj_eid_ul_adha`
  (Dhul Hijjah 1–13); all `kind="religious"`. Ranges 0.85–0.95, 1.20–1.60, 1.10–1.30,
  bracketing the design's 0.92 / 1.27 / 1.21 points.
- Dates come from `hijri-converter`'s Umm al-Qura tables, i.e. the *calculated* Saudi
  calendar. Pakistan sights the moon and trails it by a day about half the time (both Eids
  in 2025). The PRD's ±1 day covers this; tests pin against Pakistan's observed dates. Do
  not "correct" the engine to one year's sighting.
- `hijri-converter` is deprecated upstream in favour of `hijridate` (same author, same API
  and tables). Kept because the PRD names it; the import warning is silenced in `hijri.py`.
  Switching is the import line plus `pyproject.toml`, when a session decides to.
- `calendar_engine/tags.py`: `tags_for(day)` unions the two calendars, first source wins
  on a duplicate tag, sorted by tag; `tags_for_range(start, end)` iterates inclusive days.
- `fd tag-dates --from --to` (`--path` as for `db init`) defaults to today (Europe/Paris)
  through +365 days, and **replaces** the range: `db.replace_calendar_tags` deletes the
  range and upserts inside one transaction, so a retired or renamed window leaves no ghost
  rows. Rows outside the range are untouched. It runs `init_schema` itself, so the pipeline
  does not depend on a prior `fd db init`.

## The JSON contract (`dashboard.json`)

Shaped by what the design's component consumes (see `specs/ux/design-system.md` § Data
the page needs). Top level: `schema_version`, `generated_at`, `observed_on`, `carriers[]`,
`destinations[]`, `horizons[]`, `series[]` (per destination × horizon × carrier: `[date,
price_eur]` points), `bands[]`, `events[]`, `arbitrage`, `efficiency[]`, `seasonal_gauge`.
Written with `tmp + os.replace` so a reader never sees a partial file.

## Rules every story follows

- **No network in tests.** Providers and GDELT are called only behind a flag/CLI; tests
  use fixtures. Recording a new fixture is a manual step documented in the provider file.
- **Idempotent writes.** Re-running any `fd` command for the same day must not duplicate
  rows. Use `INSERT … ON CONFLICT DO UPDATE`.
- **Dates are dates.** `datetime.date` in models; timezone is Europe/Paris only where
  time-of-day matters (it mostly does not).
- **Scope filters live in one place** (`providers/filters.py`) and are unit-tested with
  edge cases at exactly 7 h and 15 h.
- **Money is `Decimal`** in EUR in the pipeline. The JSON carries integers of euros
  (prices are whole euros in every provider seen), never floats.
- **Hand-set multipliers** live in `calendar_engine` as data, with a comment citing the
  brainstorm table. They are inputs, not outputs, in v1.
- **The site reads only `dashboard.json`.** No fetches, no env-dependent URLs. If the
  page needs something, the export grows and the schema version bumps.
- **Colours and spacing in `web/` come from tokens** in `specs/ux/design-system.md`, never
  ad-hoc hex values.

## Operational

- `flight-detective-pipeline.service` (oneshot, user unit under `~/.config/systemd/user/`,
  mirroring how `cloudgaming-panel.service` is run) runs `fd ingest && fd tag-dates &&
  fd news-ingest && fd export-site && deploy/push-data.sh`;
  `flight-detective-pipeline.timer` fires it daily at 06:30 Europe/Paris. It pins the
  absolute `uv` path because systemd does not source the shell profile.
- `deploy/push-data.sh` commits `data/site/dashboard.json` on `main` with the message
  `data: <observed_on>` and pushes with the deploy key. If the file is unchanged it exits 0
  without committing. It never touches anything else in the tree.
- `deploy-site.yml` builds on that push and deploys to Pages; `ci.yml` runs the checks on
  every push and PR.
- Secrets: `SERPAPI_KEY` in `.env` (git-ignored), read by `config.py`. Never in code or
  specs. The site build has no secrets at all; the deploy key is on the box only.
- `data/site/dashboard.json` is the **one tracked file under `data/`**; `.gitignore`
  keeps the DuckDB file and everything else out.
