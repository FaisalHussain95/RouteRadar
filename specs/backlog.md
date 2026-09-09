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
- status: done
- size: L

Vite + React + TypeScript in `web/`, implementing `specs/ux/routeradar/RouteRadar.dc.html`
region by region using the tokens in `design-system.md`. `src/types.ts` generated from
`specs/dashboard-data.schema.json` (`json-schema-to-typescript`, a `pnpm gen` script).
`src/data.ts` imports `../../data/site/dashboard.json` when present, else
`web/fixtures/dashboard.json` (a committed fixture produced by S14 on the seeded DB).
`pnpm build` emits hashed static files to `web/dist/`; no runtime fetch anywhere. Extend
`scripts/check.sh` with `pnpm lint && pnpm typecheck && pnpm test && pnpm build`.

- [x] Header, filter bar, fare chart (bands, series, pins, hover card), event feed,
      three modules, event drawer all render from the fixture and match the design
- [x] Filters (destination, carriers, horizon) work client-side with no fetch
- [x] **Carrier legend** (from S13): the legend row under the plot gains six line keys —
      a short stroke in `--carrier-<code>` plus the IATA code — before the existing band and
      news-pin keys. The design's row names no carrier, so without this the only
      colour→carrier key is the filter chips, which scroll out of view on a phone
- [x] Touch behaviour per `design-system.md` § Phone behaviour: tap-to-stick hover with
      `Escape`/tap-outside to clear, a modal drawer (scroll lock, focus trap, `Escape`),
      and a filter bar whose carrier chip strip is the only part that scrolls
- [x] **Stale-data banner** (from S13): when `generated_at` is more than 36 h old the
      header's `● Updated …` status turns into a `--sev-med` banner directly under the
      header reading `Data is <N> days old — the daily ingest has not run since <date>`,
      and the status dot goes from `--color-ok` to `--sev-med`. 36 h, not 24 h, so a timer
      that fires late or a slow ingest does not cry wolf; the pipeline runs at 06:30 daily.
      Tested with a fixture whose `generated_at` is 40 h old and one 12 h old
- [x] **"No data yet" empty state** (from S13): when `series` is empty across every
      destination and horizon the page has never been fed. The chart card, the three
      modules and the feed each collapse to one centred `--text-muted-55` line — the chart
      says `No fares ingested yet · the first run is scheduled for 06:30 CET`, the modules
      and feed say `Waiting for the first ingest`. Calendar bands still draw, because
      `bands[]` comes from the calendar engine and is populated before any fare is. The
      filter bar renders and stays interactive
- [x] **"No data for this combination" empty state** (from S13): distinct from the above —
      `series` is non-empty but the active destination × horizon × carrier filter selects
      nothing. The chart keeps its axes, bands and pins and shows `No fares for
      <dest> at <horizon>d — try another horizon`; with every carrier chip toggled off it
      shows `All carriers hidden` instead. The two messages must not be interchangeable:
      one is a gap in the data, the other is the reader's own filter
- [x] **Per-region empty states** (from S13): a null `arbitrage`, `seasonal_gauge`, or an
      empty `efficiency[]`/`events[]` renders that card's or the feed's own muted line and
      never a zero, a `€0`, an `NaN` or a `+0%` gauge
- [x] Every empty state and the stale banner is covered by a Vitest case driving the
      component from a hand-made fixture, not by eyeballing the page
- [x] Every emitted asset except `index.html` is content-hashed (inspect `web/dist`)
- [x] `vite.config.ts` sets `base` from `VITE_BASE` so the Pages project path
      (`/flight-detective/`) and a root deploy both work
- [x] `web/package.json` defines `gen`, `lint`, `typecheck`, `test`, `build` exactly as
      `.github/workflows/deploy-site.yml` calls them
- [x] `scripts/check.sh` runs the web checks and stays green

Left undone in S15, deliberately, for whoever is next in `web/`: the chart's route tag reads
`CDG → ALL` when the "All · compare" segment is selected, which is what the design does
(`destLabel`) but reads worse than `CDG → ISB/LHE/SKT` would. Cosmetic; not S16's work.

