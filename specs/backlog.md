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
- status: done
- size: M

`providers/base.py` protocol; `providers/fake.py` loads JSON fixtures from
`tests/fixtures/fares/<origin>-<dest>-<departure>.json` and returns `Itinerary` objects.
Include at least three realistic fixtures (one per destination) with a mix of carriers
and in/out-of-scope itineraries.

- [x] `FakeFareProvider().search(route, date)` returns validated `Itinerary` objects
- [x] A missing fixture raises a clear `FixtureMissing` error, not `FileNotFoundError`
- [x] Fixtures cover PIA direct, a Gulf 1-stop, and an out-of-scope 2-stop

## S05 — Gregorian calendar windows
- status: done
- size: S

`calendar_engine/gregorian.py`: wedding rush (Nov 15–Jan 15), Christmas/New Year
(Dec 18–Jan 5), French summer Zone C (Jul 1–Aug 31), French Toussaint (Zone C, from the
design: 2026-10-17 → 2026-11-02; encode the rule, not the dates). Multiplier ranges as data
with a comment citing `specs/brainstorming.md` § 2 and `specs/ux/design-system.md`.

- [x] Year-spanning windows work (Jan 3 is both wedding and Christmas)
- [x] Each tag carries `multiplier_low/high`
- [x] Table-driven tests for window edges (Nov 14 vs Nov 15, Jan 15 vs Jan 16)

## S06 — Hijri calendar windows
- status: done
- size: M

`calendar_engine/hijri.py` using `hijri-converter`: Ramadan phase 1 (days 1–15),
Chaand Raat/Eid-ul-Fitr (Eid −4 to +2 days), Hajj/Eid-ul-Adha corridor (Dhul Hijjah
1–13). `calendar_engine/tags.py` unions Gregorian and Hijri tags.

- [x] Eid-ul-Fitr and Eid-ul-Adha tags for 2025, 2026, 2027 match the published dates
      within ±1 day (assert against a small table in the test)
- [x] `tags_for(date)` returns a deduplicated, sorted list
- [x] `fd tag-dates --from --to` materialises `calendar_tag` rows idempotently

## S07 — Ingest pipeline with the fake provider
- status: done
- size: M

`fd ingest --horizons 14,30,60,90,120,180 --provider fake` queries every origin/destination
pair, applies scope filters, upserts observations and writes an `ingest_run` row with
kept/dropped counts.

- [x] Running twice for the same day leaves row counts unchanged
- [x] `ingest_run.rows_dropped` reflects the out-of-scope fixtures
- [x] A provider exception is recorded in `ingest_run.error` and the command exits non-zero

Note from S04: fake fixtures are keyed by exact departure date and only exist for
`2026-12-20` (CDG→ISB/LHE/SKT, ORY→ISB). Six horizons from a real "today" will not hit
them, so the ingest test needs a fixed observation date (e.g. an `--observed-on` option or
an injected clock) with fixtures added for each resulting departure date, or a fake option
that falls back to a date-less fixture. Missing fixtures raise `FixtureMissing`, a
`ProviderError`, so they land in `ingest_run.error` rather than crashing the run. Also:
the fixtures' non-economy records reuse the carrier and flight numbers of an economy
record, and `fare_observation`'s primary key has no cabin column, so filter **before**
upserting or the two collide and one silently overwrites the other.

## S08 — SerpApi Google Flights provider
- status: done
- size: M

`providers/serpapi.py`: real provider behind `SERPAPI_KEY`, locale `fr`, currency EUR,
mapping the response into `Itinerary`. Record one real response as a fixture (manual
step, documented in the module docstring) and test the mapping against it.

- [x] Mapping test passes on the recorded fixture; no network in tests
- [x] Missing key gives a one-line actionable error from `fd ingest --provider serpapi`
- [x] Rate/quota errors are surfaced, not swallowed

## S09 — Scheduled pipeline on the host
- status: done
- size: S

`deploy/flight-detective-pipeline.{service,timer}` for `~/.config/systemd/user/`: oneshot
running `fd ingest && fd tag-dates && fd news-ingest && fd export-site &&
deploy/push-data.sh` daily at 06:30 Europe/Paris. `deploy/install.sh` copies and enables.
Commands that do not exist yet are fine to list: the unit is validated, not run, here.

