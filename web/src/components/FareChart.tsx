import { useLayoutEffect, useRef } from "react";

import {
  bandExtent,
  hoverCardPlacement,
  monthTicks,
  nearestIndex,
  PLOT_H,
  PLOT_W,
  priceDomain,
  svgPath,
  xPct,
  yPct,
  yTickValues,
} from "../lib/chart";
import { formatMonth, parseDay } from "../lib/dates";
import { eventKey } from "../lib/events";
import { carrierColor, euros } from "../lib/format";
import type { CarrierPoints, ChartEmptyState, Filters } from "../lib/select";
import { bandWash, SEVERITY_COLOR, SEVERITY_GLYPH } from "../lib/severity";
import type { DashboardData, Event } from "../types";

import { hoverContent } from "../lib/hover";

import { HoverCard } from "./HoverCard";
import { Legend } from "./Legend";

export interface HoverState {
  index: number;
  /** True once a tap or a key pinned it. A stuck hairline is not cleared by the pointer
   * leaving the plot — only by a tap outside it, by Escape, or by another tap inside. */
  stuck: boolean;
}

export function FareChart({
  data,
  filters,
  series,
  days,
  range,
  empty,
  hover,
  onHover,
  onOpenEvent,
  plotRef,
}: {
  data: DashboardData;
  filters: Filters;
  series: CarrierPoints[];
  days: number[];
  range: { start: number; end: number };
  empty: ChartEmptyState | null;
  hover: HoverState | null;
  onHover: (next: HoverState | null) => void;
  onOpenEvent: (event: Event, opener: HTMLElement) => void;
  plotRef: React.RefObject<HTMLDivElement | null>;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  const { start, end } = range;
  const [lo, hi] = priceDomain(series.flatMap((s) => s.points.map((p) => p.price_eur)));
  const hoverDay = hover === null ? null : (days[hover.index] ?? null);

  // The card's width and the scroll container's visible box are layout, and the clamp in
  // § Phone behaviour is about that *visible* rect rather than about the 760px plot — so the
  // position can only be known after the card is in the DOM. It is written straight onto the
  // node: routing a measurement back through state would re-render the whole chart on every
  // pointer move to move one absolutely positioned box.
  useLayoutEffect(() => {
    const card = cardRef.current;
    const plot = plotRef.current;
    const scroll = scrollRef.current;
    if (!card || !plot || !scroll || hoverDay === null) return;
    const plotWidth = plot.clientWidth;
    const { left } = hoverCardPlacement({
      hairlinePx: (xPct(hoverDay, start, end) / 100) * plotWidth,
      plotWidth,
      cardWidth: card.offsetWidth,
      scrollLeft: scroll.scrollLeft,
      viewportWidth: scroll.clientWidth,
      plotOffset:
        plot.getBoundingClientRect().left - scroll.getBoundingClientRect().left + scroll.scrollLeft,
    });
    card.style.left = `${left}px`;
    card.style.visibility = "visible";
  });

  const indexAt = (clientX: number): number | null => {
    const plot = plotRef.current;
    if (!plot || days.length === 0) return null;
    const rect = plot.getBoundingClientRect();
    const fraction = rect.width === 0 ? 0 : (clientX - rect.left) / rect.width;
    return nearestIndex(Math.min(1, Math.max(0, fraction)), days, start, end);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    // Mouse only. A touch fires a synthetic mousemove after the tap with no mouseleave to
    // follow it, which is exactly how the design's pointer-only wiring strands the hairline.
    if (e.pointerType !== "mouse") return;
    const index = indexAt(e.clientX);
    if (index !== null) onHover({ index, stuck: false });
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse") return;
    const index = indexAt(e.clientX);
    if (index !== null) onHover({ index, stuck: true });
  };

  const onPointerLeave = () => {
    if (hover !== null && !hover.stuck) onHover(null);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (days.length === 0) return;
    const current = hover?.index ?? 0;
    const move = (index: number) => {
      e.preventDefault();
      onHover({ index: Math.min(days.length - 1, Math.max(0, index)), stuck: true });
    };
    if (e.key === "ArrowLeft") move(current - 1);
    else if (e.key === "ArrowRight") move(hover === null ? 0 : current + 1);
    else if (e.key === "Home") move(0);
    else if (e.key === "End") move(days.length - 1);
  };

  const content =
    hoverDay === null
      ? null
      : hoverContent({
          day: hoverDay,
          series,
          bands: data.bands,
          events: data.events,
          parse: parseDay,
        });

  return (
    <section className="card chart-card">
      <div className="chart-head">
        <h2>Lowest fare by carrier</h2>
        <span className="mono" style={{ fontSize: 12, color: "var(--color-accent)" }}>
          CDG → {filters.destination}
        </span>
        <div className="spacer" />
        <span className="chart-note">Rolling 11 months · departure date × minimum EUR fare</span>
      </div>

      <div className="scroll-wrap" ref={scrollRef}>
        <div className="plot-frame">
          <div
            className="plot"
            ref={plotRef}
            tabIndex={0}
            role="group"
            aria-label="Lowest fare by carrier over the departure window"
            onPointerMove={onPointerMove}
            onPointerDown={onPointerDown}
            onPointerLeave={onPointerLeave}
            onKeyDown={onKeyDown}
          >
            {/* Bands come from the calendar engine, not from fares, so they are drawn
                before any check on the data: an empty plot still shades its seasons. */}
            {data.bands.map((b, i) => {
              const extent = bandExtent(b.from, b.to, start, end);
              if (extent === null) return null;
              return (
                <div
                  key={`${b.tag}-${b.from}`}
                  className="band"
                  data-testid={`band-${b.tag}`}
                  style={{ left: `${extent.left}%`, width: `${extent.width}%`, ...bandWash(b.kind) }}
                >
                  <span
                    className="band-label"
                    style={{
                      top: i % 2 ? 22 : 6,
                      left: 4,
                      right: 4,
                      color: `var(--band-${b.kind})`,
                    }}
                  >
                    {b.short_label}
                  </span>
                </div>
              );
            })}

            {yTickValues(lo, hi).map((v) => (
              <div key={v} className="grid-line" style={{ top: `${yPct(v, lo, hi)}%` }} />
            ))}
            {yTickValues(lo, hi).map((v) => (
              <div key={v} className="y-tick" style={{ top: `${yPct(v, lo, hi)}%` }}>
                {euros(v)}
              </div>
            ))}

            {data.events.map((e) => (
              <div
                key={`guide-${eventKey(e)}`}
                className="pin-guide"
                style={{
                  left: `${xPct(parseDay(e.date), start, end)}%`,
                  background: `linear-gradient(to bottom, transparent, color-mix(in srgb, ${
                    SEVERITY_COLOR[e.severity]
                  } 33%, transparent))`,
                }}
              />
            ))}

            <svg viewBox={`0 0 ${PLOT_W} ${PLOT_H}`} preserveAspectRatio="none" aria-hidden="true">
              {series.map((s) => (
                <path
                  key={s.carrier.code}
                  data-testid={`series-${s.carrier.code}`}
                  d={svgPath(s.points, start, end, lo, hi)}
                  fill="none"
                  stroke={carrierColor(s.carrier)}
                  strokeWidth={1.6}
                  vectorEffect="non-scaling-stroke"
                  strokeLinejoin="round"
                />
              ))}
              {/* A carrier with one observation has no line to draw, which is the normal
                  state of a route in its first week. Dots keep it on the chart. */}
              {series.flatMap((s) =>
                s.points.map((p) => (
                  <circle
                    key={`${s.carrier.code}-${p.departure_date}`}
                    cx={(xPct(parseDay(p.departure_date), start, end) / 100) * PLOT_W}
                    cy={(yPct(p.price_eur, lo, hi) / 100) * PLOT_H}
                    r={2.5}
                    fill={carrierColor(s.carrier)}
                    vectorEffect="non-scaling-stroke"
                  />
                )),
              )}
            </svg>

            {hoverDay !== null && content !== null && (
              <>
                <div
                  className="hairline"
                  data-testid="hairline"
                  style={{ left: `${xPct(hoverDay, start, end)}%` }}
                />
                <HoverCard content={content} cardRef={cardRef} />
              </>
            )}

            {data.events.map((e) => (
              <button
                key={eventKey(e)}
                type="button"
                className="pin hit44"
                aria-label={`${e.severity} severity event: ${e.headline}`}
                style={{
                  left: `${xPct(parseDay(e.date), start, end)}%`,
                  color: SEVERITY_COLOR[e.severity],
                  border: `1px solid ${SEVERITY_COLOR[e.severity]}`,
                  boxShadow: `0 0 10px color-mix(in srgb, ${SEVERITY_COLOR[e.severity]} 40%, transparent)`,
                }}
                onClick={(ev) => onOpenEvent(e, ev.currentTarget)}
              >
                <span aria-hidden="true">{SEVERITY_GLYPH[e.severity]}</span>
              </button>
            ))}

            {empty !== null && (
              <div className="chart-empty">
                <p className="empty-line" data-testid={`chart-empty-${empty.kind}`}>
                  {empty.message}
                </p>
              </div>
            )}
          </div>

          <div className="x-axis">
            {monthTicks(start, end, formatMonth).map((t) => (
              <div
                key={t.ms}
                className="x-tick"
                style={
                  t.pct > 90
                    ? { right: `${100 - t.pct}%` }
                    : { left: `${t.pct}%`, transform: "translateX(-50%)" }
                }
              >
                {t.label}
              </div>
            ))}
          </div>
        </div>
      </div>

      <Legend carriers={data.carriers} hidden={filters.hiddenCarriers} />
    </section>
  );
}
