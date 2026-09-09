import type { Band, Event } from "../types";

import { DAY_MS } from "./dates";
import { carrierColor, duration, routeOf } from "./format";
import type { CarrierPoints } from "./select";

/** The design's hover card pairs events within ±5 days, not the ±7 the PRD's explanation
 * layer uses: the card is a glance at one date, not the full explanation. */
export const HOVER_EVENT_WINDOW_DAYS = 5;

export interface HoverContent {
  day: number;
  bands: Band[];
  rows: { code: string; name: string; color: string; route: string; dur: string; fare: number }[];
  events: Event[];
}

export function hoverContent(args: {
  day: number;
  series: CarrierPoints[];
  bands: Band[];
  events: Event[];
  parse: (iso: string) => number;
}): HoverContent {
  const { day, series, bands, events, parse } = args;
  const rows = series
    .flatMap((s) => {
      const point = s.points.find((p) => parse(p.departure_date) === day);
      if (point === undefined) return [];
      return [
        {
          code: s.carrier.code,
          name: s.carrier.name,
          color: carrierColor(s.carrier),
          route: routeOf(s.carrier, point.destination),
          dur: duration(s.carrier.typical_transit_minutes),
          fare: point.price_eur,
        },
      ];
    })
    .sort((a, b) => a.fare - b.fare);
  return {
    day,
    bands: bands.filter((b) => parse(b.from) <= day && day <= parse(b.to)),
    rows,
    events: events.filter(
      (e) => Math.abs(day - parse(e.date)) <= HOVER_EVENT_WINDOW_DAYS * DAY_MS,
    ),
  };
}

