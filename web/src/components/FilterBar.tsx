import { useCallback, useEffect, useRef, useState } from "react";

import { hasOverflow } from "../lib/dom";
import { carrierColor } from "../lib/format";
import { destinationOptions, type Filters } from "../lib/select";
import type { DashboardData } from "../types";

export function FilterBar({
  data,
  filters,
  onChange,
}: {
  data: DashboardData;
  filters: Filters;
  onChange: (next: Filters) => void;
}) {
  const stripRef = useRef<HTMLDivElement>(null);
  const [overflowing, setOverflowing] = useState(false);

  const measure = useCallback(() => {
    const el = stripRef.current;
    if (el) setOverflowing(hasOverflow(el));
  }, []);

  useEffect(() => {
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    if (stripRef.current) ro.observe(stripRef.current);
    return () => {
      ro.disconnect();
    };
  }, [measure]);

  const toggleCarrier = (code: string) => {
    const hidden = new Set(filters.hiddenCarriers);
    if (hidden.has(code)) hidden.delete(code);
    else hidden.add(code);
    onChange({ ...filters, hiddenCarriers: hidden });
  };

  return (
    <div className="filter-bar">
      <div className="filter-group origin">
        <div className="filter-label">Origin</div>
        <div className="origin-box">
          CDG <span style={{ opacity: 0.4 }}>/</span> ORY{" "}
          <span className="note">Paris · fixed</span>
        </div>
      </div>

      <div className="filter-group">
        <div className="filter-label" id="destination-label">
          Destination
        </div>
        <div className="segmented" role="group" aria-labelledby="destination-label">
          {destinationOptions(data).map((d) => (
            <button
              key={d.code}
              type="button"
              aria-pressed={filters.destination === d.code}
              onClick={() => onChange({ ...filters, destination: d.code })}
            >
              {d.code === "ALL" ? d.label : d.code}
            </button>
          ))}
        </div>
      </div>

      <div className="filter-group" style={{ flex: "1 1 220px" }}>
        <div className="filter-label" id="carriers-label">
          Carriers
        </div>
        <div
          className={overflowing ? "chip-strip has-overflow" : "chip-strip"}
          ref={stripRef}
          onScroll={measure}
          role="group"
          aria-labelledby="carriers-label"
        >
          {data.carriers.map((c) => {
            const on = !filters.hiddenCarriers.has(c.code);
            return (
              <button
                key={c.code}
                type="button"
                className="chip hit44"
                aria-pressed={on}
                onClick={() => toggleCarrier(c.code)}
              >
                <span
                  className="dot"
                  style={
                    on
                      ? { background: carrierColor(c), boxShadow: `0 0 8px ${carrierColor(c)}` }
                      : undefined
                  }
                />
                {c.name} <span className="mono">{c.code}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="filter-group">
        <div className="filter-label" id="horizon-label">
          Booking horizon
        </div>
        <div className="segmented" role="group" aria-labelledby="horizon-label">
          {data.horizons.map((h) => (
            <button
              key={h}
              type="button"
              aria-pressed={filters.horizon === h}
              onClick={() => onChange({ ...filters, horizon: h })}
            >
              {h}d
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
