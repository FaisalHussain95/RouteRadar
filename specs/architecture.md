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
ingest.py           run_ingest(): routes × horizons through a FareProvider, filter, upsert, log
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
  ingest.py         run_news_ingest(): taxonomy queries, dedupe, severity, upsert, log
analytics/
  queries.py        The five F5 questions, plus fare_series for the chart, as records
  explain.py        F6: tags + nearby events for a fare row
export/
  site.py           Builds DashboardData (Pydantic) from analytics and writes dashboard.json
  schema.py         Emits specs/dashboard-data.schema.json from the model
```

`web/` (S15):

```
src/App.tsx           filter/hover/drawer state; every region is a child
src/data.ts           imports the @dashboard-data alias, checks schema_version
src/types.ts          generated by `pnpm gen`; not hand-edited
src/tokens.css        design-system.md § Tokens, verbatim
src/components/       Header, FilterBar, FareChart, HoverCard, Legend, EventFeed,
                      EventDrawer, modules/{Arbitrage,Efficiency,SeasonalGauge}Card
src/lib/              pure logic, unit-tested without a DOM: dates, staleness, select
                      (filters and empty states), chart (geometry), hover, format,
                      severity, dom
fixtures/             dashboard.json + build.py, the committed fallback data
scripts/              gen-types.mjs (`pnpm gen`), check-dist.mjs (part of `pnpm build`)
```

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

Decisions from S07 (2026-09-09):

- `ingest.py` holds the pipeline (`run_ingest(conn, provider, observed_on=, horizons=)`),
  and `cli.py` only parses options and prints. `ROUTES` is every `Origin` × `Destination`
  pair in a fixed order; `DEFAULT_HORIZONS` is the PRD's 14/30/60/90/120/180.
- **One bad query costs one cell, not the day.** A `ProviderError` on a query is appended
  to `ingest_run.error` (`"CDG->ISB 2027-01-05: <message>"`, one per line, whitespace
  flattened so a multi-line pydantic error stays on its line) and the loop goes
  on; what did answer is stored; the CLI exits 1 and lists the failures on stderr. Any other
  exception propagates before anything is written. This is what the PRD's "< 2 % missing
  cells" metric needs.
- All queries run first, then observations and the `ingest_run` row are written in one
  transaction, so a crash leaves no half-day and no run row.
- Kept itineraries are deduplicated by `fare_observation` primary key **after** scope
  filtering, lowest price wins. The key has no cabin column, and the fixtures' business
  records reuse the economy flight numbers; DuckDB's multi-row upsert keeps the *first*
  duplicate silently, so the choice is made explicitly. `rows_kept` counts rows written,
  after dedupe; `rows_dropped` counts scope drops only.
- `ingest_run` is a log: plain `INSERT`, one row per run, never upserted. `run_at` is
  `datetime.now(Europe/Paris)`; DuckDB stores the instant.
- `fd ingest --observed-on YYYY-MM-DD` exists to replay a day against fixtures (the
  fake's fixtures are all dated 2026-12-20, so `--observed-on 2026-12-06 --horizons 14`
  is the offline smoke test) and, later, to backfill. `--provider` is looked up in
  `cli.PROVIDERS`, a name → constructor dict; S08 adds `serpapi` to it.
- `pytz` is a runtime dependency: DuckDB refuses to return a `TIMESTAMPTZ` column to
  Python without it (`Required module 'pytz' failed to import`). Nothing imports it.

Decisions from S08 (2026-09-09):

- `providers/serpapi.py` is one SerpApi `google_flights` request per query, one-way,
  economy, pinned to `hl=fr` / `gl=fr` / `currency=EUR`, no `stops` filter (the pipeline
  filters). `carrier` and `flight_numbers` are parsed from `flight_number` ("PK 750"):
  `airline` is display text and `hl=fr` localizes inconsistently (airport names French,
  `travel_class` English), so nothing matches on display strings. `cabin` is the class
  asked for. `raw_ref` is `serpapi:<search_metadata.id>#<best|other>:<index>`.
- Errors: 401 → `SerpApiAuthError`, 429 or SerpApi's "run out of searches" text →
  `SerpApiQuotaError`, connection/timeout → `SerpApiTransportError`, unparseable →
  `SerpApiResponseInvalid`; all `ProviderError`s, so `run_ingest` records them per cell.
  Google's "hasn't returned any results" is `[]`, not an error. 5xx and transport errors
  are retried exactly once after 2 s; 4xx never. An option with no `price` is skipped.
- A missing `SERPAPI_KEY` is a configuration error, not a failed query: `fd ingest
  --provider serpapi` prints one `error: SERPAPI_KEY is not set …` line on stderr, exits
  2 and writes nothing. `cli.PROVIDERS` entries are constructors so this happens at run
  time, after option parsing, and `fd --help` never needs a key.
