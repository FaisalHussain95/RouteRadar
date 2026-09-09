import { carrierColor } from "../lib/format";
import { BAND_COLOR } from "../lib/severity";
import type { Band, Carrier } from "../types";

const BAND_KEYS: { kind: Band["kind"]; label: string }[] = [
  { kind: "wedding", label: "Wedding season" },
  { kind: "religious", label: "Ramadan / Eid / Hajj" },
  { kind: "french", label: "French holidays" },
];

/** The row under the plot.
 *
 * The design keys the three bands and the news pin and names no carrier at all, which
 * leaves the filter bar's chips as the only colour→carrier key on the page — and on a phone
 * they are scrolled out of view by the time the reader is looking at the plot. S13 made the
 * carrier keys part of this row; they come first because they key the data, and the bands
 * and pins key the annotations. A hidden carrier stays listed, greyed, so the row does not
 * reflow as chips are toggled. */
export function Legend({ carriers, hidden }: { carriers: Carrier[]; hidden: ReadonlySet<string> }) {
  return (
    <div className="legend">
      {carriers.map((c) => {
        const off = hidden.has(c.code);
        return (
          <span
            key={c.code}
            className="key"
            style={{ opacity: off ? 0.4 : 1 }}
            data-testid={`legend-carrier-${c.code}`}
          >
            <span
              className="line"
              style={{ color: off ? "var(--text-muted-40)" : carrierColor(c) }}
            />
            <span className="mono">{c.code}</span>
          </span>
        );
      })}
      {BAND_KEYS.map((b) => (
        <span key={b.kind} className="key">
          <span
            className="band-key"
            style={{
              background: `color-mix(in srgb, ${BAND_COLOR[b.kind]} 28%, transparent)`,
              borderLeft: `1px solid ${BAND_COLOR[b.kind]}`,
              borderRight: `1px solid ${BAND_COLOR[b.kind]}`,
            }}
          />
          {b.label}
        </span>
      ))}
      <span className="key">
        <span className="pin-key" />
        News event — click a pin
      </span>
    </div>
  );
}
