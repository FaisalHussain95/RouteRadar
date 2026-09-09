import type { Carrier, DashboardData, DestinationInfo, SeriesPoint } from "../types";

import { addDays, parseDay } from "./dates";

/** The design's fourth destination segment. It is computed here rather than exported,
 * because "compare" is a view of the three real destinations, not a fourth airport. */
export const ALL_DESTINATIONS = "ALL";

export interface Filters {
  destination: string;
  horizon: number;
  hiddenCarriers: ReadonlySet<string>;
}

/** A series point that remembers which airport it is for. Under `ALL` the three
 * destinations are collapsed to the cheapest per day, and the hover card names the route it
 * is quoting — so the winning airport has to survive the collapse or the card prints a
 * routing that was never priced. */
export interface PricedPoint extends SeriesPoint {
  destination: string;
}

export interface CarrierPoints {
  carrier: Carrier;
  points: PricedPoint[];
}

export function destinationOptions(data: DashboardData): DestinationInfo[] {
  return [...data.destinations, { code: ALL_DESTINATIONS, label: "All · compare" }];
}

export function destinationLabel(data: DashboardData, code: string): string {
  if (code === ALL_DESTINATIONS) return "All destinations";
  return data.destinations.find((d) => d.code === code)?.label ?? code;
}

/** Has the pipeline ever stored a fare? `series` is empty before the first ingest, and a
 * run that stored nothing leaves the array there but every list of points empty. */
export function hasAnyFares(data: DashboardData): boolean {
  return data.series.some((s) => s.points.length > 0);
}

/** How far the file reaches either side of `generated_at`, mirroring `export/site.py`'s
 * HISTORY and FORWARD. The chart's x axis is that window rather than the extent of the
 * data, so the axis does not shift when a chip is toggled and the bands still span a year
 * on a plot with two fares on it.
 *
 * A TypeScript constant cannot import a Python `timedelta`, so these two are a hand-copy —
 * and `HISTORY` tracks GDELT's archive length, which is not ours to fix.
 * `tests/test_export.py::test_the_site_axis_uses_the_same_window_as_the_export` reads these
 * two lines and fails if they drift. Change them together. */
export const HISTORY_DAYS = 90;
export const FORWARD_DAYS = 365;

export function exportWindow(data: DashboardData): { start: number; end: number } {
  const anchor = parseDay(data.generated_at);
  return { start: addDays(anchor, -HISTORY_DAYS), end: addDays(anchor, FORWARD_DAYS) };
}

/** The lines to draw, in the order `carriers[]` lists them — never in price order, so a
 * carrier keeps its colour and its place in the legend whatever the fares do.
 *
 * For `ALL` the three destinations are collapsed to the cheapest of them per day, which is
 * the comparison the segment promises: what this carrier would cost to fly to the region
 * on that date, whichever northern airport that turns out to be. */
export function seriesFor(data: DashboardData, filters: Filters): CarrierPoints[] {
  const wanted = data.series.filter(
    (s) =>
      s.horizon_days === filters.horizon &&
      (filters.destination === ALL_DESTINATIONS || s.destination === filters.destination),
  );
  return data.carriers
    .filter((c) => !filters.hiddenCarriers.has(c.code))
    .map((carrier) => {
      const cheapest = new Map<string, PricedPoint>();
      for (const s of wanted) {
        if (s.carrier !== carrier.code) continue;
        for (const p of s.points) {
          const seen = cheapest.get(p.departure_date);
          if (seen === undefined || p.price_eur < seen.price_eur) {
            cheapest.set(p.departure_date, { ...p, destination: s.destination });
          }
        }
      }
      const points = [...cheapest.values()].sort((a, b) =>
        a.departure_date.localeCompare(b.departure_date),
      );
      return { carrier, points };
    });
}

/** The dates the hover can snap to: every day any visible line has a fare for. */
export function hoverDates(series: CarrierPoints[]): number[] {
  const days = new Set<number>();
  for (const s of series) for (const p of s.points) days.add(parseDay(p.departure_date));
  return [...days].sort((a, b) => a - b);
}

export interface ChartEmptyState {
  /** `no-data-yet` is a gap in the data; the other two are the reader's own filter. The
   * page must not use one message for the other, so they are different kinds and not one
   * "nothing to show" string. */
  kind: "no-data-yet" | "no-carriers" | "no-combination";
  message: string;
}

export function chartEmptyState(
  data: DashboardData,
  filters: Filters,
  series: CarrierPoints[],
): ChartEmptyState | null {
  if (!hasAnyFares(data)) {
    return {
      kind: "no-data-yet",
      message: "No fares ingested yet · the first run is scheduled for 06:30 CET",
    };
  }
  if (data.carriers.every((c) => filters.hiddenCarriers.has(c.code))) {
    return { kind: "no-carriers", message: "All carriers hidden" };
  }
  if (series.some((s) => s.points.length > 0)) return null;

  // Nothing visible. Whose doing? If a *hidden* carrier has fares in this same cell then the
  // reader hid the only line there was, and telling them to try another horizon would blame
  // the data for their own chip.
  const where = `${destinationLabel(data, filters.destination)} at ${filters.horizon}d`;
  const hiddenHaveFares = seriesFor(data, { ...filters, hiddenCarriers: new Set() }).some(
    (s) => s.points.length > 0,
  );
  return hiddenHaveFares
    ? { kind: "no-carriers", message: `No fares for ${where} from the carriers you have shown` }
    : { kind: "no-combination", message: `No fares for ${where} — try another horizon` };
}