- The key is redacted (`***`) from every error message and from recorded fixtures, and
  `record_fixture` drops SerpApi's account-scoped archive URLs (`json_endpoint`,
  `raw_html_file`, …) from `search_metadata`; only `id` is read from it.
- `tests/fixtures/serpapi/CDG-ISB-2026-12-20.json` is a **real recording** (search id
  `6aa0a67073bf59e675609d77`, 2026-09-09) made with `python -m
  flight_detective.providers.serpapi CDG ISB 2026-12-20`. Its `search_parameters` block is
  asserted equal to what `query_params` sends, so changing the request parameters means
  re-recording. Recording costs one search. The default grid is 36 searches a day, about
  1 100 a month, well above SerpApi's free tier (a few hundred; see
  https://serpapi.com/pricing), so the timer (S09) needs a paid plan or fewer horizons.

Decisions from S09 (2026-09-09):

- The daily chain lives in `deploy/run-pipeline.sh`, not in `ExecStart=`. `ExecStart` has
  no shell, so a `&&` chain there would need `/bin/sh -c '…'` anyway, and a script can be
  tested: `tests/test_deploy.py` runs it with a stub `uv` and a stub `push-data.sh` and
  asserts which steps did *not* run after a failure.
- **`fd ingest` now exits 3 for a partial run** (some route × horizon queries failed, the
  rest were stored), 1 when every query failed, 2 for a configuration error. This is the
  S07 note's open question, decided: `run-pipeline.sh` continues past 3 and only 3, so one
  dead cell does not cost the day's export, while a provider that answered nothing stops
  the run before `push-data.sh`. S07's "exits non-zero" still holds for both.
- The units are **templates**: `deploy/install.sh` substitutes `@REPO@` and `@UV@` and
  writes `~/.config/systemd/user/`. The absolute `uv` path has to be in the unit (systemd
  sources no profile) and cannot be committed — `~/.local/bin/uv` here, `/usr/bin/uv` in
  CI. `install.sh` runs `systemd-analyze --user verify` on what it wrote, so a bad path
  fails at install time rather than silently at 06:30.
- `OnCalendar` names `Europe/Paris` explicitly rather than inheriting the host timezone,
  and the timer is `Persistent=true` (a run missed across a reboot happens at next start)
  with `RandomizedDelaySec=5min`.
- Grid size is `FD_HORIZONS`/`FD_PROVIDER` in the service's environment, defaulting to the
  PRD's six horizons: 36 searches a day, ~1 100 a month, which needs a paid SerpApi plan.
  Overrides go in a `systemctl --user edit` drop-in, not in the unit: `install.sh`
  re-renders the unit from the template every run. README § What it costs has the numbers.
- `install.sh` never sudoes. `loginctl enable-linger` is printed as a hint; on this box the
  auto-logged-in gaming session keeps the user manager alive anyway.

Decisions from S10 (2026-09-09):

- The module map's `news/` gained a third file: `news/gdelt.py` is the DOC 2.0 client,
  `news/dedupe.py` the incident grouping, and **`news/ingest.py`** the pipeline
  (`run_news_ingest`) plus the severity heuristic — the same split as `providers/` and the
  top-level `ingest.py`, so `cli.py` stays option-parsing and printing.
- **The taxonomy is four queries, not eleven.** Brainstorming §3's phrases are OR-ed into
  one request per group; `"EASA" "PIA"` is its own query because GDELT will not mix an
  implicit AND with an OR group at the same level. Each `TaxonomyQuery` carries a `slug`
  that names its fixture, so adding a group is one row plus one file.
- **`timespan=<n>d`, not an explicit datetime range.** `--days` means "what GDELT has seen
  in the last N days", which keeps the clock out of the request and out of the fixtures.
  DOC 2.0 only indexes a rolling three months, so `--days` is capped at 90.
- **`event_date` is the Paris date of GDELT's `seendate`.** `artlist` offers nothing
  closer to when the incident happened, and for a disruption the two are hours apart; Paris
  because every other date here is Paris-local and S12 joins events to fares on that
  calendar.
- **Dedupe is same-day plus title similarity, nothing cleverer.** GDELT gives no cluster
  id in `artlist`; Event Registry, which does, stays the PRD's open question.
