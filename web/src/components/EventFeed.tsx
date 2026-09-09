import { formatDay, parseDay } from "../lib/dates";
import { eventKey } from "../lib/events";
import { SEVERITY_COLOR } from "../lib/severity";
import type { NewsFreshness } from "../lib/staleness";
import type { Event } from "../types";

/** The feed line is `date · source · severity`.
 *
 * The design printed an impact score there, derived from severity (high 0.91 / med 0.64 /
 * low 0.22). The export deliberately does not carry it — it would be a second copy of a
 * field already in the file — so the row names the severity it was derived from instead of
 * inventing a number that looks measured.
 *
 * `news` is the one thing the rows cannot say for themselves. An empty feed is a quiet week
 * and a dead news step alike, so when the run log says the queries stopped landing the list
 * gets a muted line above it — above rather than instead of, because the rows below it are
 * still real, just old. */
export function EventFeed({
  events,
  emptyLine,
  news,
  onOpen,
}: {
  events: Event[];
  emptyLine: string;
  news: NewsFreshness;
  onOpen: (event: Event, opener: HTMLElement) => void;
}) {
  return (
    <aside className="card feed-aside">
      <div className="feed-head">
        <h2>Event feed</h2>
        <div style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 10.5, color: "var(--text-muted-45)" }}>
          GDELT
        </span>
      </div>
      {news.message !== null && (
        <p className="feed-stale" role="status" data-testid="feed-stale">
          {news.message}
          {news.detail !== null && <span className="feed-stale-detail">{news.detail}</span>}
        </p>
      )}
      {events.length === 0 ? (
        <p className="empty-line" data-testid="feed-empty">
          {emptyLine}
        </p>
      ) : (
        <div className="feed-list">
          {events.map((e) => (
            <button
              key={eventKey(e)}
              type="button"
              className="feed-row"
              onClick={(ev) => onOpen(e, ev.currentTarget)}
            >
              <span
                className="sev-dot"
                style={{
                  background: SEVERITY_COLOR[e.severity],
                  boxShadow:
                    e.severity === "low" ? "none" : `0 0 8px ${SEVERITY_COLOR[e.severity]}`,
                }}
              />
              <span style={{ minWidth: 0 }}>
                <span className="feed-headline">{e.headline}</span>
                <span className="feed-meta">
                  {formatDay(parseDay(e.date))} · {e.source} · {e.severity}
                </span>
                {e.impact_text !== null && <span className="feed-impact">{e.impact_text}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}
