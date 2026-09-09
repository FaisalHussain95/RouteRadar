import type { SeriesPoint } from "../types";

import { DAY_MS, parseDay } from "./dates";

/** The plot's own coordinate system. The SVG is drawn at this size and stretched to the
 * card with `preserveAspectRatio="none"`, exactly as the design does, so every path is
 * computed once and the browser handles the resize. Strokes are `non-scaling-stroke` so
 * they stay 1.6px however far the box stretches. */
export const PLOT_W = 1000;
export const PLOT_H = 340;

/** The design's rounding: drop to the 50 below the cheapest fare (with 60 of headroom) and
 * rise to the 50 above the dearest, so the axis labels are round numbers and the curves
 * never touch the frame. An empty plot gets a plausible range rather than a degenerate one. */
export function priceDomain(prices: number[]): [number, number] {
  if (prices.length === 0) return [500, 1000];
  const lo = Math.floor((Math.min(...prices) - 60) / 50) * 50;
  const hi = Math.ceil((Math.max(...prices) + 40) / 50) * 50;
  return [lo, hi === lo ? lo + 50 : hi];
}

export function yTickValues(lo: number, hi: number): number[] {
  const step = Math.round((hi - lo) / 5 / 50) * 50 || 50;
  const ticks: number[] = [];
  for (let v = lo; v <= hi; v += step) ticks.push(v);
  return ticks;
}

/** Position along the x axis as a percentage of the plot, from a day in the export window. */
export function xPct(ms: number, start: number, end: number): number {
  return ((ms - start) / (end - start)) * 100;
}

export function yPct(price: number, lo: number, hi: number): number {
  return (1 - (price - lo) / (hi - lo)) * 100;
}

/** One `<path d>` for a carrier. A single-point series has no line, which is why the chart
 * also draws a dot per point: early in a route's history that is all there is. */
export function svgPath(
  points: SeriesPoint[],
  start: number,
  end: number,
  lo: number,
  hi: number,
): string {
  return points
    .map((p, i) => {
      const x = (xPct(parseDay(p.departure_date), start, end) / 100) * PLOT_W;
      const y = (yPct(p.price_eur, lo, hi) / 100) * PLOT_H;
      return `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

export interface MonthTick {
  ms: number;
  label: string;
  pct: number;
}

/** One tick per month start inside the window. */
export function monthTicks(
  start: number,
  end: number,
  label: (ms: number) => string,
): MonthTick[] {
  const ticks: MonthTick[] = [];
  const first = new Date(start);
  let ms = Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 1);
  while (ms <= end) {
    ticks.push({ ms, label: label(ms), pct: xPct(ms, start, end) });
    const d = new Date(ms);
    ms = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
  }
  return ticks;
}

/** The date a pointer at `fraction` across the plot is asking about: the nearest day any
 * visible line has a fare for, not the day under the finger. Snapping to real data is what
 * makes a tap on a phone land on something. */
export function nearestIndex(fraction: number, days: number[], start: number, end: number): number {
  const target = start + fraction * (end - start);
  let best = 0;
  for (let i = 1; i < days.length; i++) {
    if (Math.abs(days[i] - target) < Math.abs(days[best] - target)) best = i;
  }
  return best;
}

export interface CardPlacement {
  /** Left edge in plot pixels, for `left: <n>px` on the plot's own coordinate box. */
  left: number;
  /** True when the card was flipped to the left of the hairline. */
  flipped: boolean;
}

/** Where the hover card goes.
 *
 * The design anchors it at `left: <pct>%` of the plot and flips it past 58 %. That is right
 * on a desktop, where the plot and the viewport are the same box, and wrong under 900px,
 * where the plot is a 760px-wide frame inside `overflow-x:auto`: a card anchored at 80 % of
 * the plot is off-screen when the reader has only scrolled to 30 %. So the flip stays and
 * the result is clamped into the *scroll container's visible* rect with 8px margins.
 *
 * Two coordinate spaces meet here and they do not share an origin. The card is positioned
 * inside `.plot`, but `.plot-frame` carries a 44px left gutter for the y-axis labels and
 * `.scroll-wrap` 2px of padding, so the plot starts ~46px into the scroll content. Every
 * argument except `plotOffset` is in plot pixels, `plotOffset` is where the plot begins in
 * the scroller, and the returned `left` is back in plot pixels — which is what the card's
 * `style.left` needs. Forgetting the offset shifts the whole visible window right by the
 * gutter, which on a 360px phone is enough to push the card back off the screen. */
export function hoverCardPlacement(args: {
  hairlinePx: number;
  plotWidth: number;
  cardWidth: number;
  scrollLeft: number;
  viewportWidth: number;
  /** The plot's left edge measured in the scroll container's content box. */
  plotOffset?: number;
  margin?: number;
  gap?: number;
}): CardPlacement {
  const { hairlinePx, plotWidth, cardWidth, scrollLeft, viewportWidth } = args;
  const plotOffset = args.plotOffset ?? 0;
  const margin = args.margin ?? 8;
  const gap = args.gap ?? 12;
  const flipped = plotWidth > 0 && hairlinePx / plotWidth > 0.58;
  const wanted = flipped ? hairlinePx - gap - cardWidth : hairlinePx + gap;
  const min = scrollLeft + margin - plotOffset;
  const max = scrollLeft + viewportWidth - margin - cardWidth - plotOffset;
  return { left: max < min ? min : Math.min(Math.max(wanted, min), max), flipped };
}

/** Bands are clipped to the window by the export, but a band that ends before the window
 * starts (or starts after it ends) would still produce a zero-width wash; drop those. */
export function bandExtent(
  from: string,
  to: string,
  start: number,
  end: number,
): { left: number; width: number } | null {
  const a = Math.max(parseDay(from), start);
  // A band's `to` is inclusive, so it covers that whole day.
  const z = Math.min(parseDay(to) + DAY_MS, end);
  if (z <= a) return null;
  return { left: xPct(a, start, end), width: xPct(z, start, end) - xPct(a, start, end) };
}

/** The axis range for a set of values, with padding.
 *
 * The design hardcodes the axes at €480–€1100 and 7h–17h. Those are its fake model's range;
 * real fares walk outside it, and a dot outside the box lands on top of the card title. So
 * both axes are computed from the data and the printed ticks come from the same numbers. */
export function domainOf(values: number[], pad: number): [number, number] {
  if (values.length === 0) return [0, 1];
  const lo = Math.min(...values) - pad;
  const hi = Math.max(...values) + pad;
  return [lo, hi === lo ? lo + pad * 2 : hi];
}