- **Similarity is Jaccard overlap of content words, not character similarity.**
  `difflib.SequenceMatcher` over normalised titles was the first attempt and is recorded in
  `news/dedupe.py` as a trap: two *different* incidents one salient word apart score 0.79–
  0.92 as strings ("… Paris flights" vs "… Lahore flights", "… to Indian carriers" vs "… to
  all carriers", "EASA bans PIA" vs "EASA lifts ban on PIA"), i.e. above any threshold that
  still merges a genuine reword. Dropping a short list of function words and comparing the
  remaining word *sets* puts those pairs at 0.57–0.67 and real duplicates at 0.83–1.00, so
  `SIMILARITY_THRESHOLD = 0.75` sits in a wide gap. "all" is deliberately not a function
  word. False splits are preferred to false merges: a duplicate marker is cosmetic, a false
  merge deletes an event and files the survivor under the wrong headline.
- `dedupe_key` is `<event_date>:<slug of the canonical title>-<sha256[:8]>`, and the
  canonical article is the earliest `(seen_at, url)` in the group — candidates are sorted
  before grouping so the key does not depend on GDELT's listing order. Known limitation:
  a rolling window eventually drops the canonical article and the same incident then gets a
  new key and a second row. Accepted; the alternative is fuzzy-matching every new incident
  against the whole table.
- **Severity 1–3 is a keyword heuristic on the headline**, documented in `news/ingest.py`:
  3 for capacity gone (closure, ban, suspension, grounding, strike, war), 2 for capacity
  strained (delays, disruption, cancellations, quota cuts, reroutes), 1 for everything else
  the taxonomy caught. Matching is word-bounded on the normalised title, highest band wins,
  and the matching keyword goes into `impact_note` so a surprising severity is traceable.
  Like the calendar multipliers these are inputs to revise from data, not conclusions.
- **The scale reads direction.** Half of what the regulatory taxonomy catches is a
  restriction being undone — "EASA *lifts* its ban", "airspace reopened", "strike called
  off" — carrying the same band-3 keyword as the event it reverses. A `REVERSAL_CUES` word
  anywhere in a band-3 headline demotes the row to 1 and names the cue in `impact_note`:
  the scale measures capacity *lost*, and a maximum-severity marker on a fare drop is worse
  for S12 than no marker. Band 2 is left alone; reading direction into a delay would
  over-fit. `severity_for` returns a `Severity(level, keyword, reversed_by)` rather than a
  bare int so the demotion is inspectable.
- **The cue list holds only verbs with no other sense**, because the demotion goes straight
  from 3 to 1 and a false demotion is as bad as the bug it fixes. "end"/"ends" and "revoked"
  were tried and removed — "closure extended to the *end* of October", "strike *ends* its
  third day", "suspends flights after its licence was *revoked*" are all live capacity
  losses. Requiring the cue to sit next to the keyword does not rescue them ("strike ends"
  is adjacent and still wrong), so the list is kept narrow instead;
  `tests/test_news_ingest.py::test_an_ambiguous_word_does_not_demote_a_live_capacity_loss`
  pins all four.
- **`fd news-ingest` exits 1 only when every taxonomy query failed.** A dead query is one
  line in `ingest_run.error` and a warning on stderr; the run still stores what answered and
  exits 0. `deploy/run-pipeline.sh` chains it with `&&`, and news is context around the
  fares rather than the dataset, so one flaky free-API query must not cost the day's export.
  A totally unreachable GDELT does stop the chain — nothing new would be exported anyway.
- News runs are logged to `ingest_run` under provider `gdelt`: `queries` is the number of
  taxonomy queries, `rows_kept` the incidents written, `rows_dropped` the articles that
  collapsed into an existing incident. Reusing the table is what makes "the pilgrimage
  query has quietly stopped matching anything" visible.
- The committed fixtures in `tests/fixtures/gdelt/` are **hand-written** to the documented
  `artlist` shape, not recordings like the SerpApi one: what a live taxonomy query returns
  depends on the week it runs, so a recording would date immediately.
  `python -m flight_detective.news.gdelt [DAYS] [DIR]` records real ones over them (free,
  no key) and the parsing tests read whatever is in the files. Unlike `providers/fake.py`,
  the client does *not* know where the test tree is: nothing reads these fixtures at run
  time, so the path lives in the recorder's `_main` (relative to the working directory) and
  in `tests/conftest.py`.
- `tests/conftest.py` exposes `gdelt_body(slug)` so the client, dedupe and pipeline tests
  all answer from the same four files.

Decisions from S11 (2026-09-09):

- `analytics/queries.py` is five read-only functions returning frozen dataclasses, one per
  PRD F5 question; `cli.py` owns `fd report <question>` and the table rendering. The
  records are dataclasses, not Pydantic: they never cross a boundary that needs
  validating. S14 turns them into the JSON contract, which *is* a Pydantic model.
- **`avg()` is never used.** DuckDB's `avg()` over a `DECIMAL` returns a `DOUBLE`, and a
  float is what `models.Money` exists to keep out of a fare table. Every aggregate asks for
  `sum(price_eur)` (exact `DECIMAL(38,2)`) and `count(*)` and divides in `Decimal`,
  quantized to the cent with `ROUND_HALF_UP`. Ratios (the wedding multiplier, euros per
  hour) divide the *sums*, so they carry one rounding rather than two.
- Every question aggregates over all observation days, not the latest snapshot: the
  lowest-fare curve is the cheapest the market ever showed, the arbitrage rows are the
  cheapest way into each airport on a departure date. `airport_arbitrage(observed_on=)` is
  the pin for "what does it cost today". All five take the same scope filters
  (`origin`/`destination`/`carrier`, whichever apply) so a caller narrows instead of
  re-querying.
- **Lead times are bucketed to the *nearest* configured horizon**, not matched exactly: a
  run that slips a day lands 13 or 15 days out, and a horizon added later shifts every
  boundary. The SQL is a `CASE` over `days * 2 <= lo + hi`, integer arithmetic so the
  midpoint is exact; a lead time exactly between two horizons goes to the shorter one.
  Departures earlier than the day they were seen are dropped — only a backfill mistake
  makes one, and they would all pile into the shortest bucket.
- **A departure in two calendar windows contributes to both band curves.** That is what a
  band curve means, and picking one tag per date would need a precedence order nothing in
  the PRD supplies. Consequence: observation counts across bands sum to more than the
  table. Untagged departures land in `UNTAGGED_BAND` (`off_peak`), which is the curve the
  banded ones are read against, not a hole.
- `airport_arbitrage` returns only departure dates with a fare into **both** LHE and SKT: a
  spread needs two prices, and a date where one airport was never quoted would otherwise
  read as an infinite saving. ISB rides along as context and may be `None`. The verdict is
  `lhe`/`skt` when one airport wins by more than `break_even_eur` (default 34, the design's
  `ground_transfer_eur`), else `either` — inside the transfer cost the choice is about
  convenience, not money.
- `wedding_premium` returns `None` when either side is empty: a premium against nothing is
  unknown, not `1.00`, and `fd report` prints `no rows`. The baseline is **months**
  (February/March), as the PRD words it, not "dates with no tag". Known drift: Ramadan is
  cheap and moves ~11 days earlier a year, sitting inside Feb/March for the rest of the
  decade, so the baseline is depressed and the premium reads a little high. Fixing that
  means changing the baseline in the PRD, not special-casing it here.
- The carrier efficiency index is total euros over total hours, not the mean of per-row
  indices, so a carrier is not rewarded for one short cheap hop among long ones.
  `avg_price_eur` and `avg_duration_hours` are reported beside it because the index alone
  cannot tell "cheap and slow" from "dear and fast".
- `fd report` prints a hand-rolled fixed-width table rather than pulling in `rich`, so the
  output is byte-stable and the tests can assert on a row. `--break-even` is parsed from a
  **string** into `Decimal`; a `float` option would put a float back into the money path.
  `Decimal` also builds `NaN` and `Infinity` without complaint, and a `NaN` survives until
  the spread comparison and surfaces as a traceback, so non-finite and negative amounts are
  rejected at parse time. `--origin`/`--destination` are validated against
  `get_args(Origin)`/`get_args(Destination)`. `--break-even` defaults to `None` and the
  arbitrage branch substitutes `DEFAULT_BREAK_EVEN_EUR`, so the option can be checked like
  the others; a real default would make "asked for" indistinguishable from "left alone".
- **An option a question does not read is a usage error, not a no-op** (`cli.QUESTION_OPTIONS`).
  `report arbitrage --destination LHE` would otherwise print a table with SKT and ISB
  columns and an unfiltered verdict — a different question than the one asked, with no sign
  that anything was ignored. The verdict boundary is inclusive on the "no advantage" side:
  a spread exactly equal to `break_even_eur` reads `either`, because the drive has eaten
  the whole saving.
- **The analytics fixture is `tests/fixtures/analytics/fares.json`**, 21 hand-written
  `fare_observation` rows over two observation days, loaded by `conftest.seed_analytics_db`
  (fixtures `analytics_db_path` and `analytics_conn`). It is shaped so every F5 question has
  a hand-checkable answer: LHE/SKT spreads either side of the break-even, lead times both on
  and off the horizon grid, a departure in two calendar windows, wedding and Feb/March
  departures, five carriers with different durations. Expected values in the tests are
  literals worked out by hand — recomputing them from the fixture would only prove the test
  agrees with itself. Calendar tags come from the real engine, so a window moving in
  `calendar_engine` surfaces as a failing analytics test. S12 and S14 should seed from it too.

Decisions from S12 (2026-09-09):

- `analytics/explain.py` is one read: `explain(conn, observation, window_days=7)` returns a
  frozen `Explanation` (the fare row, `tags`, `events`, `window_days`) whose `.sentence`
  renders the same thing as one line. Same shape as `queries.py` — dataclasses, no writes,
  no re-running of the calendar or the severity heuristic. It composes `db.fetch_calendar_tags`
  and `db.fetch_news_events` rather than issuing its own SQL.
- **Both halves are anchored on the departure date, not on `observed_on`.** PRD F6 says
  "within ±7 days of the observation" and a fare row carries two dates; a window and an
  airspace closure are properties of the journey, not of the day the market was asked. It
  is also the axis the chart draws bands and pins on, so a tooltip lines up with the pins
  beside it instead of drifting by the booking horizon.
- **Tags are read from `calendar_tag`, not recomputed with `tags_for()`.** The engine would
  answer for dates `fd tag-dates` has not reached, and the chart shades its bands from the
  table, so an engine-computed explanation could name a window the chart does not draw. An
  explanation with no tags on a date that should have some is a true report that the
  pipeline has not tagged it yet.
- **±7 is a parameter, because the PRD and the design disagree**: F6 says ±7, the design's
  hover card says ±5 (`design-system.md` § Page anatomy). Both are display choices, so the
  caller passes one. `window_days=0` means same-day only; a negative one raises rather than
  quietly returning nothing.
- Events come back **most severe first, then nearest** (`dedupe_key` breaks the last tie),
  which is the order the sentence truncates from. The sentence names at most
  `MAX_EVENTS_IN_SENTENCE` (3) and counts the rest ("A, B, C and 2 more") so a tooltip stays
  one line; the structured list keeps them all for the drawer.
- `tags.label_for(tag)` (in `calendar_engine/tags.py`) is the one map from a stored tag to
  its dashboard label across both calendars. It **falls back to the tag itself** rather than
  raising: `calendar_tag` holds whatever the engine wrote on the day it ran, and a window
  retired since then must not turn a tooltip into a `KeyError`.
- `Explanation.tags` and `.events` are always lists. Nothing applying is an answer — the
  fare is off-peak and uneventful — not a missing value, and a renderer should never have to
  tell `None` from `[]`.

## The JSON contract (`dashboard.json`)

Shaped by what the design's component consumes (see `specs/ux/design-system.md` § Data
the page needs). Top level: `schema_version`, `generated_at`, `observed_on`, `carriers[]`,
`destinations[]`, `horizons[]`, `series[]` (per destination × horizon × carrier: `{date,
price_eur}` points), `bands[]`, `events[]`, `arbitrage`, `efficiency[]`, `seasonal_gauge`.
Written with `tmp + os.replace` so a reader never sees a partial file. The generated
schema is `specs/dashboard-data.schema.json`; see § Decisions from S14 for the details a
reader of the file needs.

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
  ad-hoc hex values. `specs/ux/routeradar/RouteRadar.dc.html` is a read-only pull from the
  Claude Design project and still carries the pre-S13 carrier hexes; where the two disagree
  the design-system file wins.