- [x] `systemd-analyze --user verify` passes on both units
- [x] Service pins the absolute `uv` path (systemd does not source the shell profile)
- [x] A failing step stops the chain (`&&`), so nothing is pushed after a failed ingest
- [x] README section on `journalctl --user -u flight-detective-pipeline`

Note from S08: `fd ingest --provider serpapi` reads `SERPAPI_KEY` from the process
environment or from `.env` in the working directory, so the unit needs `WorkingDirectory=`
set to the repo (or `EnvironmentFile=`). Without it the ingest exits 2 with `error:
SERPAPI_KEY is not set` before anything is written. Also: the default grid is 36
searches a day, about 1 100 a month, above SerpApi's free tier (a few hundred a month; see
https://serpapi.com/pricing), so a daily timer needs a paid plan or fewer horizons; decide
which here and put the number in the README.

Note from S07: `fd ingest` exits 1 whenever *any* route × horizon query failed, even though
the queries that answered were stored. Chained as `fd ingest && fd tag-dates && …`, one
missing cell would skip the export for the day, which is the opposite of what the PRD's
"< 2 % missing cells" metric wants. Decide here whether the unit chains with `;`, or
ingest grows an exit code that distinguishes "partial" from "nothing stored".

Decided: `fd ingest` exits 3 for a partial run and 1 when nothing was gathered, and
`deploy/run-pipeline.sh` continues past 3 and only 3; everything after the ingest stays an
`&&` chain. The search budget stays at the PRD's 36 a day, which needs a paid SerpApi plan;
README § What it costs has the numbers and the drop-in that shrinks the grid. Both are
written up in `specs/architecture.md` § Decisions from S09.

## S10 — GDELT news ingestion
- status: done
- size: M

`news/gdelt.py` client for the GDELT DOC 2.0 API using the three taxonomy groups
(regulatory, airspace/disruption, pilgrimage/visas); `news/dedupe.py`; `fd news-ingest
--days 7`. Severity heuristic documented in code.

- [x] Fixture-based tests for parsing and dedupe (same incident, two outlets → one row)
- [x] Re-ingesting the same window is idempotent
- [x] Each row has category, severity 1–3, source URL

## S11 — Analytics queries
- status: done
- size: L

`analytics/queries.py` implementing the five PRD F5 questions as functions over DuckDB,
plus `fd report <question>` printing a table. Seed a test DB from fixtures.

- [x] Lowest-fare curve per carrier per departure month
- [x] LHE vs SKT spread with a `break_even_eur` parameter for ground transport
- [x] Lead-time curve bucketed by horizon and seasonal band
- [x] Wedding premium vs Feb/Mar baseline
- [x] Carrier efficiency index (EUR per hour of total duration)

## S12 — Explanation layer
- status: done
- size: S

`analytics/explain.py`: for a fare row, its calendar tags and news events within ±7 days,
as a structured object and a one-line sentence.

- [x] Returns tags and events; empty lists, not None, when nothing applies
- [x] Sentence reads naturally for 0, 1 and many reasons (tested)

## S13 — UX: reconcile the RouteRadar design
- status: done
- size: S

The design exists (`specs/ux/routeradar/`, spec in `specs/ux/design-system.md`). This story
closes its gaps. No app code.

- [x] Carrier palette fixed: six distinguishable hues, none equal to a band or severity
      colour, validated colour-blind safe with the `dataviz` skill; new values written into
      `design-system.md` § Tokens with a one-line rationale each
- [x] Phone behaviour specified for hover (tap-to-stick), the drawer, and the filter bar
      overflow, as a short section in `design-system.md`
- [x] Anything the JSON contract needs that the design implies but does not show (empty
      states, "no data yet" before the first ingest, stale-data banner when
      `generated_at` is > 36 h old) written up as acceptance criteria on S15

## S14 — Dashboard data export (the JSON contract)
- status: done
- size: M

