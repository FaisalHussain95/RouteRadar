import { formatDay } from "../lib/dates";
import { eventKey } from "../lib/events";
import { euros } from "../lib/format";
import type { HoverContent } from "../lib/hover";
import { BAND_COLOR } from "../lib/severity";

export function HoverCard({
  content,
  cardRef,
}: {
  content: HoverContent;
  cardRef: React.Ref<HTMLDivElement>;
}) {
  return (
    <div
      className="hover-card"
      ref={cardRef}
      data-testid="hover-card"
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 7 }}>
        <span className="mono" style={{ fontSize: 12.5 }}>
          {formatDay(content.day)}
        </span>
        {content.bands.map((b) => (
          <span
            key={b.tag}
            style={{
              padding: "2px 7px",
              borderRadius: "var(--radius-sm)",
              fontSize: 10,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color: BAND_COLOR[b.kind],
              background: `color-mix(in srgb, ${BAND_COLOR[b.kind]} 12%, transparent)`,
            }}
          >
            {b.short_label}
          </span>
        ))}
      </div>
      {content.rows.map((r, i) => (
        <div key={r.code} className={i === 0 ? "hover-row cheapest" : "hover-row"}>
          <span className="swatch" style={{ background: r.color }} />
          <span style={{ fontSize: 12 }}>
            {r.name}{" "}
            <span className="mono" style={{ fontSize: 10.5, color: "rgba(233,233,237,0.5)" }}>
              {r.route} · {r.dur}
            </span>
          </span>
          <span className="mono" style={{ fontSize: 13, fontWeight: 500 }}>
            {euros(r.fare)}
          </span>
        </div>
      ))}
      {content.events.map((e) => (
        <div key={eventKey(e)} className="hover-news">
          <span style={{ color: "var(--sev-high)" }} aria-hidden="true">
            ◆
          </span>{" "}
          {e.headline}
          <span className="mono" style={{ color: "rgba(233,233,237,0.5)" }}>
            {" "}
            {e.severity}
          </span>
        </div>
      ))}
    </div>
  );
}