- **The carrier palette is computed, not chosen.** Its six hues are gated by the `dataviz`
  skill's checks and `tests/test_design_system.py` re-runs them against the spec's tokens,
  so changing a carrier colour by hand fails `scripts/check.sh`. See
  `specs/ux/design-system.md` § Carrier palette for the rule and § Decisions from S13 below
  for why the thresholds differ per role.

## Operational

- `flight-detective-pipeline.service` (oneshot, user unit under `~/.config/systemd/user/`,
  mirroring how `cloudgaming-panel.service` is run) runs `deploy/run-pipeline.sh`, which is
  `fd ingest`, then `fd tag-dates && fd news-ingest && fd export-site &&
  deploy/push-data.sh`. The ingest is deliberately outside the `&&` chain: its exit 3
  (partial) continues, 1 and 2 stop the run. See § Decisions from S09.
  `flight-detective-pipeline.timer` fires it daily at 06:30 Europe/Paris. The unit pins the
  absolute `uv` path because systemd does not source the shell profile.
- `deploy/push-data.sh` commits `data/site/dashboard.json` on `main` with the message
  `data: <observed_on>` and pushes with the deploy key. If the file is unchanged it exits 0
  without committing. It never touches anything else in the tree. `FD_REMOTE`, `FD_BRANCH`
  and `FD_DEPLOY_KEY` override the three defaults; see § Decisions from S16 for what it
  refuses to do and why.