`export/site.py`: `DashboardData` Pydantic model per `specs/ux/design-system.md` § Data the
page needs, built from `analytics/`; `fd export-site [--out data/site/dashboard.json]`
writing atomically (`tmp` + `os.replace`). `export/schema.py` + `fd export-schema` writing
`specs/dashboard-data.schema.json`; commit the schema.

- [x] Export on the seeded test DB validates against the schema and round-trips
- [x] Prices are integers of euros; dates are ISO `YYYY-MM-DD`; `schema_version` present
- [x] A reader opening the file mid-write never sees a partial file (test: no `.tmp` left,
      output written via rename)
- [x] `fd export-site` on an empty DB produces a valid file with empty series and a
      `generated_at`, so the site can build before the first ingest

Note from S13: the empty and stale states S15 now lists need the contract to say so.
`arbitrage` and `seasonal_gauge` must be **nullable** (both need fares that an empty or
partial DB does not have: arbitrage needs LHE *and* SKT, the gauge needs a Feb/Mar
baseline), and `series[]`, `events[]`, `efficiency[]` must be allowed to be empty arrays.
`bands[]` is the exception — it comes from `calendar_engine`, not from fares, so it is
populated even before the first ingest and the page draws bands on an empty chart. Emit all
of that in `specs/dashboard-data.schema.json` so S15's types carry the nulls rather than
discovering them at runtime.

