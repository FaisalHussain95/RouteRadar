import type { Event } from "../types";

/** A stable React key for an event row, pin or hover line.
 *
 * Not `source_url` alone: `news_event`'s primary key is `dedupe_key` and the export sorts by
 * `(event_date, dedupe_key)` precisely because two rows can share a day — and the same story
 * re-filed under another category or date shares a URL just as easily. A duplicate key drops
 * a pin and a feed row silently, and only ever on real GDELT data. `dedupe_key` itself is not
 * in the contract, so the date and the URL together are the closest thing the file carries. */
export function eventKey(event: Event): string {
  return `${event.date}|${event.source_url}`;
}