- `deploy-site.yml` builds on that push and deploys to Pages; `ci.yml` runs the checks on
  every push and PR.
- Secrets: `SERPAPI_KEY` in `.env` (git-ignored), read by `config.py`. Never in code or
  specs. The site build has no secrets at all; the deploy key is on the box only.
- `data/site/dashboard.json` is the **one tracked file under `data/`**; `.gitignore`
  keeps the DuckDB file and everything else out.

## Decisions from S13 (2026-09-09)

- **All six carrier hues were replaced**, not just the ones the story expected. The
  original palette's collisions went further than the two literal ones: PIA's `#3fae6d` sat
  ΔE 4.4 from `--band-religious` and 3.3 from `--color-ok`, and Saudia's `#37a894` sat 2.4
  from `--band-religious`. "Keep PIA green, Qatar pink, Saudia teal" survives only as hue
  families; every value moved.
- **The carrier ↔ carrier gate is the `dataviz` skill's, unmodified** (all-pairs
  ΔE ≥ 8 protan/deutan, ≥ 15 normal vision, OKLCH L in 0.48–0.67, C ≥ 0.10, contrast ≥ 3:1
  on `--color-card`). All-pairs rather than the default adjacent list, because the fare
  chart overlaps six paths and the efficiency matrix is a scatter.
