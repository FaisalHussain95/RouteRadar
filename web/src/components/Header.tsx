import { clockOf, formatDay, parseDay } from "../lib/dates";
import type { Staleness } from "../lib/staleness";
import type { DashboardData } from "../types";

/** Sticky header, plus the stale-data banner that replaces its "updated" claim.
 *
 * The banner is under the header rather than inside it because it is a statement about the
 * whole page, not about one status line, and because the header wraps on a phone — a
 * warning that wraps into the second row of a filter bar is a warning nobody reads. */
export function Header({ data, freshness }: { data: DashboardData; freshness: Staleness }) {
  return (
    <>
      <header className="header">
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
          <div className="brand-mark" aria-hidden="true">
            <span />
          </div>
          <div className="brand-name">RouteRadar</div>
          <div className="route-tag">CDG → PK-NORTH</div>
        </div>
        <div style={{ flex: 1 }} />
        <div className="constraint-pill">
          <span className="glyph" aria-hidden="true">
            ◈
          </span>{" "}
          Max 1 Stop <span style={{ opacity: 0.35 }}>|</span> Layover ≤ 7h
        </div>
        <div className="status">
          <span
            className={freshness.stale ? "status-dot stale" : "status-dot"}
            data-testid="status-dot"
          />
          {freshness.stale ? (
            <>Ingest stalled</>
          ) : (
            <>
              Updated {formatDay(parseDay(data.generated_at))}{" "}
              <span className="mono" style={{ color: "rgba(233,233,237,0.8)" }}>
                {clockOf(data.generated_at)} CET
              </span>{" "}
              · Daily cron ingest
            </>
          )}
        </div>
      </header>
      {freshness.message !== null && (
        <div className="stale-banner" role="status">
          <span aria-hidden="true">▲</span>
          {freshness.message}
        </div>
      )}
    </>
  );
}
