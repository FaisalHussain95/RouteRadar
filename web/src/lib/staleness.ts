import { formatDay, parseDay } from "./dates";

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