- **Carrier ↔ role thresholds are lower and differ by rendering channel** (15 for
  `--color-accent`, 10 for bands, severity and `--color-ok`). A uniform ΔE ≥ 15 gate is
  *achievable* — do not repeat the claim that it is not — but it buys nothing and costs two
  things: the best such set sits exactly on both dataviz floors (8.1 protan, 15.0 normal, vs
  the shipped 8.2 and 18.9), and it gives up Saudia's hue — the cyan arc is boxed in by
  `--band-religious` and `--band-french`, and only clears ΔE 15 past H ≈ 226, which is a
  cyan-blue rather than a teal (`--carrier-SV` is at H 212, where the ceiling is 12.8). Both
  of those are measured; the per-hue table is in `design-system.md`. So the
  binding constraint is brand fidelity plus margin, not feasibility. The channels justify the
  lower numbers — bands are 13–21 % alpha washes, severity pins carry a glyph — and the table
  is in `design-system.md` § The rule, by rendering channel.
- **Six carriers is the ceiling.** A seventh is not a palette change; it is "Other", or a
  facet. Any future carrier addition must re-run the validator before it ships.
- **Not changed, deliberately:** `--sev-med` and `--band-wedding` are still the same
  `#d6a63c`, and `--band-french` `#6f8fd6` is ΔE 0.5 from `--color-accent` under
  deuteranopia. Both are outside S13's scope (it owns the carrier hues) and neither is a
  data-identity channel — a gold pin carries `▲` and sits on the baseline, a band is a wash.
  Worth revisiting if S15 finds them confusable on the real page.

## Decisions from S14 (2026-09-09)

- `export/site.py` is Pydantic models plus one builder, `build_dashboard(conn, *,
  generated_at, horizons, break_even_eur)`, and `write_dashboard(data, path)`. The
  timestamp is a **parameter, not `now()`**, because the export window is anchored on it:
  the tests pin a day, and a replay produces that day's file. `export/schema.py` renders
  the JSON Schema in **serialization** mode and `fd export-schema` writes the committed
  copy; `tests/test_export.py` fails if the committed file has drifted from the model, so
  the site's generated TypeScript can never be a version behind.
- **The window reaches backwards as well as forwards**: `generated_at - 90 days` (GDELT's
  archive, so no pin can exist before it) to `generated_at + 365 days` (the range
  `fd tag-dates` tags, so no band names a window `calendar_tag` has no rows for). Anchoring
  the chart at today would have put every news pin off the left edge — GDELT only knows the
  recent past, while the fares are all in the future.
- **Three regions never come from the database**: `carriers[]`, `destinations[]` and
  `horizons[]` are reference tables in `site.py`, so the filter bar renders before the
  first ingest. `bands[]` is the same argument one step on: it is built from
  `tags_for_range`, not from `calendar_tag`, so the chart shades an empty plot. Building it
  from the engine also means a band is exactly what `fd tag-dates` would write — clipped at
  the window edges, and split into two bands when a window recurs inside the window
  (Ramadan drifts ~11 days a year), with no special case for either.
- **A carrier the palette has no colour for is left out of every region.** S13 fixed the
  palette at six and a seventh is a design change, not a row; the page cannot draw a line it
  has no colour for. The scope filters (`providers/filters.py`) do not filter by carrier, so
  a real ingest does return other codes — and dropping them from the chart while leaving
  them in the modules would put a spread on the arbitrage card that no line on the chart
  accounts for. `queries._scope` therefore takes a `carriers=` set, threaded through
  `fare_series`, `airport_arbitrage`, `wedding_premium` and `carrier_efficiency` (the four
  the export calls), and the export passes `CARRIER_CODES` to all four. `fd export-site`
  says how many series it wrote, which is where a silently missing carrier shows up.
