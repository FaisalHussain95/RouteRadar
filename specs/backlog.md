# Backlog

Stories are vertical slices sized for **one Claude session each** (fresh context). Order is
priority order; the dev loop takes the first story whose `status` is `todo`. Statuses:
`todo` → `doing` → `done`. A story left `doing` means the last session died mid-way;
the next session resumes it rather than starting a new one.

Every story lists acceptance criteria as checkboxes. A story is `done` only when every
box is ticked, `scripts/check.sh` is green, and the `qa-reviewer` agent has approved.

---

## S01 — Project skeleton
- status: done
- size: S

Python 3.12 + uv, src layout, Typer CLI with `fd version`, pytest/ruff/mypy wired into
`scripts/check.sh`, Stop hook gate, agents, backlog and specs.

- [x] `bash scripts/check.sh` prints `ALL CHECKS PASSED`
- [x] `uv run fd version` prints the version

## S02 — Domain models and DuckDB schema
- status: done
- size: M

Create `models.py` (Route, Itinerary, FareObservation, CalendarTag, NewsEvent, IngestRun)
and `db.py` with idempotent schema creation and an upsert for fare observations, per
`specs/architecture.md` § Data model. Add `config.py` with `DB_PATH` (default
`data/flight_detective.duckdb`) read from env.

- [x] `fd db init` creates the file and all four tables; running it twice is a no-op
- [x] Upserting the same FareObservation twice yields one row with the second price
- [x] `price_eur` is `Decimal` end to end; a float price fails validation
- [x] Tests use a temporary DB path, never `data/`

## S03 — Scope filters
- status: done
- size: S

`providers/filters.py`: `is_in_scope(itinerary) -> bool` and `apply_scope(itineraries)
-> tuple[kept, dropped_count]` implementing the PRD scope table.

- [x] Rejects 2+ stops, layover > 7 h, duration > 15 h, non-economy
- [x] Boundary tests: exactly 7 h layover and exactly 15 h duration are kept
- [x] Direct flights (0 stops, no layover) are kept

## S04 — FareProvider protocol and fixture-backed fake
- status: todo
- size: M

`providers/base.py` protocol; `providers/fake.py` loads JSON fixtures from
`tests/fixtures/fares/<origin>-<dest>-<departure>.json` and returns `Itinerary` objects.
Include at least three realistic fixtures (one per destination) with a mix of carriers
and in/out-of-scope itineraries.

- [ ] `FakeFareProvider().search(route, date)` returns validated `Itinerary` objects
- [ ] A missing fixture raises a clear `FixtureMissing` error, not `FileNotFoundError`
- [ ] Fixtures cover PIA direct, a Gulf 1-stop, and an out-of-scope 2-stop

## S05 — Gregorian calendar windows
- status: todo
- size: S

`calendar_engine/gregorian.py`: wedding rush (Nov 15–Jan 15), Christmas/New Year
(Dec 18–Jan 5), French summer Zone C (Jul 1–Aug 31), French Toussaint (Zone C, from the
design: 2026-10-17 → 2026-11-02; encode the rule, not the dates). Multiplier ranges as data
with a comment citing `specs/brainstorming.md` § 2 and `specs/ux/design-system.md`.

- [ ] Year-spanning windows work (Jan 3 is both wedding and Christmas)
- [ ] Each tag carries `multiplier_low/high`
- [ ] Table-driven tests for window edges (Nov 14 vs Nov 15, Jan 15 vs Jan 16)

## S06 — Hijri calendar windows
- status: todo
- size: M

`calendar_engine/hijri.py` using `hijri-converter`: Ramadan phase 1 (days 1–15),
Chaand Raat/Eid-ul-Fitr (Eid −4 to +2 days), Hajj/Eid-ul-Adha corridor (Dhul Hijjah
1–13). `calendar_engine/tags.py` unions Gregorian and Hijri tags.

