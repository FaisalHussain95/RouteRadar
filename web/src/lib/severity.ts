import type { Band, Event } from "../types";

/** Severity is carried by three channels, not by colour alone: the pin's glyph, the feed
 * dot and the drawer badge. That is what lets the carrier palette sit at ΔE ≥ 10 from the
 * severity colours rather than ≥ 15 (design-system.md § The rule, by rendering channel). */
export const SEVERITY_COLOR: Record<Event["severity"], string> = {
  high: "var(--sev-high)",
  med: "var(--sev-med)",
  low: "var(--sev-low)",
};

export const SEVERITY_GLYPH: Record<Event["severity"], string> = {
  high: "!",
  med: "▲",
  low: "•",
};

export const BAND_COLOR: Record<Band["kind"], string> = {
  wedding: "var(--band-wedding)",
  religious: "var(--band-religious)",
  french: "var(--band-french)",
};

/** The design's `color22 → color0d` wash with `color40` edges, as percentages a token can
 * be mixed at: a band is 13 % ink at the top and 5 % at the bottom, which is why a carrier
 * hue only has to clear ΔE 10 against it. */
export function bandWash(kind: Band["kind"]): {
  background: string;
  borderLeft: string;
  borderRight: string;
} {
  const c = BAND_COLOR[kind];
  const edge = `1px solid color-mix(in srgb, ${c} 25%, transparent)`;
  return {
    background:
      `linear-gradient(to bottom, color-mix(in srgb, ${c} 13%, transparent), ` +
      `color-mix(in srgb, ${c} 5%, transparent))`,
    borderLeft: edge,
    borderRight: edge,
  };
}