- **Prices are integers of euros and durations are integers of minutes.** Pydantic
  serialises a `Decimal` to a JSON *string*, so a `Decimal` in the contract would make the
  site parse numbers out of strings. Band multipliers are the one float in the file: they
  are a display hint on a band chip, never an input to exact arithmetic.
- `arbitrage` is the **nearest departure on the latest observation day** that was priced
  into both Lahore and Sialkot — the snapshot question ("what would I pay to go"), not the
  cheapest the market ever showed. Its `spread_eur` keeps the analytics convention,
  `lhe_eur - skt_eur`, which is the opposite sign to the design's caption ("spread SKT −
  LHE"); one convention across the codebase beats matching a caption.
- `seasonal_gauge` **is** `queries.wedding_premium`: the design's "current average vs
  February baseline" is that query, so the gauge carries a `method` line saying so rather
  than leaving the mapping in the code.
- **`impact_score` is not exported.** The design derives it from severity (high 0.91, med
  0.64, low 0.22), so it would be a second copy of a field already in the file. `body` is
  null in v1 for a harder reason: GDELT's DOC 2.0 `artlist` carries titles and URLs, not
  article text. `impact_text` carries the ingest's own note (how many outlets, which
  keyword set the severity), which is not the fare-impact estimate the design's drawer
  labels it as — S15 relabels it.
- `analytics/queries.py` gained a sixth function, `fare_series`, for the chart's own shape.
  It is not an F5 question; it lives there because that is where reads of
  `fare_observation` live, and it buckets lead times with the same `_horizon_bucket_sql` as
  `lead_time_curve` so a run that slipped a day stays on its curve. Origins are pooled: the
  design's origin control is display-only.
- `calendar_engine`'s `Window` gained `short_label` (the uppercase band chip) and
  `tags.window_for(tag)` returns the whole window rather than just its label, so the export
  reads label, short label and kind from one place. `label_for` is unchanged and still
  falls back to the raw tag.

## Decisions from S15 (2026-09-09)

- **`web/` reads the JSON as an import, never a fetch.** `src/data.ts` imports the alias
  `@dashboard-data`, which `vite.config.ts` resolves at config time to
  `data/site/dashboard.json` if the box has written one and to `web/fixtures/dashboard.json`
  otherwise. The file ends up inside the hashed JS bundle, so a clean checkout builds
  without running the pipeline and the deployed page makes no request for data at all.
  `parseDashboard` checks `schema_version` before anything reads the file: the one drift the
  generated types cannot catch is a deployed bundle meeting a newer JSON.
- **`pnpm gen` uses the library API, not the `json2ts` CLI**, and strips the schema's
  `title`, `$id` and every property-level `title` first. Pydantic titles every property, and
  the generator turns each title into a named alias — including `export type Date = string`,
  which shadows the global. Stripping them leaves nine interfaces named after the `$defs`
  and a root named `DashboardData`.
- **The chart's x axis is the export window** (`generated_at` −90d to +365d, mirrored from
  `export/site.py` in `lib/select.ts`), not the extent of the data. Series, bands and events
  are all clipped to that window by the export, so the axis is stable: toggling a chip or a
  horizon does not rescale time under the reader, and the bands still span a year on a plot
  with two fares on it. Fares are drawn as a path *and* a dot per point, because a carrier
  with one observation has no line.
- **The efficiency scatter computes its axes**; the design hardcodes €480–€1100 and 7h–17h,
  which are its fake model's range, and a real dot outside that box lands on the card title.
- **`initialFilters` opens on a horizon that has fares** — 60d when it has any, else the
  first that does. The design defaults to 60d, but a young database has not filled every
  cell of the grid, and opening on the "no fares for this combination" message reads as a
  broken site rather than as a gap in one cell.
- **Empty states are three distinct kinds, not one string** (`lib/select.ts`
  `chartEmptyState`): `no-data-yet` is a gap in the data, `no-combination` and `no-carriers`
  are the reader's own filter. Each renders its own `data-testid`, which is how the tests
  assert that one is never substituted for the other. Attribution is *computed*, not assumed:
  when nothing is visible the selection is re-run with no chips hidden, and if that would
  have had fares the message blames the chips rather than the horizon. Hiding the one carrier
  that flies a cell is otherwise reported as missing data.
- **Under `ALL`, a collapsed point keeps the airport that won it** (`PricedPoint`). The
  hover card names the routing it is quoting, and § Carrier palette § Obligations makes that
  line load-bearing, so a point collapsed across ISB/LHE/SKT cannot be labelled with the
  filter's own value — it would print a route that was never priced.
