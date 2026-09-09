/** Dates in the contract are plain `YYYY-MM-DD` days and the timestamps carry their own
 * offset, so everything here works in UTC milliseconds and never in the reader's local
 * timezone: a chart whose bands moved a day depending on where the browser is would be a
 * bug that only shows up abroad. */

export const DAY_MS = 86_400_000;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** `YYYY-MM-DD` (or the date half of a timestamp) as UTC milliseconds. */
export function parseDay(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

/** `9 Sep 2026`, the design's `fmtDate`. */
export function formatDay(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** `Sep`, and `Jan 27` at a year boundary, as the design's x axis labels them. */
export function formatMonth(ms: number): string {
  const d = new Date(ms);
  const month = MONTHS[d.getUTCMonth()];
  return d.getUTCMonth() === 0 ? `${month} ${String(d.getUTCFullYear()).slice(2)}` : month;
}

/** The wall-clock time inside a timestamp, `06:35`, read off the string rather than off a
 * `Date`. The export writes it in Europe/Paris and the header labels it CET; converting it
 * into the browser's zone would relabel a Paris time as something else. */
export function clockOf(timestamp: string): string {
  return timestamp.slice(11, 16);
}

export function addDays(ms: number, days: number): number {
  return ms + days * DAY_MS;
}