- [ ] Eid-ul-Fitr and Eid-ul-Adha tags for 2025, 2026, 2027 match the published dates
      within ±1 day (assert against a small table in the test)
- [ ] `tags_for(date)` returns a deduplicated, sorted list
- [ ] `fd tag-dates --from --to` materialises `calendar_tag` rows idempotently

## S07 — Ingest pipeline with the fake provider
- status: todo
- size: M

`fd ingest --horizons 14,30,60,90,120,180 --provider fake` queries every origin/destination
pair, applies scope filters, upserts observations and writes an `ingest_run` row with
kept/dropped counts.

- [ ] Running twice for the same day leaves row counts unchanged
- [ ] `ingest_run.rows_dropped` reflects the out-of-scope fixtures
- [ ] A provider exception is recorded in `ingest_run.error` and the command exits non-zero

## S08 — SerpApi Google Flights provider
- status: todo
- size: M

`providers/serpapi.py`: real provider behind `SERPAPI_KEY`, locale `fr`, currency EUR,
mapping the response into `Itinerary`. Record one real response as a fixture (manual
step, documented in the module docstring) and test the mapping against it.

- [ ] Mapping test passes on the recorded fixture; no network in tests
- [ ] Missing key gives a one-line actionable error from `fd ingest --provider serpapi`
- [ ] Rate/quota errors are surfaced, not swallowed

## S09 — Scheduled pipeline on the host
- status: todo
- size: S

`deploy/flight-detective-pipeline.{service,timer}` for `~/.config/systemd/user/`: oneshot
running `fd ingest && fd tag-dates && fd news-ingest && fd export-site &&
deploy/push-data.sh` daily at 06:30 Europe/Paris. `deploy/install.sh` copies and enables.
Commands that do not exist yet are fine to list: the unit is validated, not run, here.

- [ ] `systemd-analyze --user verify` passes on both units
- [ ] Service pins the absolute `uv` path (systemd does not source the shell profile)
- [ ] A failing step stops the chain (`&&`), so nothing is pushed after a failed ingest
- [ ] README section on `journalctl --user -u flight-detective-pipeline`

## S10 — GDELT news ingestion
- status: todo
- size: M

`news/gdelt.py` client for the GDELT DOC 2.0 API using the three taxonomy groups
(regulatory, airspace/disruption, pilgrimage/visas); `news/dedupe.py`; `fd news-ingest
--days 7`. Severity heuristic documented in code.

- [ ] Fixture-based tests for parsing and dedupe (same incident, two outlets → one row)
- [ ] Re-ingesting the same window is idempotent
- [ ] Each row has category, severity 1–3, source URL

## S11 — Analytics queries
- status: todo
- size: L

`analytics/queries.py` implementing the five PRD F5 questions as functions over DuckDB,
plus `fd report <question>` printing a table. Seed a test DB from fixtures.

- [ ] Lowest-fare curve per carrier per departure month
- [ ] LHE vs SKT spread with a `break_even_eur` parameter for ground transport
- [ ] Lead-time curve bucketed by horizon and seasonal band
- [ ] Wedding premium vs Feb/Mar baseline
- [ ] Carrier efficiency index (EUR per hour of total duration)

## S12 — Explanation layer
- status: todo
- size: S

`analytics/explain.py`: for a fare row, its calendar tags and news events within ±7 days,
as a structured object and a one-line sentence.

- [ ] Returns tags and events; empty lists, not None, when nothing applies
- [ ] Sentence reads naturally for 0, 1 and many reasons (tested)

## S13 — UX: reconcile the RouteRadar design
- status: todo
- size: S

The design exists (`specs/ux/routeradar/`, spec in `specs/ux/design-system.md`). This story
closes its gaps. No app code.

- [ ] Carrier palette fixed: six distinguishable hues, none equal to a band or severity
      colour, validated colour-blind safe with the `dataviz` skill; new values written into
      `design-system.md` § Tokens with a one-line rationale each