- **Events are keyed by `date|source_url`, not by `source_url`.** `news_event`'s primary key
  is `dedupe_key`, which the contract does not carry; two rows sharing a URL are allowed, and
  a duplicate React key drops a pin and a feed row silently on real GDELT data only.
- **The hover card is positioned by a layout effect writing `style.left` on the node**,
  rather than through React state. Its placement depends on its own measured width and on
  the scroll container's visible rect (§ Phone behaviour), which only exist after it is in
  the DOM; routing that measurement back through state would re-render the whole chart on
  every pointer move, and `react-hooks/set-state-in-effect` fails the lint for it. Two
  coordinate spaces meet in `hoverCardPlacement` and they do not share an origin: the card is
  positioned inside `.plot`, which starts ~46px into the scroll content (`.plot-frame`'s 44px
  y-axis gutter plus `.scroll-wrap`'s padding). It takes a `plotOffset` for exactly that, and
  a clamp that ignores it is off by the gutter — which on a 360px phone is enough to put the
  card back off the screen it was clamped onto, while looking perfectly correct in a test
  that also assumes a shared origin.
- **Fonts are self-hosted** (`@fontsource-variable/*`) rather than linked from
  `fonts.googleapis.com` as the design does, so "no runtime fetch" holds for everything and
  not only for the data. The woff2 files come out content-hashed like every other asset.
- **`pnpm build` runs `scripts/check-dist.mjs`** after Vite: every emitted file but
  `index.html` must carry a content hash, and `index.html` must reference them under the
  configured base. A build that silently fell back to base `/` looks perfect locally and
  404s every asset on Pages, so it is checked rather than remembered.
- **`scripts/check.sh` runs the web checks by the same five script names CI calls**
  (`gen`, `lint`, `typecheck`, `test`, `build`), `gen` first so a lint never passes against
  yesterday's types. It fails loudly when `pnpm` is missing rather than skipping them: nvm
  lives in the shell profile, so a non-login shell has to source it.

## Decisions from S16 (2026-09-09)

- **`push-data.sh` refuses rather than guesses.** It runs unattended at 06:30 in a checkout
  a human also edits, so it stages one path and stops if anything else is already staged —
  committing on top of someone's staged work would publish it, and there is no safe reading
  of what they meant. It also stops if HEAD is not the branch it pushes: `git push origin
  main` from a checkout parked on another branch publishes every unrelated commit on it.
  Both refusals happen before the commit, along with the missing-key and missing-export
  checks, so a refusal never leaves a dangling commit for the next run to reason about.
- **A failed push keeps the commit.** The next run stacks its own on top and pushes both,
  so a night without a network costs nothing. The case this cannot repair is a remote that
  has moved on: a rejected non-fast-forward needs a human, and re-running will keep failing
  the same way until it gets one. Deliberately no auto-rebase — unattended history rewriting
  on the branch that feeds the deploy is worse than a loud stall.
- **`IdentitiesOnly=yes` is the half of the deploy key that matters.** Without it `ssh -i`
  is a preference: ssh still offers every key the agent holds, and the push authenticates
  as whoever happens to be logged in on the box. The key is scoped to this repo; the human's
  `~/.ssh/id_ed25519` is not, and it is also the key that reaches the TV.
- **Commits are authored by `flight-detective bot`** via `git -c user.name=…`, not a config
  write, so the checkout's own identity is untouched and `git log` still separates the timer's
  commits from a human's at a glance.
- **The Pages base path comes from the repository name at build time.** `deploy-site.yml`
  sets `VITE_BASE: /${{ github.event.repository.name }}/`, because Vite bakes the prefix into
  every asset URL and a project site is served from `/<repo>/` — the previous arrangement
  relied on `vite.config.ts`'s `/flight-detective/` default, which does not match this repo
  (`RouteRadar`) and would have deployed an `index.html` whose every asset 404s. The default
  in `vite.config.ts` stays as the local one for `pnpm dev`/`vite preview`.
- **The GitHub-side steps are not automatable and are not pretended to be.** Registering the
  deploy key with write access, and setting Pages' source to "GitHub Actions", are UI
  actions; `deploy/install.sh` prints both when the key is missing and README § Publishing
  records them. See S16's `### Blocked` note in the backlog for what that leaves unverified.
- **The push tests use a stub `ssh` that runs the remote command locally** against an
  on-disk bare repo, so the deploy-key wiring is asserted on a push that really succeeded,
  with no network and no key that exists anywhere. `tests/test_push_data.py` also hand-parses
  `deploy-site.yml`'s `paths:` list rather than adding a YAML dependency to assert on six
  lines we wrote.