Note from S12: `analytics.explain.explain(conn, observation)` answers for **one** fare row
with two queries (its departure date's `calendar_tag` rows, and `news_event` in ±7 days), so
calling it per series point would be thousands of queries per export. Either fetch the whole
range's tags and events once and assemble the per-point reasons in Python, or export the
`bands[]`/`events[]` arrays the design actually consumes and let the page pair them up by
date — the design's hover card does the pairing client-side already. If the export does keep
a per-point sentence, pass `window_days=5`: the PRD's ±7 is the default, but the design's
hover card says ±5. `calendar_engine.tags.label_for(tag)` is the tag → band label map the
`bands[]` array needs, and it falls back to the raw tag rather than raising.

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
- [ ] **Carrier legend** (from S13): the legend row under the plot gains six line keys —
      a short stroke in `--carrier-<code>` plus the IATA code — before the existing band and
      news-pin keys. The design's row names no carrier, so without this the only
      colour→carrier key is the filter chips, which scroll out of view on a phone
- [ ] Touch behaviour per `design-system.md` § Phone behaviour: tap-to-stick hover with
      `Escape`/tap-outside to clear, a modal drawer (scroll lock, focus trap, `Escape`),
      and a filter bar whose carrier chip strip is the only part that scrolls
- [ ] **Stale-data banner** (from S13): when `generated_at` is more than 36 h old the
      header's `● Updated …` status turns into a `--sev-med` banner directly under the
      header reading `Data is <N> days old — the daily ingest has not run since <date>`,
      and the status dot goes from `--color-ok` to `--sev-med`. 36 h, not 24 h, so a timer
      that fires late or a slow ingest does not cry wolf; the pipeline runs at 06:30 daily.
      Tested with a fixture whose `generated_at` is 40 h old and one 12 h old
- [ ] **"No data yet" empty state** (from S13): when `series` is empty across every
      destination and horizon the page has never been fed. The chart card, the three
      modules and the feed each collapse to one centred `--text-muted-55` line — the chart
      says `No fares ingested yet · the first run is scheduled for 06:30 CET`, the modules
      and feed say `Waiting for the first ingest`. Calendar bands still draw, because
      `bands[]` comes from the calendar engine and is populated before any fare is. The
      filter bar renders and stays interactive
- [ ] **"No data for this combination" empty state** (from S13): distinct from the above —
      `series` is non-empty but the active destination × horizon × carrier filter selects
      nothing. The chart keeps its axes, bands and pins and shows `No fares for
      <dest> at <horizon>d — try another horizon`; with every carrier chip toggled off it
      shows `All carriers hidden` instead. The two messages must not be interchangeable:
      one is a gap in the data, the other is the reader's own filter
- [ ] **Per-region empty states** (from S13): a null `arbitrage`, `seasonal_gauge`, or an
      empty `efficiency[]`/`events[]` renders that card's or the feed's own muted line and
      never a zero, a `€0`, an `NaN` or a `+0%` gauge
- [ ] Every empty state and the stale banner is covered by a Vitest case driving the
      component from a hand-made fixture, not by eyeballing the page
- [ ] Every emitted asset except `index.html` is content-hashed (inspect `web/dist`)
- [ ] `vite.config.ts` sets `base` from `VITE_BASE` so the Pages project path
      (`/flight-detective/`) and a root deploy both work
- [ ] `web/package.json` defines `gen`, `lint`, `typecheck`, `test`, `build` exactly as
      `.github/workflows/deploy-site.yml` calls them
- [ ] `scripts/check.sh` runs the web checks and stays green

Note from S14: the contract is `specs/dashboard-data.schema.json` (generated; regenerate
with `fd export-schema`). Things S15 has to know that the design does not show:

- `series[]` points are `{departure_date, price_eur}` objects, not `[date, price]` pairs, so
  `json-schema-to-typescript` emits a named type rather than a tuple. Prices are integers of
  euros and durations integers of minutes throughout; the only floats in the file are a
  band's `multiplier_low`/`multiplier_high`.
- A band's date keys are `from` and `to`. `short_label` is the uppercase chip; `label` is
  the long form for the chart's band caption.
- `arbitrage.spread_eur` is `lhe_eur - skt_eur` (positive = Sialkot cheaper), the **opposite
  sign** to the design's caption "spread SKT − LHE". Reword the caption; do not flip the
  data. `verdict` is already computed with the same break-even the card prints.
- `events[]` has no `impact_score`: the design derives it from severity (high 0.91 / med
  0.64 / low 0.22), so the feed line should read `date · source · severity` instead.
  `body` is always null in v1 (GDELT's artlist has no article text), so the drawer needs
  its own muted line for it, and `impact_text` is the *ingest's* note — how many outlets
  carried the story and which keyword set the severity — not a fare-impact estimate, so
  the drawer's "Est. fare impact" label is wrong as drawn.
- `seasonal_gauge` carries a `method` line; print it as the card's one-line method note
  rather than writing one in the component.
- `web/fixtures/dashboard.json` is produced from the same seeded DB the Python tests use:

  ```python
  import sys; sys.path.insert(0, "tests")
  import duckdb
  from datetime import datetime
  from pathlib import Path
  from conftest import seed_analytics_db
  from flight_detective.export import site

  conn = duckdb.connect(":memory:")
  seed_analytics_db(conn)
  when = datetime.fromisoformat("2026-09-09T06:35:00+02:00")
  site.write_dashboard(
      site.build_dashboard(conn, generated_at=when), Path("web/fixtures/dashboard.json")
  )
  ```

  Pin `generated_at`: the export window is anchored on it, so letting it default to now
  would change the fixture's shape every day. That timestamp reads as fresh against a
  "12 h old" clock; the stale-data case needs its own copy with `generated_at` moved back
  40 h, and the two empty-state cases need hand-made fixtures rather than this one.

Note from S13: `tests/test_design_system.py` vendors the `dataviz` skill's thresholds and
Machado matrices, because the skill lives outside the repo. They matched exactly on
2026-09-09, but nothing detects drift if the skill's floors move. Before touching
`web/src/tokens.css`, invoke the `dataviz` skill and re-run the real
`scripts/validate_palette.js` rather than trusting the copy.

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

Note from S14: `fd export-site` now exists and writes the JSON, so the chain stops only at
`push-data.sh`. README § Scheduled pipeline still carries a narrowed "not end-to-end yet"
paragraph naming just this story; delete it when the script lands.

Note from S09: `deploy/install.sh` warns that `deploy/push-data.sh` does not exist yet, and
`tests/test_deploy.py::test_install_warns_that_the_last_step_of_the_chain_is_missing` skips
itself once it does. Delete both when the script lands here, along with README § Scheduled
pipeline's "The chain is not end-to-end yet" paragraph if S10 and S14 are also done. The
script is called as `$REPO/deploy/push-data.sh` from `run-pipeline.sh`, with the repo as the
working directory, and must be executable.