- [ ] Phone behaviour specified for hover (tap-to-stick), the drawer, and the filter bar
      overflow, as a short section in `design-system.md`
- [ ] Anything the JSON contract needs that the design implies but does not show (empty
      states, "no data yet" before the first ingest, stale-data banner when
      `generated_at` is > 36 h old) written up as acceptance criteria on S15

## S14 — Dashboard data export (the JSON contract)
- status: todo
- size: M

`export/site.py`: `DashboardData` Pydantic model per `specs/ux/design-system.md` § Data the
page needs, built from `analytics/`; `fd export-site [--out data/site/dashboard.json]`
writing atomically (`tmp` + `os.replace`). `export/schema.py` + `fd export-schema` writing
`specs/dashboard-data.schema.json`; commit the schema.

- [ ] Export on the seeded test DB validates against the schema and round-trips
- [ ] Prices are integers of euros; dates are ISO `YYYY-MM-DD`; `schema_version` present
- [ ] A reader opening the file mid-write never sees a partial file (test: no `.tmp` left,
      output written via rename)
- [ ] `fd export-site` on an empty DB produces a valid file with empty series and a
      `generated_at`, so the site can build before the first ingest

## S15 — Static dashboard (`web/`)
- status: todo
- size: L

Vite + React + TypeScript in `web/`, implementing `specs/ux/routeradar/RouteRadar.dc.html`
region by region using the tokens in `design-system.md`. `src/types.ts` generated from
`specs/dashboard-data.schema.json` (`json-schema-to-typescript`, a `pnpm gen` script).
`src/data.ts` imports `../../data/site/dashboard.json` when present, else
`web/fixtures/dashboard.json` (a committed fixture produced by S14 on the seeded DB).
`pnpm build` emits hashed static files to `web/dist/`; no runtime fetch anywhere. Extend
`scripts/check.sh` with `pnpm lint && pnpm typecheck && pnpm test && pnpm build`.

- [ ] Header, filter bar, fare chart (bands, series, pins, hover card), event feed,
      three modules, event drawer all render from the fixture and match the design
- [ ] Filters (destination, carriers, horizon) work client-side with no fetch
- [ ] Empty-state and stale-data banner per S13
- [ ] Every emitted asset except `index.html` is content-hashed (inspect `web/dist`)
- [ ] `vite.config.ts` sets `base` from `VITE_BASE` so the Pages project path
      (`/flight-detective/`) and a root deploy both work
- [ ] `web/package.json` defines `gen`, `lint`, `typecheck`, `test`, `build` exactly as
      `.github/workflows/deploy-site.yml` calls them
- [ ] `scripts/check.sh` runs the web checks and stays green

## S16 — Data push and Pages deploy
- status: todo
- size: S

The workflows already exist (`.github/workflows/ci.yml`, `deploy-site.yml`). This story
adds the box side and verifies the chain end to end.

`deploy/push-data.sh`: `git add data/site/dashboard.json`; if `git diff --cached --quiet`
exit 0 with "unchanged"; else commit `data: <observed_on>` as `flight-detective bot` and
push `main` over the deploy key (`GIT_SSH_COMMAND` pointing at
`~/.ssh/flight-detective-deploy`). It must refuse to run with any other file staged.
`deploy/install.sh` prints the deploy-key steps (generate, add to the repo with write
access) rather than doing them; that needs the GitHub UI.

- [ ] `push-data.sh` twice with the same JSON commits once (second run prints "unchanged")
- [ ] With another file modified in the tree, the script commits only the JSON
- [ ] `deploy-site.yml` runs on a JSON-only commit and on a `web/**` commit, and not on a
      pipeline-code-only commit (check the Actions runs, note the run URLs in the story)
- [ ] Pages is enabled with source "GitHub Actions"; the deployed URL is recorded in
      `README.md`
- [ ] A polling loop against the Pages URL during a deploy never gets a 404
