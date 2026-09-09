import type { NewsStatus } from "../types";

import { DAY_MS, formatDay, parseDay } from "./dates";

/** Hours after `generated_at` at which the page stops claiming the data is current.
 *
 * 36, not 24: the pipeline timer fires at 06:30 daily, so a run that starts late or an
 * ingest that takes an hour would trip a 24 h threshold every time it happened and the
 * banner would stop meaning anything. 36 h is "a whole day was missed". */
export const STALE_AFTER_HOURS = 36;

export interface Staleness {
  stale: boolean;
  ageHours: number;
  /** Whole days, floored, never less than 1 — the banner only appears past 36 h. */
  ageDays: number;
  /** The banner's line, or null while the data is fresh. */
  message: string | null;
}

export function staleness(generatedAt: string, now: Date): Staleness {
  const ageHours = (now.getTime() - Date.parse(generatedAt)) / 3_600_000;
  const stale = ageHours > STALE_AFTER_HOURS;
  const ageDays = Math.max(1, Math.floor(ageHours / 24));
  return {
    stale,
    ageHours,
    ageDays,
    message: stale
      ? `Data is ${ageDays} ${ageDays === 1 ? "day" : "days"} old — ` +
        `the daily ingest has not run since ${formatDay(parseDay(generatedAt))}`
      : null,
  };
}

/** Days without a successful news run after which the feed stops reading as a quiet week.
 *
 * 2, and measured against `generated_at` rather than the reader's clock. Against the clock
 * it would double up with the banner above — a file that is simply old is already said
 * once — whereas the interesting case is the one this signal exists for: fares kept
 * arriving and news did not. Two days of silence is one bad night plus the retry that
 * should have cleared it; three is a pattern worth a line on the page. */
export const NEWS_STALE_AFTER_DAYS = 2;

export interface NewsFreshness {
  /** The muted line above the feed, or null while the news half is working. Unlike
   * `Staleness`, there is no separate `stale` flag: the header reads that one for its
   * status dot as well as its banner, whereas nothing here asks the question twice. */
  message: string | null;
  /** The failing run's own one-liner, shown under the message. Null when the run log has
   * no failure to blame — news that simply stopped being written looks like this. */
  detail: string | null;
}

/** Is the feed's emptiness news, or is it a broken pipeline?
 *
 * `events` is empty in both cases (see `export/site.py` § What is data), so this reads
 * `news_status`, which comes from the `ingest_run` log rather than from the row count. */
export function newsFreshness(status: NewsStatus, generatedAt: string): NewsFreshness {
  const detail = status.last_error;
  if (status.last_success === null) {
    // Never succeeded. With nothing failed either the news step has simply never run, which
    // is a fresh database — the page already says that once, as "waiting for the first
    // ingest", and a second sentence about it would be noise.
    return detail === null
      ? { message: null, detail: null }
      : { message: "News unavailable — no run has succeeded yet", detail };
  }
  const lastSuccess = parseDay(status.last_success);
  const ageDays = Math.round((parseDay(generatedAt) - lastSuccess) / DAY_MS);
  if (ageDays <= NEWS_STALE_AFTER_DAYS) return { message: null, detail: null };
  return { message: `News unavailable since ${formatDay(lastSuccess)}`, detail };
}
