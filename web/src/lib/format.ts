import type { Carrier } from "../types";

/** `€612`. Whole euros throughout the contract, so no decimals anywhere. */
export function euros(value: number): string {
  return `€${Math.round(value)}`;
}

/** `+€90` / `−€90`, with the typographic minus the design uses. */
export function signedEuros(value: number): string {
  return `${value < 0 ? "−" : "+"}€${Math.abs(Math.round(value))}`;
}

/** `12h 24m`, from the integer minutes the contract carries. */
export function duration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  return `${h}h ${String(minutes - h * 60).padStart(2, "0")}m`;
}

export function hours(minutes: number): number {
  return minutes / 60;
}

/** The token for a carrier's hue, with the export's own colour as the fallback.
 *
 * Both exist for a reason: `--carrier-<code>` in tokens.css is the value the design system
 * validated, and it is keyed by IATA code so hiding a chip never repaints the survivors —
 * but the stylesheet only knows the six codes S13 fixed, and the fallback keeps a seventh
 * from rendering as `currentColor` if the export ever grows one. */
export function carrierColor(carrier: Pick<Carrier, "code" | "color">): string {
  return `var(--carrier-${carrier.code}, ${carrier.color})`;
}

/** `CDG-DOH-LHE`, the hover card's routing line. */
export function routeOf(carrier: Pick<Carrier, "hub" | "direct">, destination: string): string {
  return carrier.direct || carrier.hub === null
    ? `CDG-${destination}`
    : `CDG-${carrier.hub}-${destination}`;
}
