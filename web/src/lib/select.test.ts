import { describe, expect, it } from "vitest";

import { FIXTURE } from "../test/fixtures";
import type { DashboardData } from "../types";

import { parseDay } from "./dates";
import {
  ALL_DESTINATIONS,
  chartEmptyState,
  destinationLabel,
  exportWindow,
  hasAnyFares,
  hoverDates,
  seriesFor,
  type Filters,
} from "./select";

const NONE = new Set<string>();

function filters(over: Partial<Filters> = {}): Filters {
  return { destination: "ISB", horizon: 14, hiddenCarriers: NONE, ...over };
}

describe("seriesFor", () => {
  it("keeps one entry per carrier, in the order the export lists them", () => {
    const series = seriesFor(FIXTURE, filters());
    expect(series.map((s) => s.carrier.code)).toEqual(FIXTURE.carriers.map((c) => c.code));
  });

  it("selects on both destination and horizon", () => {
    const isb14 = seriesFor(FIXTURE, filters()).filter((s) => s.points.length > 0);
    expect(isb14.map((s) => s.carrier.code).sort()).toEqual(["PK", "QR"]);
    const lhe14 = seriesFor(FIXTURE, filters({ destination: "LHE" })).filter(
      (s) => s.points.length > 0,
    );
    expect(lhe14.map((s) => s.carrier.code)).toEqual(["GF"]);
  });

  it("drops a hidden carrier rather than repainting the survivors", () => {
    const series = seriesFor(FIXTURE, filters({ hiddenCarriers: new Set(["PK"]) }));
    expect(series.map((s) => s.carrier.code)).not.toContain("PK");
    expect(series.find((s) => s.carrier.code === "QR")?.carrier.color).toBe(
      FIXTURE.carriers.find((c) => c.code === "QR")?.color,
    );
  });

  it("collapses the three destinations to the cheapest of them under ALL", () => {
    const data: DashboardData = {
      ...FIXTURE,
      series: [
        {
          destination: "LHE",
          horizon_days: 14,
          carrier: "GF",
          points: [{ departure_date: "2026-10-01", price_eur: 700 }],
        },
        {
          destination: "SKT",
          horizon_days: 14,
          carrier: "GF",
          points: [{ departure_date: "2026-10-01", price_eur: 640 }],
        },
      ],
    };
    const gf = seriesFor(data, filters({ destination: ALL_DESTINATIONS })).find(
      (s) => s.carrier.code === "GF",
    );
    expect(gf?.points).toEqual([
      { departure_date: "2026-10-01", price_eur: 640, destination: "SKT" },
    ]);
  });

  it("sorts the points by departure date", () => {
    const data: DashboardData = {
      ...FIXTURE,
      series: [
        {
          destination: "ISB",
          horizon_days: 14,
          carrier: "PK",
          points: [
            { departure_date: "2026-11-01", price_eur: 700 },
            { departure_date: "2026-10-01", price_eur: 600 },
          ],
        },
      ],
    };
    const pk = seriesFor(data, filters()).find((s) => s.carrier.code === "PK");
    expect(pk?.points.map((p) => p.departure_date)).toEqual(["2026-10-01", "2026-11-01"]);
  });
});

describe("hoverDates", () => {
  it("is the sorted union of every visible line's departure days", () => {
    const days = hoverDates([
      {
        carrier: FIXTURE.carriers[0],
        points: [
          { departure_date: "2026-10-05", price_eur: 1, destination: "ISB" },
          { departure_date: "2026-10-01", price_eur: 1, destination: "ISB" },
        ],
      },
      {
        carrier: FIXTURE.carriers[1],
        points: [{ departure_date: "2026-10-05", price_eur: 1, destination: "ISB" }],
      },
    ]);
    expect(days).toEqual([parseDay("2026-10-01"), parseDay("2026-10-05")]);
  });
});

describe("exportWindow", () => {
  it("is generated_at −90d to +365d, the range the file is clipped to", () => {
    const { start, end } = exportWindow(FIXTURE);
    expect(new Date(start).toISOString().slice(0, 10)).toBe("2026-06-11");
    expect(new Date(end).toISOString().slice(0, 10)).toBe("2027-09-09");
  });
});

describe("hasAnyFares", () => {
  it("is false before the first ingest and false for a run that stored nothing", () => {
    expect(hasAnyFares({ ...FIXTURE, series: [] })).toBe(false);
    expect(
      hasAnyFares({
        ...FIXTURE,
        series: [{ destination: "ISB", horizon_days: 14, carrier: "PK", points: [] }],
      }),
    ).toBe(false);
    expect(hasAnyFares(FIXTURE)).toBe(true);
  });
});

describe("chartEmptyState", () => {
  it("says nothing when there are fares to draw", () => {
    expect(chartEmptyState(FIXTURE, filters(), seriesFor(FIXTURE, filters()))).toBeNull();
  });

  it("distinguishes an empty database from an empty filter", () => {
    const empty = { ...FIXTURE, series: [] };
    expect(chartEmptyState(empty, filters(), seriesFor(empty, filters()))?.kind).toBe(
      "no-data-yet",
    );

    const combination = filters({ horizon: 60 });
    expect(chartEmptyState(FIXTURE, combination, seriesFor(FIXTURE, combination))).toEqual({
      kind: "no-combination",
      message: "No fares for Islamabad at 60d — try another horizon",
    });
  });

  it("blames the chips when the reader hid the only carrier flying that cell", () => {
    // Gulf Air is the only carrier with LHE fares at 14d in the fixture. Hiding it must not
    // produce "try another horizon": the horizon has fares, they are just not shown.
    const f = filters({ destination: "LHE", hiddenCarriers: new Set(["GF"]) });
    expect(chartEmptyState(FIXTURE, f, seriesFor(FIXTURE, f))).toEqual({
      kind: "no-carriers",
      message: "No fares for Lahore at 14d from the carriers you have shown",
    });
  });

  it("blames the reader's own chips when every carrier is off", () => {
    const hidden = filters({ hiddenCarriers: new Set(FIXTURE.carriers.map((c) => c.code)) });
    expect(chartEmptyState(FIXTURE, hidden, seriesFor(FIXTURE, hidden))).toEqual({
      kind: "no-carriers",
      message: "All carriers hidden",
    });
  });
});

describe("destinationLabel", () => {
  it("names the city, so a message can say Lahore rather than LHE", () => {
    expect(destinationLabel(FIXTURE, "LHE")).toBe("Lahore");
    expect(destinationLabel(FIXTURE, ALL_DESTINATIONS)).toBe("All destinations");
  });
});
