import { describe, expect, it } from "vitest";

import { FIXTURE } from "../test/fixtures";
import type { Band, Event } from "../types";

import { parseDay } from "./dates";
import { hoverContent } from "./hover";

const DAY = "2026-10-01";

const BANDS: Band[] = [
  {
    tag: "wedding_rush",
    label: "Desi wedding season",
    short_label: "Wedding peak",
    kind: "wedding",
    from: "2026-11-15",
    to: "2027-01-15",
    multiplier_low: 1.4,
    multiplier_high: 1.8,
  },
  {
    tag: "french_toussaint",
    label: "French Toussaint",
    short_label: "FR school holiday",
    kind: "french",
    from: "2026-09-20",
    to: "2026-10-05",
    multiplier_low: 1.05,
    multiplier_high: 1.15,
  },
];

function event(date: string, url: string): Event {
  return {
    date,
    severity: "med",
    headline: `something on ${date}`,
    source: "example.com",
    source_url: url,
    impact_text: null,
    body: null,
  };
}

function content(events: Event[] = []) {
  const [pk, qr] = FIXTURE.carriers;
  return hoverContent({
    day: parseDay(DAY),
    series: [
      { carrier: pk, points: [{ departure_date: DAY, price_eur: 812, destination: "LHE" }] },
      { carrier: qr, points: [{ departure_date: DAY, price_eur: 655, destination: "LHE" }] },
    ],
    bands: BANDS,
    events,
    parse: parseDay,
  });
}

describe("hoverContent", () => {
  it("names the airport each point was actually priced for, not the filter", () => {
    const [pk] = FIXTURE.carriers;
    const mixed = hoverContent({
      day: parseDay(DAY),
      series: [
        {
          carrier: pk,
          points: [{ departure_date: DAY, price_eur: 600, destination: "SKT" }],
        },
      ],
      bands: [],
      events: [],
      parse: parseDay,
    });
    expect(mixed.rows[0].route).toBe("CDG-SKT");
  });

  it("lists the carriers cheapest first, with route and duration", () => {
    const rows = content().rows;
    expect(rows.map((r) => r.code)).toEqual(["QR", "PK"]);
    expect(rows[0].route).toBe("CDG-DOH-LHE");
    expect(rows[1].route).toBe("CDG-LHE");
    expect(rows[1].dur).toBe("8h 06m");
  });

  it("keeps only the calendar windows covering that day", () => {
    expect(content().bands.map((b) => b.tag)).toEqual(["french_toussaint"]);
  });

  it("pairs events within ±5 days, the design's window rather than the PRD's ±7", () => {
    const events = [
      event("2026-09-26", "a"),
      event("2026-10-06", "b"),
      event("2026-10-07", "c"),
      event("2026-09-25", "d"),
    ];
    expect(content(events).events.map((e) => e.source_url)).toEqual(["a", "b"]);
  });

  it("returns empty lists rather than nothing when no line has a fare that day", () => {
    const empty = hoverContent({
      day: parseDay("2027-01-01"),
      series: [],
      bands: BANDS,
      events: [],
      parse: parseDay,
    });
    expect(empty.rows).toEqual([]);
    expect(empty.events).toEqual([]);
    expect(empty.bands.map((b) => b.tag)).toEqual(["wedding_rush"]);
  });
});
