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
  mirroring how `cloudgaming-panel.service` is run) runs `deploy/run-pipeline.sh`, which is
  `fd ingest`, then `fd tag-dates && fd news-ingest && fd export-site &&
  deploy/push-data.sh`. The ingest is deliberately outside the `&&` chain: its exit 3
  (partial) continues, 1 and 2 stop the run. See § Decisions from S09.
  `flight-detective-pipeline.timer` fires it daily at 06:30 Europe/Paris. The unit pins the
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
