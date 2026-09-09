import { useEffect, useRef } from "react";

import { formatDay, parseDay } from "../lib/dates";
import { SEVERITY_COLOR } from "../lib/severity";
import type { Event } from "../types";

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The right-side panel. The design draws it; what it does not have is modal behaviour, and
 * without that a phone reader scrolls the page behind it and a keyboard reader tabs out of
 * it into a page they cannot see. So: body scroll locked, focus moved to the heading and
 * trapped, Escape and a backdrop tap to close, and focus returned to whatever opened it —
 * the pin or the feed row — so the reader is put back where they were. */
export function EventDrawer({
  event,
  opener,
  onClose,
  onBackdropPointerDown,
}: {
  event: Event;
  opener: HTMLElement | null;
  onClose: () => void;
  onBackdropPointerDown: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
      opener?.focus();
    };
  }, [opener]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const items = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === headingRef.current)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [onClose]);

  return (
    <>
      <button
        type="button"
        className="backdrop"
        aria-label="Close event details"
        tabIndex={-1}
        onPointerDown={onBackdropPointerDown}
        onClick={onClose}
      />
      <div
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-heading"
        ref={panelRef}
      >
        <div className="drawer-head">
          <span
            className="sev-badge"
            style={{
              color: SEVERITY_COLOR[event.severity],
              background: `color-mix(in srgb, ${SEVERITY_COLOR[event.severity]} 14%, transparent)`,
            }}
          >
            {event.severity.toUpperCase()} SEVERITY
          </span>
          <div style={{ flex: 1 }} />
          <button type="button" className="drawer-close hit44" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="mono" style={{ fontSize: 12, color: "rgba(233,233,237,0.5)" }}>
          {formatDay(parseDay(event.date))}
        </div>
        <h2 id="drawer-heading" tabIndex={-1} ref={headingRef}>
          {event.headline}
        </h2>
        {/* GDELT's DOC 2.0 artlist carries titles and URLs, never article text, so `body` is
            null in v1 and the drawer says so rather than showing an empty paragraph. */}
        <p style={{ fontSize: 13.5, lineHeight: 1.6, color: "var(--text-muted-72)" }}>
          {event.body ?? "No article text — GDELT indexes headlines and links, not bodies."}
        </p>
        <dl className="drawer-grid">
          <dt>Source</dt>
          <dd className="mono">
            <a href={event.source_url} target="_blank" rel="noreferrer">
              {event.source}
            </a>
          </dd>
          {/* Not "Est. fare impact", which is what the design labels it: `impact_text` is
              the ingest's note on the row — how many outlets carried it and which keyword
              set the severity — and v1 estimates no fare impact at all. */}
          <dt>Ingest note</dt>
          <dd className="mono" style={{ color: "var(--color-accent-300)" }}>
            {event.impact_text ?? "—"}
          </dd>
          <dt>Scope</dt>
          <dd>Carrier set under Max 1 Stop · Layover ≤ 7h</dd>
        </dl>
      </div>
    </>
  );
}
