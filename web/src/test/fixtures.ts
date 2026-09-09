import raw from "../../fixtures/dashboard.json";
import type { DashboardData } from "../types";

/** The committed export, the same file `src/data.ts` falls back to. Every component test
 * drives the page from this or from a hand-made variant of it, never from live data. */
export const FIXTURE = raw as unknown as DashboardData;

/** `generated_at` in the fixture is pinned at 2026-09-09T06:35:00+02:00. These two clocks
 * sit either side of the 36 h staleness threshold. */
export const NOW_12H_LATER = new Date("2026-09-09T18:35:00+02:00");
export const NOW_40H_LATER = new Date("2026-09-10T22:35:00+02:00");

/** Before the first ingest: the reference regions are populated (they are tables in the
 * export, not queries) and the bands are there, but nothing was ever priced. */
export function beforeFirstIngest(): DashboardData {
  return {
    ...FIXTURE,
    observed_on: null,
    series: [],
    events: [],
    efficiency: [],
    arbitrage: null,
    seasonal_gauge: null,
  };
}

/** Fares exist, but the queries behind the three modules and the feed could not be
 * answered — a partial database rather than an empty one. */
export function withoutModules(): DashboardData {
  return { ...FIXTURE, events: [], efficiency: [], arbitrage: null, seasonal_gauge: null };
}

/** The news half of the pipeline has been dead for a week while the fares kept arriving.
 * `events` still carries the rows the last working run wrote — they are old, not wrong. */
export function newsStale(
  last_error: string | null = "cdg-strikes: timed out (+8 more)",
): DashboardData {
  return { ...FIXTURE, news_status: { last_success: "2026-09-02", last_error } };
}

/** News is working and there simply was nothing to report. The case the muted line must
 * stay out of, because it is the answer rather than a fault. */
export function quietWeek(): DashboardData {
  return { ...FIXTURE, events: [] };
}
