import { describe, expect, it } from "vitest";

import {
  bandExtent,
  domainOf,
  hoverCardPlacement,
  monthTicks,
  nearestIndex,
  priceDomain,
  svgPath,
  xPct,
  yTickValues,
} from "./chart";
import { parseDay } from "./dates";

describe("priceDomain", () => {
  it("rounds out to the 50s either side, as the design does", () => {
    expect(priceDomain([612, 738])).toEqual([550, 800]);
  });

  it("gives an empty plot a plausible range rather than a degenerate one", () => {
    expect(priceDomain([])).toEqual([500, 1000]);
  });

  it("never collapses to a zero-height axis on a single fare", () => {
    const [lo, hi] = priceDomain([600]);
    expect(hi).toBeGreaterThan(lo);
  });
});

describe("yTickValues", () => {
  it("steps in 50s and includes both ends", () => {
    expect(yTickValues(550, 800)).toEqual([550, 600, 650, 700, 750, 800]);
  });
});

describe("hoverCardPlacement", () => {
  const plot = { plotWidth: 1000, cardWidth: 290, scrollLeft: 0, viewportWidth: 1000 };

  it("sits to the right of the hairline in the first 58 % of the plot", () => {
    expect(hoverCardPlacement({ ...plot, hairlinePx: 100 })).toEqual({ left: 112, flipped: false });
  });

  it("flips to the left of the hairline past 58 %", () => {
    expect(hoverCardPlacement({ ...plot, hairlinePx: 800 })).toEqual({ left: 498, flipped: true });
  });

  it("clamps into the scroll container's visible box, not the 760px plot", () => {
    // A phone: 360px of window showing a 760px plot scrolled 400px in. The design's
    // `left: <pct>%` anchor would put this card 200px off the left of the visible box.
    const phone = { plotWidth: 760, cardWidth: 250, scrollLeft: 400, viewportWidth: 360 };
    expect(hoverCardPlacement({ ...phone, hairlinePx: 200 }).left).toBe(408);
    // …and at the other edge it would hang 210px past the right of it.
    expect(hoverCardPlacement({ ...phone, scrollLeft: 0, hairlinePx: 300 }).left).toBe(102);
    // Inside the visible box it is left where the flip put it.
    expect(hoverCardPlacement({ ...phone, hairlinePx: 740 }).left).toBe(478);
  });

  it("accounts for the plot's gutter inside the scroll container", () => {
    // .plot-frame's 44px y-axis gutter plus .scroll-wrap's 2px padding: the plot starts 46px
    // into the scroll content, so a clamp computed as if it started at 0 lands the card 46px
    // too far right — on a 360px phone, back off the screen it was clamped onto.
    const phone = {
      plotWidth: 760,
      cardWidth: 250,
      scrollLeft: 0,
      viewportWidth: 360,
      plotOffset: 46,
    };
    // Right edge of the visible box, in plot pixels: 0 + 360 − 8 − 250 − 46 = 56.
    expect(hoverCardPlacement({ ...phone, hairlinePx: 300 }).left).toBe(56);
    // Left edge: 0 + 8 − 46 = −38, i.e. 8px inside the container even though that is left of
    // the plot's own origin.
    expect(hoverCardPlacement({ ...phone, scrollLeft: 400, hairlinePx: 200 }).left).toBe(362);
  });

  it("falls back to the left margin when the card cannot fit at all", () => {
    const tiny = { plotWidth: 760, cardWidth: 400, scrollLeft: 100, viewportWidth: 300 };
    expect(hoverCardPlacement({ ...tiny, hairlinePx: 200 }).left).toBe(108);
  });
});

describe("bandExtent", () => {
  const start = parseDay("2026-09-01");
  const end = parseDay("2027-09-01");

  it("clips a band that starts before the window", () => {
    const extent = bandExtent("2026-07-01", "2026-09-15", start, end);
    expect(extent?.left).toBe(0);
    expect(extent?.width).toBeGreaterThan(0);
  });

  it("drops a band that falls entirely outside it", () => {
    expect(bandExtent("2026-06-01", "2026-08-01", start, end)).toBeNull();
  });

  it("covers the whole of its last day, which the contract states inclusively", () => {
    const oneDay = bandExtent("2026-09-01", "2026-09-01", start, end);
    expect(oneDay?.width).toBeGreaterThan(0);
  });
});

describe("monthTicks", () => {
  it("puts one tick on each month start inside the window", () => {
    const ticks = monthTicks(parseDay("2026-09-09"), parseDay("2026-12-09"), () => "");
    expect(ticks.map((t) => new Date(t.ms).toISOString().slice(0, 10))).toEqual([
      "2026-10-01",
      "2026-11-01",
      "2026-12-01",
    ]);
  });
});

describe("nearestIndex", () => {
  const start = parseDay("2026-09-01");
  const end = parseDay("2026-09-11");
  const days = ["2026-09-02", "2026-09-06", "2026-09-10"].map(parseDay);

  it("snaps to the nearest day that actually has a fare", () => {
    expect(nearestIndex(0, days, start, end)).toBe(0);
    expect(nearestIndex(0.55, days, start, end)).toBe(1);
    expect(nearestIndex(1, days, start, end)).toBe(2);
  });
});

describe("svgPath", () => {
  it("moves to the first point and lines to the rest", () => {
    const d = svgPath(
      [
        { departure_date: "2026-09-01", price_eur: 600 },
        { departure_date: "2026-09-11", price_eur: 700 },
      ],
      parseDay("2026-09-01"),
      parseDay("2026-09-11"),
      600,
      700,
    );
    expect(d).toBe("M0.0 340.0 L1000.0 0.0");
  });
});

describe("xPct", () => {
  it("is 0 at the window start and 100 at its end", () => {
    const start = parseDay("2026-09-01");
    const end = parseDay("2027-09-01");
    expect(xPct(start, start, end)).toBe(0);
    expect(xPct(end, start, end)).toBe(100);
  });
});

describe("domainOf", () => {
  it("pads either side of the data instead of using the design's hardcoded axis", () => {
    expect(domainOf([600, 900], 40)).toEqual([560, 940]);
  });

  it("still spans something when every value is identical", () => {
    const [lo, hi] = domainOf([700], 40);
    expect(hi).toBeGreaterThan(lo);
  });
});
