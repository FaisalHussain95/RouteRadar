# PRD — Flight Detective

Source: `specs/brainstorming.md` (2026-09-09). This document is the product contract the
backlog derives from. Change this first, then the backlog, never the other way round.

## Problem

Fares from Paris to northern Pakistan swing by 1.5–2x across the year and the reasons are
scattered: Desi wedding season, Ramadan and the two Eids on a drifting Hijri calendar,
French school holidays, Hajj seat rationing at Gulf hubs, plus one-off shocks (airspace
closures, PIA regulatory bans, strikes). Nobody tracking these routes today sees the price
*and* the reason in one place, so booking decisions are guesswork.

## Users

- **Primary:** a household in the Paris area booking 2–6 seats a year to ISB/LHE/SKT,
  wanting to know *when* to book and *which airport* to fly into.
- **Secondary:** the same person as an analyst, backtesting how past events moved fares.

## Goals

1. Record the lowest practical fare per carrier, per departure date, every day, for a
   rolling set of departure horizons.
2. Tag every departure date with the cultural and calendar windows that apply to it.
3. Capture exogenous events that plausibly moved fares, with a date and a severity.
4. Answer the four research questions below with charts a non-analyst can read.

## Non-goals (v1)

- Booking or price alerts. Read-only intelligence.
- Multi-ticket / self-transfer itineraries. Only single-ticket, max 1 stop.
- Business or first class. Economy only.
- Airports beyond CDG/ORY → ISB/LHE/SKT.
- Predictive ML. v1 is descriptive; multipliers in the calendar engine are hand-set.

## Scope rules (hard constraints applied at ingestion)

| Rule | Value |
|---|---|
| Origins | CDG, ORY |
| Destinations | ISB, LHE, SKT |
| Stops | 0 or 1 |
| Layover | ≤ 7 h |
| Total duration | ≤ 15 h |
| Cabin | Economy |
| Carriers of interest | PIA, Gulf Air, Qatar Airways, Emirates/flydubai, Turkish, Saudia |

Itineraries outside the rules are dropped before storage, and the drop is counted so a
provider change that silently returns nothing usable is visible.

## Functional requirements

- **F1 Fare ingestion.** Daily job queries departure dates at 14/30/60/90/120/180 days
  out for every origin/destination pair and stores one row per (observation date,
  departure date, route, carrier, itinerary) with price in EUR. Idempotent: re-running a
  day upserts rather than duplicates.
- **F2 Provider abstraction.** Fare data comes through a `FareProvider` interface. A
  fixture-backed fake exists for tests and offline development. The real provider is
  SerpApi Google Flights; swapping to Apify or another API must not touch the pipeline.
- **F3 Calendar engine.** For any Gregorian date, return the set of windows that apply
  (see brainstorming §2) with an expected multiplier range. Hijri windows are computed
  from `hijri-converter`, not hard-coded per year. French holidays are Zone C.
- **F4 Event ingestion.** Pull disruption news from GDELT with the taxonomy in
  brainstorming §3; dedupe per incident; store date, headline, source, category, severity.
- **F5 Analytics.** SQL over DuckDB producing:
  - lowest-fare curve per carrier per departure month,
  - LHE vs SKT arbitrage spread and ground-transport break-even,
  - lead-time curve (price vs days-before-departure) per seasonal band,
  - wedding-window premium vs Feb/Mar baseline,
  - carrier efficiency index (price vs total duration).
- **F6 Explanation.** For any fare row, list the calendar tags and any events within
  ±7 days of the observation, so a chart tooltip can say *why*.
- **F7 Dashboard.** A single-page dashboard (design: `specs/ux/routeradar/`) with fare
  curves, shaded cultural windows, event markers and an event feed. Usable on a phone.
- **F8 Static, cacheable, zero-downtime.** The dashboard is a static site built from a
  JSON export of the pipeline. No API and no runtime fetch: everything is cacheable and
  deployable as a directory. The daily pipeline writes the JSON; the site is rebuilt only
  after a successful export and published by an atomic swap, so a visitor never sees a
  missing page or a half-updated one, and a failed run leaves yesterday's site up.

## Success metrics

- 90 days of uninterrupted daily observations with < 2 % missing (route, horizon) cells.
- Every departure date in the DB carries calendar tags; Hijri tags match published Eid
  dates for 2025–2027 within ±1 day.
- Each of the five F5 questions has a chart and a one-paragraph reading of the result.

## Constraints and risks

- SerpApi has a paid quota. Requests per day are bounded: 2 origins × 3 destinations ×
  6 horizons = 36 queries/day, well under the free tier if one exists, otherwise the
  cheapest paid plan. Provider calls are never made from tests.
- The host is the `cloudgaming` box (Bazzite, immutable). Scheduling is a systemd user
  timer; the DuckDB file lives in the repo's `data/` directory (git-ignored). The site is
  built by GitHub Actions and served by GitHub Pages; its build output must work unchanged
  on any static host.
- Google Flights results vary by IP/locale; the provider must pin `fr`/`EUR`.

## Open questions (do not block v1)

- Whether ORY has any qualifying itineraries at all; drop it if 30 days show zero rows.
- Whether Event Registry is worth paying for on top of GDELT.