Note for S16: the site is in and `pnpm install --frozen-lockfile` in `deploy-site.yml` is
satisfied by the committed `web/pnpm-lock.yaml`. `web/dist/` and `web/node_modules/` are
git-ignored, so a JSON-only commit from `push-data.sh` stays JSON-only. The Pages project
path is baked in as `vite.config.ts`'s default `base` (`/flight-detective/`) — the workflow
sets no `VITE_BASE` — so if the repo is ever renamed or deployed at a root, that default
moves with it or every asset 404s.

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
- status: blocked  (GitHub Actions has never run on this repo; set back to `doing` once the repo's Actions permissions are enabled — see ### Blocked below)
- size: S

The workflows already exist (`.github/workflows/ci.yml`, `deploy-site.yml`). This story
adds the box side and verifies the chain end to end.

`deploy/push-data.sh`: `git add data/site/dashboard.json`; if `git diff --cached --quiet`
exit 0 with "unchanged"; else commit `data: <observed_on>` as `flight-detective bot` and
push `main` over the deploy key (`GIT_SSH_COMMAND` pointing at
`~/.ssh/flight-detective-deploy`). It must refuse to run with any other file staged.
`deploy/install.sh` prints the deploy-key steps (generate, add to the repo with write
access) rather than doing them; that needs the GitHub UI.

- [x] `push-data.sh` twice with the same JSON commits once (second run prints "unchanged")
- [x] With another file modified in the tree, the script commits only the JSON
- [ ] `deploy-site.yml` runs on a JSON-only commit and on a `web/**` commit, and not on a
      pipeline-code-only commit (check the Actions runs, note the run URLs in the story)
- [ ] Pages is enabled with source "GitHub Actions"; the deployed URL is recorded in
      `README.md`
- [ ] A polling loop against the Pages URL during a deploy never gets a 404

Note from S14 (done): `fd export-site` writes the JSON and `push-data.sh` now publishes it,
so the chain is complete on the box side.

Note from S09 (done): `deploy/install.sh`'s "does not exist yet" warning, the test that
skipped itself on it, and README's "not end-to-end yet" paragraph are all deleted. The
installer now warns about a missing deploy key instead, and README has a § Publishing.

### Blocked

The box side is complete and tested: `deploy/push-data.sh` exists, is executable, and
`tests/test_push_data.py` covers the two script criteria plus its refusals, the bot
identity, the `data: <observed_on>` message and the deploy-key flags (over a stub `ssh`
that runs the remote command against a local bare repo, so no network).

The last three criteria cannot be closed from here, and not for want of trying — each one
needs an action only the repository owner can take in the GitHub UI:

1. **The deploy key is not registered.** `~/.ssh/flight-detective-deploy` does not exist on
   this box, and creating it is only half the job: the public half has to be added to
   `FaisalHussain95/RouteRadar` → Settings → Deploy keys with **Allow write access**. Until
   then `push-data.sh` refuses at its precondition check, by design.
2. **Pages is not switched on**, or at least nothing here can see that it is. Settings →
   Pages → Source: **"GitHub Actions"**. Without it `deploy-pages` fails at the deploy job.
3. **Nothing has been pushed to `main`.** This work is on `dev`, twelve commits ahead of
   `origin/dev`, and this session does not push. So there are no Actions runs to record URLs
   for and no live Pages URL to poll.

What is verified locally in place of criterion 3: `test_only_the_site_and_its_data_trigger_a_
rebuild` checks `deploy-site.yml`'s `paths:` list against nine representative changed files,
including a pipeline-code-only and a specs-only commit. That is the filter's logic, not a
real Actions run, and does not substitute for one.

Also found and fixed here, because it would have made criterion 5 fail on assets: the build
had no `VITE_BASE`, so it fell back to `vite.config.ts`'s `/flight-detective/` while the repo
is `RouteRadar`. `deploy-site.yml` now sets it from `github.event.repository.name`. Verified
locally that `VITE_BASE=/RouteRadar/ pnpm build` emits `/RouteRadar/assets/…` and passes
`check-dist.mjs`.

**To finish this story**, someone with repo access does 1 and 2 above, merges `dev` to `main`
and pushes, **and moves this box's own checkout to `main`** — it is on `dev`, and
`push-data.sh` refuses from there, so without that step the first timed run after this story
closes still publishes nothing (`deploy/install.sh` warns about it, and re-running the
installer after switching is the check). Then: notes the two Actions run URLs (one from the `web/**` commit that merge
carries, one from the next `data:` commit), confirms a pipeline-code-only commit produced no
run, records the Pages URL — `https://faisalhussain95.github.io/RouteRadar/` — and polls it
through a deploy (`while :; do curl -o /dev/null -sw '%{http_code}\n' <url>; sleep 1; done`)
to confirm no 404. Then tick the last three boxes and set `status: done`.

The script is called as `$REPO/deploy/push-data.sh` from `run-pipeline.sh`, with the repo as
the working directory, and must be executable.

## S17 — News from GDELT Cloud
- status: done
- size: M

The public GDELT DOC API answered HTTP 429 to nearly every request on 2026-09-09, from two
hosts, even a one-word query at 20 s spacing. It is replaced by **GDELT Cloud**
(`https://gdeltcloud.com/api/v2`, Bearer `GDELT_API_KEY` from `.env`, present on both
hosts). Measured on 2026-09-09 with `/api/v2/meta/query-units` (free): plan "Explore",
1,048 units this calendar month, every call costs 1 unit, `/meta/*` is free, stories
coverage starts 2026-03-08, search windows are at most 30 inclusive days. Docs, all plain
markdown: `https://docs.gdeltcloud.com/llms.txt` (index),
`…/api-reference/stories/search-stories.md`, `…/api-reference/events/search-events.md`,
`…/api-keys.md`. The `gdelt-cloud` Claude Code plugin is installed at user scope and
carries the same docs as skills.

Design (checked against the plugin's `building-with-the-api` skill on 2026-09-09; load
`gdelt-cloud:getting-started`, `gdelt-cloud:core-api` and `gdelt-cloud:building-with-the-api`
before writing the client):

- `news/gdeltcloud.py` replaces `news/gdelt.py` (delete the old client, its fixtures and
  tests; one news source, per architecture). Two kinds of daily query, both on
  `GET /api/v2/stories` with `days=7`, `sort=recent`, `limit=100` (a call costs 1 unit
  whatever the limit, so never page smaller), `languages=en,fr`, walking `next_cursor`
  until it is `null` (row count is not a truncation signal) with a cap of 300 rows:
  1. **Entity queries** for the six carriers: resolve each name once with
     `GET /api/v2/search?q=<name>&country_match=strict`, inspect the candidates, and commit
     the chosen ids as data in code with the name each resolved from. Then
     `/stories?entity=<id>` — precise coverage of that airline, no semantic fuzz. Category
     `regulatory` unless the guard below reclassifies.
  2. **Semantic queries** for the topical groups (airspace/disruption, pilgrimage/visas,
     strikes at CDG): `search=<query>`, keep rows with `match_type=semantic` and
     `search_score >= 0.55` (threshold as data, with the fixture evidence for it) or
     `match_type=name`.
  Assert `applied_filters` echoes every filter sent and that `applied_filters.ignored` is
  empty; a filter the server did not apply is an error, not an empty week.
- **Relevance guard** on top of both: drop a story unless its title or a top article
  mentions one of a documented keyword list (Pakistan, PIA, Islamabad, Lahore, Sialkot,
  Paris, CDG, the six carriers, their hubs, EASA, Hajj, Umrah, airspace). The list is data
  in code with the reason for each entry.
- Errors: 401 → `GdeltCloudAuthError`; 429 branches on the body's `code`:
  `RATE_LIMITED` waits `details.retry_after` and retries once, `QUOTA_EXCEEDED` raises
  `GdeltCloudQuotaError` and the run stops calling; 5xx/transport → one retry. Parse
  permissively (new fields ship without notice). `X-Quota-Cost` logged per call.
- Stories are already clusters: `dedupe_key` is the story `id`, so `news/dedupe.py`'s
  title-similarity grouping goes away. `NewsEvent` maps `story_date` → `event_date`,
  `title` → `headline`, `top_articles[0].url` → `source_url`, taxonomy group →
  `category`; severity from `metrics.significance` with thresholds written next to the
  data, tie-broken by `article_count`.
- `fd news-ingest` logs `usage.remaining` from `/meta/query-units` (free) at the start of
  each run and warns below 150. Budget: 6 entity + 3 semantic = 9 calls/day plus paging
  ≈ 300 units/month of 1,048.
- Recording fixtures: one `search` resolution per carrier (6 units, free if `/search` is
  under `/meta`, check `X-Quota-Cost`), then each query once in the mode it will run
  (9 units). Documented in the module docstring.

- [x] Fixture-based tests for parsing, the relevance guard, paging and the error classes;
      no network in tests
- [x] The recorded fixtures (6 resolutions + 9 queries) are committed under
      `tests/fixtures/gdeltcloud/` with the recording command documented in the module docstring
- [x] Entity ids for the six carriers committed as data with the name each resolved from
- [x] Re-ingesting the same window is idempotent (story id as the key)
- [x] `fd news-ingest` on the box (key in `.env`) returns real events for the last 7 days
      and the run row shows kept/dropped; paste the command output into this entry
- [x] `specs/architecture.md` § news updated: source, budget, guard, why DOC API was dropped
- [x] `config.py` reads `GDELT_API_KEY`; missing key gives a one-line actionable error

### What the build changed about the design above

Three of the design's assumptions did not survive contact with the API, and the corrections
are the substance of this story. All three are recorded in `specs/architecture.md` § S17.

1. **Semantic queries could not be phrased as `a or b`.** The server *silently truncates* a
   query containing a conjunction to its first term — HTTP 200, `ignored` empty, the dropped
   terms mentioned only in a `note`. The three group queries were re-phrased to one concept
   each and probed until `applied_filters.search` echoed them verbatim.
2. **`MIN_SEARCH_SCORE = 0.55` was far too high**, and `match_type=name` must not be exempt.
   The three committed pools are 300 scored rows over **0.2943–0.4303**, so 0.55 keeps *none*
   of them — it would have read as "no news this week", every day. (It was originally picked
   against the superseded pre-correction recordings, whose 204 rows ran 0.25–0.62 and of
   which exactly one cleared 0.55; wrong there too.) Threshold is now 0.20, below the
   measured floor, with the relevance guard doing the real filtering. `name` rows carry a
   score like any other and get no exemption.
3. **The one-keyword relevance guard let through mostly non-aviation news** ("Pakistan
   tenders for wheat imports"). It became two-axis: a carrier or flying topic passes alone, a
   place only passes next to an aviation word. 300 recorded rows → 16.

Also settled here: semantic queries are **not** paged (a bounded relevance pool has no end
worth reaching, and paging it would cost 3 extra units a day), which is what holds the run
at the budgeted 9 calls. `export/site.py`'s `HISTORY` was decoupled from the news client —
it had imported the DOC archive length, and GDELT Cloud's `MAX_DAYS` is a per-query cap of
30 days, not an archive length.

The severity heuristic lost its direction-reading: `REVERSAL_CUES` demoted "EASA lifts its
ban" from 3 to 1, and `metrics.significance` cannot tell a ban from its lifting. Accepted
for this story; if the dashboard wants direction it is a signal to add beside severity.

### The run on the box (2026-09-09)

```
$ uv run fd news-ingest --days 7
query units: 980 of 1015 left (Explore)
news for the last 7 days: 9 queries, 10 events kept, 299 dropped
```

A second identical run left `news_event` at 10 rows, confirming idempotency on the story id.
Sample of what was stored:

```
2026-09-08 s1 [regulatory]          PIA seeks new leadership and staff
                                    1 article (arynews.tv); significance 0.0753; kept on 'pia'
2026-09-07 s1 [airspace_disruption] Pakistan Air Force chief vows protect air sovereignty
                                    2 articles (samaa.tv, thefrontierpost.com); significance 0.1193; kept on 'airspace'
2026-09-06 s1 [airspace_disruption] Cathay Pacific suspends Dubai Riyadh flights
                                    1 article (asiaone.com); significance 0.0753; kept on 'dubai'
```

## S18 — Stale-news signal on the dashboard
- status: done
- size: S

If every news call fails, the pipeline still exits 0 and exports zero events, and the
feed looks like a quiet week. Export `news_status` (`last_success` date, `last_error`
one-liner) in `dashboard.json` (schema bump), and have the event feed show a muted
"News unavailable since <date>" line when `last_success` is older than 2 days.

- [x] Contract field added, schema regenerated, `web/src/types.ts` regenerated
- [x] Feed renders the line from the fixture; hidden when news is fresh (tested)
- [x] Empty feed with fresh news still reads as "no events this week", not as an error

**What S17 left you.** The `ingest_run` row is already the source for this — provider
`gdeltcloud`, with `rows_kept` and a newline-separated `error`. `last_success` is the newest
`run_at` whose run kept rows; `last_error` is the first line of the newest failing run's
`error`. Two failure shapes are worth distinguishing in the one-liner, because they need
different actions and S17 already separates them:

- **Quota exhausted.** `error` contains `not issued after quota exhausted` and the run
  stopped early, so `queries` is well below 9. This is a sizing problem — nothing will work
  until the month rolls over — and it is the one worth naming on the dashboard.
- **A dead query or two.** One line per failed query and `rows_kept > 0`. Usually transient.

`NewsIngestResult.units` carries the remaining budget (`QueryUnits.is_low`, threshold 150),
but note it is *not* persisted anywhere — it is read from the free `/meta/query-units` per
run and printed. Exporting a "budget is low" warning means storing it, which S18 should
decide on rather than assume.

Also relevant: a full page of semantic results is the normal shape of a *quiet* week as much
as a busy one (the pool is bounded and always fills), so "zero events exported" genuinely
can mean nothing happened. That is exactly why the signal has to come from the run row
rather than from the event count.

### What the build changed about the brief above

Both are written up in `specs/architecture.md` § Decisions from S18.

1. **`last_success` is not "the newest run whose run kept rows".** That definition, taken
   literally, prints "News unavailable" for the quiet week — the case this signal exists to
   distinguish. The guard drops 300 rows to 16 on S17's own recorded week, so a real day
   keeps zero often enough that a three-day gap needs no failure at all. `_news_succeeded`
   is `queries > 0 and (rows_kept > 0 or error is None)`: rows kept, so the calls landed, or
   queries issued and nothing failed.
2. **The budget reading is not exported, and that is the answer to the open question above.**
   Persisting `units` would need a column and a migration to show a warning the reader has
   no action for. S20 owns the budget and the throttle; it decides what has to persist. Until
   then it stays the journal line `fd news-ingest` already prints.

Also settled: the threshold is on the site (`NEWS_STALE_AFTER_DAYS = 2` in
`web/src/lib/staleness.ts`) and compares against the file's `generated_at`, not the reader's
clock — against the clock it would restate the header banner instead of saying the thing only
this signal can say, that fares kept arriving while news stopped.

The schema bump to 2 forced two readers to move with it: `web/src/data.ts`'s
`SUPPORTED_SCHEMA_VERSION`, and the committed `data/site/dashboard.json`, which is tracked
(`.gitignore` un-ignores it for `push-data.sh`) and would otherwise leave the deployed page
throwing "schema_version 1, this build reads 2". It was regenerated with `fd export-site`
against the box's real database, which is where the run rows verifying `news_status` came
from.

## S19 — SerpApi within the free plan
- status: todo
- size: S

The free plan is 250 searches a month; the grid as built (2 origins × 3 destinations × 6
horizons) is 36 a day, about 1,080 a month, so fares stop around the 7th of each month.
Measured 2026-09-09: 37 used, 213 left. ORY returned nothing usable on the first real run,
which the PRD anticipated. Both keys stay on free plans by decision (2026-09-09).

- [ ] `fd ingest` gains a `--plan rotating` (default) mode: CDG only, the three
      destinations, two horizons a day cycling 14/30 → 60/90 → 120/180 by
      `observed_on.toordinal() % 3`, so every cell refreshes every 3 days (the dashboard
      plots at a 3-day step). 6 searches a day. `--plan full` keeps the old grid.
- [ ] `run-pipeline.sh` and the README § What it costs updated; the timer needs no change
- [ ] A budget guard: before ingesting, read `https://serpapi.com/account.json` (free) and
      skip the run with a one-line `ingest_run.error` if `plan_searches_left` is below the
      day's need, rather than burning the last searches on a partial grid
- [ ] `specs/prd.md` F1 and the success metric reworded for a 3-day refresh; the export
      and dashboard already tolerate gaps (verify with the seeded DB)

## S20 — GDELT Cloud within the free plan
- status: todo
- size: M

The 971-unit grant is a 7-day evaluation (plugin docs); afterwards the free plan is 50
units a month, under 2 calls a day, while S17 spends 9 a day. Measured 2026-09-09: 962
units left. Design, per the plugin's `hosted-monitors` skill: monitor runs cost 0 units
and deliver by signed webhook.

- [ ] A throttle first: `fd news-ingest --budget <units/day>` (default 1) rotates the nine
      S17 queries so no more than the budget is spent per day, and stops calling when
      `usage.remaining` from `/meta/query-units` (free) is below 5; the daily journal
      says which queries ran. Ships regardless of the monitor work below.
- [ ] Hosted monitors evaluated: create the nine as paused monitors with
      `POST /api/v2/monitors/preview` first, record the previews as fixtures, and write
      into this entry whether the free plan allows nine monitors and webhook delivery.
      If yes: a `deploy/webhook-receiver` (Python stdlib http.server, signature-verified,
      appends JSONL under `data/news-inbox/`) as a user unit on the VPS, `fd news-ingest
      --from-inbox` reading it, monitors enabled, API calls dropped to zero.
      If no: the throttle stays and this box is documented as the ceiling.
- [ ] README § What it costs updated with the measured monthly units either way

Note from S18 (done): the budget reading is still not persisted — S18 decided that
deliberately and left it to this story, since the throttle is what gives a "budget is low"
warning something to act on. If `--budget` needs the remaining units across runs, that is
the column to add here. S18 also exports `news_status.last_error`, which already names quota
exhaustion in one line on the dashboard (`GDELT Cloud query units exhausted after N
queries`); a throttle that *declines* to call should record something equally readable in
`ingest_run.error` rather than leaving the day silent, or the feed will read as stale after
three throttled days.

